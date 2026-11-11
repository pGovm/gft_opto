
"""Build and export netlist data from the current one-line diagram."""

import json
import math
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


    return str(value)

# Netlist save functionality
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
            "port_positions": {
                name: {"x": point.x(), "y": point.y()}
                for name, point in item.base_port_positions().items()
            },
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

# exporting netlist file JSON
def export_netlist_json(netlist, file_path):
    """Write netlist data to a JSON file."""

    path = Path(file_path)

    with path.open("w", encoding="utf-8") as file:
        json.dump(netlist, file, indent=2, ensure_ascii=False)

# import netlist function from JSON
def load_netlist_json(file_path):
    """Load and validate a netlist exported by this application."""
    with Path(file_path).open("r", encoding="utf-8") as file:
        netlist = json.load(file)

    if not isinstance(netlist, dict):
        raise ValueError("The netlist root must be a JSON object.")
    if netlist.get("schema_version") != 1:
        raise ValueError(
            f"Unsupported netlist schema version: "
            f"{netlist.get('schema_version', 'missing')}."
        )
    project_name = netlist.get("project_name", "Untitled Project")
    if not isinstance(project_name, str) or not project_name.strip():
        raise ValueError("The netlist project_name must be a non-empty string.")

    components = netlist.get("components")
    connections = netlist.get("connections")
    if not isinstance(components, list) or not isinstance(connections, list):
        raise ValueError("The netlist must contain components and connections lists.")

    component_ports = {}
    for index, component in enumerate(components):
        location = f"Component {index + 1}"
        if not isinstance(component, dict):
            raise ValueError(f"{location} must be an object.")

        component_id = component.get("id")
        component_type = component.get("type")
        if not isinstance(component_id, str) or not component_id:
            raise ValueError(f"{location} has no valid id.")
        if component_id in component_ports:
            raise ValueError(f"Duplicate component id: {component_id}.")
        if not isinstance(component_type, str) or not component_type:
            raise ValueError(f"{location} has no valid type.")

        properties = component.get("properties", {})
        ports = component.get("ports")
        position = component.get("position")
        if not isinstance(properties, dict):
            raise ValueError(f"{location} properties must be an object.")
        if (
            not isinstance(ports, list)
            or any(not isinstance(port, str) or not port for port in ports)
            or len(ports) != len(set(ports))
        ):
            raise ValueError(f"{location} ports must be a list of unique names.")
        component_ports[component_id] = set(ports)
        if not isinstance(position, dict):
            raise ValueError(f"{location} has no valid position.")
        for axis in ("x", "y"):
            value = position.get(axis)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"{location} position {axis} must be a finite number.")

        for field in ("rotation_deg", "scale_factor"):
            value = component.get(field)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"{location} {field} must be a finite number.")
            if field == "scale_factor" and value <= 0:
                raise ValueError(f"{location} scale_factor must be greater than zero.")

        port_positions = component.get("port_positions", {})
        if not isinstance(port_positions, dict):
            raise ValueError(f"{location} port_positions must be an object.")
        for port, point in port_positions.items():
            if port not in ports or not isinstance(point, dict):
                raise ValueError(f"{location} has invalid port position data.")
            for axis in ("x", "y"):
                value = point.get(axis)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                ):
                    raise ValueError(
                        f"{location} port {port} position {axis} "
                        "must be a finite number."
                    )

    for index, connection in enumerate(connections):
        location = f"Connection {index + 1}"
        if not isinstance(connection, dict):
            raise ValueError(f"{location} must be an object.")
        for endpoint_name in ("from", "to"):
            endpoint = connection.get(endpoint_name)
            if not isinstance(endpoint, dict):
                raise ValueError(f"{location} has no valid {endpoint_name} endpoint.")
            component_id = endpoint.get("component")
            port = endpoint.get("port")
            if not isinstance(component_id, str) or component_id not in component_ports:
                raise ValueError(
                    f"{location} references unknown component {component_id!r}."
                )
            if not isinstance(port, str) or not port:
                raise ValueError(f"{location} has an invalid {endpoint_name} port.")
            if port not in component_ports[component_id]: # checks 
                raise ValueError(
                    f"{location} references unknown port {port!r} on "
                    f"component {component_id!r}."
                )

    return netlist