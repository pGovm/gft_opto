
"""Build and export netlist data from the current one-line diagram."""

import json
from pathlib import Path

from gft_opto.gui.symbols import ConnectionItem, OneLineSymbolItem


def _json_safe(value):
    """Convert common property values into JSON-compatible data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]

    # TODO: Replace this fallback if the project later stores
    # special objects that need a more precise representation.
    return str(value)


def build_netlist(scene, project_name="Untitled Project"):
    """Build a netlist from the current QGraphicsScene."""

    if scene is None:
        raise ValueError("No diagram scene is available.")

    scene_items = scene.items()

    # Gather electrical symbols and wires, excluding the grid and PDF.
    components = [
        item for item in scene_items
        if isinstance(item, OneLineSymbolItem)
    ]

    wires = [
        item for item in scene_items
        if isinstance(item, ConnectionItem)
    ]

    # Use internal IDs for references; display labels can be edited.
    component_ids = {
        item: f"{item.equip_type}:{item.instance_id}"
        for item in components
    }

    netlist_components = []

    for item in components:
        position = item.pos()
        properties = getattr(item, "properties", {})

        netlist_components.append({
            "id": component_ids[item],
            "display_id": item.visible_id(),
            "type": item.equip_type,
            "name": getattr(item, "display_name", item.label),
            "properties": _json_safe(properties),
            "position": {
                "x": position.x(),
                "y": position.y(),
            },
            "rotation_deg": item.rotation_deg,
            "scale_factor": item.scale_factor,
            "ports": list(item.ports().keys()),
        })

    netlist_connections = []

    for index, wire in enumerate(wires, start=1):
        # Every wire endpoint should reference a component in the scene.
        if (
            wire.from_item not in component_ids
            or wire.to_item not in component_ids
        ):
            # TODO: Collect this as a validation warning instead
            # of silently skipping it in a production version.
            continue

        netlist_connections.append({
            "id": f"connection_{index}",
            "from": {
                "component": component_ids[wire.from_item],
                "port": wire.from_port,
            },
            "to": {
                "component": component_ids[wire.to_item],
                "port": wire.to_port,
            },
        })

    return {
        "schema_version": 1,
        "project_name": project_name,
        "components": netlist_components,
        "connections": netlist_connections,
    }


def export_netlist_json(netlist, file_path):
    """Write netlist data to a JSON file."""

    path = Path(file_path)

    with path.open("w", encoding="utf-8") as file:
        json.dump(netlist, file, indent=2, ensure_ascii=False)