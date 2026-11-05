# Validates, saves, and rebuilds editable JSON layouts and derives equipment-library entries from drawings.

"""Versioned, atomic layout persistence, independent of the main window."""

from gft_opto.gui import messages

import json
import math
import os
import tempfile
import copy
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtWidgets import QGraphicsScene

from gft_opto.gui.symbols import (
    BUILTIN_SYMBOLS, BusItem, ConnectionItem, JunctionItem, OneLineSymbolItem,
    _instance_counters, make_user_equipment,
)
from gft_opto.logic.protection_study import validate_zones
from gft_opto.gui.routing import default_snap_port, position_for_snapped_port


def library_from_layout(data):
    """Retain saved templates and expose every drawn equipment definition."""
    library = copy.deepcopy(data.get("library", {}))

    def signature(definition):
        return json.dumps(definition, sort_keys=True, allow_nan=False)

    seen = {signature(definition) for definition in library.values()}
    for component in data.get("components", []):
        symbol = component["symbol"]
        # Junctions are wire-editing helpers, not equipment templates.
        if symbol == "junction":
            continue
        definition = {"symbol": symbol if symbol in BUILTIN_SYMBOLS else "custom",
            "name": component.get("display_id") or component["name"],
            "properties": copy.deepcopy(component["properties"])}
        key_signature = signature(definition)
        if key_signature in seen:
            continue
        base = f"layout_{component['id']}"
        key, suffix = base, 2
        while key in library:
            key = f"{base}_{suffix}"
            suffix += 1
        library[key] = definition
        seen.add(key_signature)
    return library


def scene_data(scene):
    components, wires = [], []
    for item in scene.items():
        if isinstance(item, OneLineSymbolItem):
            symbol = "junction" if isinstance(item, JunctionItem) else item.equip_type
            validate_properties(getattr(item, "properties", {}), symbol=symbol)
            components.append({
                "id": item.instance_id, "symbol": "junction" if isinstance(item, JunctionItem)
                else getattr(item, "properties", {}).get("symbol_type", item.equip_type),
                "name": item.display_name, "display_id": item.display_id,
                "properties": dict(getattr(item, "properties", {})),
                "position": [item.x(), item.y()], "scale": item.scale_factor,
                "rotation": item.rotation_deg,
                "ports": {key: [p.x(), p.y()] for key, p in item._base_ports.items()},
            })
        elif isinstance(item, ConnectionItem):
            wires.append([item.from_item.instance_id, item.from_port,
                          item.to_item.instance_id, item.to_port])
    return {"components": sorted(components, key=lambda c: c["id"]),
            "connections": sorted(wires)}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(messages.LAYOUT_IO_LAYOUT_CONTAINS_AN_INVALID_NUMBER)
    return value


def validate_properties(props, *, symbol=None):
    if not isinstance(props, dict):
        raise ValueError(messages.LAYOUT_IO_COMPONENT_PROPERTIES_MUST_BE_AN_OBJECT)
    # Do not reload a breaker glyph as a CT because conflicting metadata wins
    # over its real symbol type in the netlist study adapter.
    if symbol is not None and "symbol_type" in props:
        declared = props["symbol_type"]
        allowed = {symbol}
        if symbol == "custom" or symbol.startswith("custom_"):
            allowed.add("custom")
        if not isinstance(declared, str) or declared not in allowed:
            raise ValueError(messages.LAYOUT_IO_SYMBOL_CONFLICTS_WITH_PROPERTIES_SYMBOL_TYPE.format(symbol, declared))
    for key, choices in (("coil_current_basis", ("whole_breaker", "per_mechanism")),
                         ("mechanism_arrangement", ("shared", "independent"))):
        if key in props and props[key] not in choices:
            raise ValueError(messages.LAYOUT_IO_INVALID_BREAKER_SETTING.format(key))
    if "pole_count" in props and (isinstance(props["pole_count"], bool) or not isinstance(props["pole_count"], int) or not 1 <= props["pole_count"] <= 3):
        raise ValueError(messages.LAYOUT_IO_BREAKER_POLE_COUNT_MUST_BE_BETWEEN_1_AND_3)
    for key in ("trip_coil_1_supply", "trip_coil_2_supply", "close_coil_supply", "motor_supply"):
        if key in props and (not isinstance(props[key], str) or not props[key].strip()):
            raise ValueError(messages.LAYOUT_IO_A_DC_SUPPLY_IS_REQUIRED_FOR.format(key))
    for key in ("trip_coil_1_a", "trip_coil_2_a", "close_coil_a", "motor_inrush_a", "motor_run_a", "rating_kv"):
        if key in props:
            value = _number(props[key])
            if value < 0 or (key == "rating_kv" and value == 0):
                raise ValueError(messages.LAYOUT_IO_INVALID_VALUE_FOR.format(key))


def build_scene(data, *, align_to_grid=False):
    """Validate and construct a separate scene before touching the user's drawing."""
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError(messages.LAYOUT_IO_UNSUPPORTED_LAYOUT_FORMAT_OR_VERSION)
    validate_zones(data.get("zones", []))
    if not isinstance(data.get("project"), str) or not data["project"].strip():
        raise ValueError(messages.LAYOUT_IO_A_PROJECT_NAME_IS_REQUIRED)
    rect = data["scene_rect"]
    if len(rect) != 4 or any(not math.isfinite(_number(v)) for v in rect) or rect[2] <= 0 or rect[3] <= 0:
        raise ValueError(messages.LAYOUT_IO_INVALID_DRAWING_DIMENSIONS)
    settings = data["settings"]
    if not 0 <= _number(settings["grid_opacity"]) <= 100:
        raise ValueError(messages.LAYOUT_IO_INVALID_GRID_OPACITY)
    for key in ("grid_visible", "snap", "pdf_visible"):
        if not isinstance(settings[key], bool):
            raise ValueError(messages.LAYOUT_IO_INVALID_SETTING.format(key))
    if data.get("pdf") is not None and not isinstance(data["pdf"], str):
        raise ValueError(messages.LAYOUT_IO_INVALID_PDF_REFERENCE)
    library = data["library"]
    if not isinstance(library, dict):
        raise ValueError(messages.LAYOUT_IO_INVALID_EQUIPMENT_LIBRARY)
    for key, entry in library.items():
        if not isinstance(key, str) or not isinstance(entry["name"], str) or not entry["name"].strip():
            raise ValueError(messages.LAYOUT_IO_INVALID_LIBRARY_ENTRY)
        if entry["symbol"] not in (*BUILTIN_SYMBOLS, "custom"):
            raise ValueError(messages.LAYOUT_IO_UNKNOWN_LIBRARY_SYMBOL)
        validate_properties(entry["properties"], symbol=entry["symbol"])
    scene = QGraphicsScene()
    scene.setSceneRect(QRectF(*rect))
    items = {}
    counters = dict(_instance_counters)
    snap = OneLineSymbolItem.snap_to_grid_enabled
    OneLineSymbolItem.snap_to_grid_enabled = False
    try:
        for record in data["components"]:
            ident = record["id"]
            if not isinstance(ident, str) or not ident or ident in items:
                raise ValueError(messages.LAYOUT_IO_MISSING_OR_DUPLICATE_COMPONENT_ID)
            key = record["symbol"]
            if key not in (*BUILTIN_SYMBOLS, "junction", "custom") and not key.startswith("custom_"):
                raise ValueError(messages.LAYOUT_IO_UNKNOWN_SYMBOL.format(key))
            if not isinstance(record["name"], str) or not isinstance(record["display_id"], str):
                raise ValueError(messages.LAYOUT_IO_INVALID_COMPONENT_NAME_OR_DISPLAYED_ID)
            props = record["properties"]
            validate_properties(props, symbol=key)
            item = JunctionItem() if key == "junction" else make_user_equipment(key, record["name"], props)
            scene.addItem(item)
            item.instance_id = ident
            item.display_id = record["display_id"]
            ports = record["ports"]
            if not isinstance(ports, dict) or not ports:
                raise ValueError(messages.LAYOUT_IO_COMPONENT_HAS_NO_PORTS)
            if any(not isinstance(name, str) or len(point) != 2 for name, point in ports.items()):
                raise ValueError(messages.LAYOUT_IO_INVALID_COMPONENT_PORTS)
            restored_ports = {name: QPointF(*[_number(v) for v in point]) for name, point in ports.items()}
            if isinstance(item, BusItem):
                if not {"left", "right"} <= ports.keys():
                    raise ValueError(messages.LAYOUT_IO_BUS_ENDPOINTS_ARE_MISSING)
                left, right = restored_ports["left"].x(), restored_ports["right"].x()
                if right - left < 40 or any(p.y() != 0 or not left <= p.x() <= right for p in restored_ports.values()):
                    raise ValueError(messages.LAYOUT_IO_INVALID_BUS_GEOMETRY)
                item._base_ports = restored_ports
                item._sync_bus_rect()
            elif set(ports) != set(item.ports()):
                raise ValueError(messages.LAYOUT_IO_UNEXPECTED_COMPONENT_TERMINALS)
            scale = _number(record["scale"])
            if scale < 0.5 or (item.max_scale is not None and scale > item.max_scale):
                raise ValueError(messages.LAYOUT_IO_INVALID_COMPONENT_SCALE)
            item.set_scale_factor(scale)
            item.set_rotation_deg(_number(record["rotation"]))
            position = record["position"]
            if len(position) != 2:
                raise ValueError(messages.LAYOUT_IO_INVALID_COMPONENT_POSITION)
            restored_position = QPointF(*[_number(v) for v in position])
            if align_to_grid and settings["snap"]:
                anchor = default_snap_port(item)
                restored_position = position_for_snapped_port(restored_position, item.ports()[anchor])
            item.setPos(restored_position)
            item.setZValue(100)
            items[ident] = item
        for a, ap, b, bp in data["connections"]:
            if a not in items or b not in items or ap not in items[a].ports() or bp not in items[b].ports():
                raise ValueError(messages.LAYOUT_IO_WIRE_REFERENCES_AN_UNKNOWN_COMPONENT_OR_TERMINAL)
            scene.addItem(ConnectionItem(items[a], ap, items[b], bp))
    finally:
        OneLineSymbolItem.snap_to_grid_enabled = snap
        _instance_counters.clear()
        _instance_counters.update(counters)
    return scene


def reserve_ids(scene):
    for item in scene.items():
        if isinstance(item, OneLineSymbolItem):
            prefix, separator, number = item.instance_id.rpartition("_")
            if separator and number.isdigit():
                _instance_counters[prefix] = max(_instance_counters[prefix], int(number))


def compact_layout_text(data):
    """Omit source-image annotations and redundant generated library entries."""
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()
                    if not key.startswith("reference_") and key != "display_polygon"}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    saved = clean(data)
    generated = library_from_layout(dict(saved, library={}))
    signatures = {json.dumps(entry, sort_keys=True) for entry in generated.values()}
    saved["library"] = {key: entry for key, entry in saved.get("library", {}).items()
                        if json.dumps(entry, sort_keys=True) not in signatures}
    encode = lambda value: json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    fields = []
    for key, value in saved.items():
        if isinstance(value, list) and value and isinstance(value[0], (dict, list)):
            rendered = "[\n" + ",\n".join("    " + encode(item) for item in value) + "\n  ]"
        else:
            rendered = encode(value)
        fields.append("  " + encode(key) + ": " + rendered)
    return "{\n" + ",\n".join(fields) + "\n}\n"


def write_layout(path, data):
    """Replace a saved layout only after a complete, valid JSON write."""
    target = Path(path)
    text = compact_layout_text(data)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=f".{target.name}.", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
