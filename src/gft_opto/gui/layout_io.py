"""Versioned, atomic layout persistence, independent of the main window."""

import json
import math
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtWidgets import QGraphicsScene

from gft_opto.gui.symbols import (
    BUILTIN_SYMBOLS, BusItem, ConnectionItem, JunctionItem, OneLineSymbolItem,
    _instance_counters, make_user_equipment,
)


def scene_data(scene):
    components, wires = [], []
    for item in scene.items():
        if isinstance(item, OneLineSymbolItem):
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
        raise ValueError("Layout contains an invalid number.")
    return value


def validate_properties(props):
    if not isinstance(props, dict):
        raise ValueError("Component properties must be an object.")
    for key in ("trip_coil_1_a", "trip_coil_2_a", "close_coil_a", "motor_inrush_a", "motor_run_a", "rating_kv"):
        if key in props:
            value = _number(props[key])
            if value < 0 or (key == "rating_kv" and value == 0):
                raise ValueError(f"Invalid value for {key}.")


def build_scene(data):
    """Validate and construct a separate scene before touching the user's drawing."""
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unsupported layout format or version.")
    if not isinstance(data.get("project"), str) or not data["project"].strip():
        raise ValueError("A project name is required.")
    rect = data["scene_rect"]
    if len(rect) != 4 or any(not math.isfinite(_number(v)) for v in rect) or rect[2] <= 0 or rect[3] <= 0:
        raise ValueError("Invalid drawing dimensions.")
    settings = data["settings"]
    if not 0 <= _number(settings["grid_opacity"]) <= 100:
        raise ValueError("Invalid grid opacity.")
    for key in ("grid_visible", "snap", "pdf_visible"):
        if not isinstance(settings[key], bool):
            raise ValueError(f"Invalid setting: {key}")
    if data.get("pdf") is not None and not isinstance(data["pdf"], str):
        raise ValueError("Invalid PDF reference.")
    library = data["library"]
    if not isinstance(library, dict):
        raise ValueError("Invalid equipment library.")
    for key, entry in library.items():
        if not isinstance(key, str) or not isinstance(entry["name"], str) or not entry["name"].strip():
            raise ValueError("Invalid library entry.")
        if entry["symbol"] not in (*BUILTIN_SYMBOLS, "custom"):
            raise ValueError("Unknown library symbol.")
        validate_properties(entry["properties"])
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
                raise ValueError("Missing or duplicate component ID.")
            key = record["symbol"]
            if key not in (*BUILTIN_SYMBOLS, "junction", "custom") and not key.startswith("custom_"):
                raise ValueError(f"Unknown symbol: {key}")
            if not isinstance(record["name"], str) or not isinstance(record["display_id"], str):
                raise ValueError("Invalid component name or displayed ID.")
            props = record["properties"]
            validate_properties(props)
            item = JunctionItem() if key == "junction" else make_user_equipment(key, record["name"], props)
            scene.addItem(item)
            item.instance_id = ident
            item.display_id = record["display_id"]
            ports = record["ports"]
            if not isinstance(ports, dict) or not ports:
                raise ValueError("Component has no ports.")
            if any(not isinstance(name, str) or len(point) != 2 for name, point in ports.items()):
                raise ValueError("Invalid component ports.")
            restored_ports = {name: QPointF(*[_number(v) for v in point]) for name, point in ports.items()}
            if isinstance(item, BusItem):
                if not {"left", "right"} <= ports.keys():
                    raise ValueError("Bus endpoints are missing.")
                left, right = restored_ports["left"].x(), restored_ports["right"].x()
                if right - left < 40 or any(p.y() != 0 or not left <= p.x() <= right for p in restored_ports.values()):
                    raise ValueError("Invalid bus geometry.")
                item._base_ports = restored_ports
                item._sync_bus_rect()
            elif set(ports) != set(item.ports()):
                raise ValueError("Unexpected component terminals.")
            scale = _number(record["scale"])
            if scale < 0.5 or (item.max_scale is not None and scale > item.max_scale):
                raise ValueError("Invalid component scale.")
            item.set_scale_factor(scale)
            item.set_rotation_deg(_number(record["rotation"]))
            position = record["position"]
            if len(position) != 2:
                raise ValueError("Invalid component position.")
            item.setPos(*[_number(v) for v in position])
            item.setZValue(100)
            items[ident] = item
        for a, ap, b, bp in data["connections"]:
            if a not in items or b not in items or ap not in items[a].ports() or bp not in items[b].ports():
                raise ValueError("Wire references an unknown component or terminal.")
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


def write_layout(path, data):
    """Replace a saved layout only after a complete, valid JSON write."""
    target = Path(path)
    text = json.dumps(data, indent=2, allow_nan=False)
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
