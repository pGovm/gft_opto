"""Topology-based zone proposals and explicit simultaneous DC load scenarios.

CT assignments and trip actions must be confirmed; topology is not relay logic.
This module deliberately has no Qt dependency.
"""
from collections import defaultdict
from math import isfinite
import json

LOADS = {
    "trip_coil_1_a": "Trip Coil 1", "trip_coil_2_a": "Trip Coil 2",
    "close_coil_a": "Close Coil", "motor_inrush_a": "Motor Inrush",
    "motor_run_a": "Motor Run",
}
STAGES = ("Primary trip", "Reclose", "Motor inrush", "Motor run", "Breaker failure")


def topology_signature(netlist):
    """Geometry and labels do not change protection connectivity."""
    return json.dumps({"components": sorted((c["id"], kind(c), c.get("properties", {}).get("status", "Closed"))
                      for c in netlist["components"]),
        "connections": sorted(tuple(sorted(((w["from_id"], w["from_port"]), (w["to_id"], w["to_port"]))))
                              for w in netlist["connections"])}, sort_keys=True)


def kind(component):
    return component.get("properties", {}).get("symbol_type", component["type"])


def topology(netlist, boundaries):
    components = {c["id"]: c for c in netlist["components"]}
    ports, graph, wired = defaultdict(set), defaultdict(set), set()
    for wire in netlist["connections"]:
        a, b = (wire["from_id"], wire["from_port"]), (wire["to_id"], wire["to_port"])
        if a[0] not in components or b[0] not in components:
            continue
        graph[a].add(b)
        graph[b].add(a)
        wired.update((a, b))
        ports[a[0]].add(a)
        ports[b[0]].add(b)
    for ident, component in components.items():
        if ident in boundaries or kind(component) in ("pt", "ground"):
            continue
        if kind(component) in ("breaker", "disconnect", "mos") and component.get("properties", {}).get("status", "Closed") != "Closed":
            continue
        for port in ports[ident]:
            graph[port].update(ports[ident] - {port})
    return components, ports, graph


def zone_members(netlist, zone):
    components, ports, graph = topology(netlist, set(zone["boundary_cts"]))
    seed = zone["seed"]
    if seed not in components:
        return [], ["Protected equipment is missing."]
    pending = list(ports[seed])
    visited = set(pending)
    while pending:
        port = pending.pop()
        for neighbor in graph[port] - visited:
            visited.add(neighbor)
            pending.append(neighbor)
    members = sorted({p[0] for p in visited} | {seed})
    warnings = []
    boundaries = zone["boundary_cts"]
    if not boundaries:
        warnings.append("No CT boundaries assigned.")
    for ident in boundaries:
        if ident not in components or kind(components[ident]) != "ct":
            warnings.append(f"Boundary CT {ident} is missing or is not a CT.")
        elif ident not in members:
            warnings.append(f"Boundary CT {ident} is not reachable from the protected equipment.")
        elif len(ports[ident] & visited) > 1:
            warnings.append(f"Boundary CT {ident} is bypassed by another connected path; review boundaries.")
    # A connection on one side of a two-terminal device is not a complete zone.
    for ident in members:
        component_kind = kind(components[ident])
        expected = 3 if component_kind == "xfmr_3w" else 2
        if component_kind not in ("bus", "junction", "ground", "pt") and len(ports[ident]) < expected:
            warnings.append(f"{components[ident].get('label', ident)} has unconnected terminals.")
    return members, warnings


def propose_zones(netlist):
    components = {c["id"]: c for c in netlist["components"]}
    cts = [c["id"] for c in components.values() if kind(c) == "ct"]
    proposals = []
    for component in components.values():
        if kind(component) not in ("bus", "xfmr_2w", "xfmr_3w"):
            continue
        ident = component["id"]
        zone = {"id": f"zone_{ident}", "name": f"{component.get('label', ident)} zone",
                "kind": "Bus" if kind(component) == "bus" else "Transformer",
                "seed": ident, "boundary_cts": cts[:], "confirmed": False, "actions": []}
        members, _ = zone_members(netlist, zone)
        zone["boundary_cts"] = sorted(set(cts) & set(members))
        # Suggest local breakers and the first breaker outward from each boundary.
        breakers = {i for i in members if kind(components[i]) == "breaker"}
        neighbors = defaultdict(set)
        for wire in netlist["connections"]:
            neighbors[wire["from_id"]].add(wire["to_id"])
            neighbors[wire["to_id"]].add(wire["from_id"])
        for ct in zone["boundary_cts"]:
            pending = list(neighbors[ct] - set(members))
            seen = set(members)
            while pending:
                current = pending.pop()
                if current in seen:
                    continue
                seen.add(current)
                typ = kind(components[current])
                if typ == "breaker":
                    breakers.add(current)
                    continue
                if typ in ("bus", "ct", "xfmr_2w", "xfmr_3w", "pt", "ground"):
                    continue
                pending.extend(neighbors[current] - seen)
        for breaker in sorted(breakers):
            zone["actions"].append({"component": breaker, "load": "trip_coil_1_a",
                "stage": "Primary trip", "supply": "DC 1", "units": 1})
        proposals.append(zone)
    return proposals


def validate_zones(zones):
    """Validate saved configuration structure; stale references are evaluation warnings."""
    if not isinstance(zones, list):
        raise ValueError("Protection zones must be a list.")
    ids = set()
    for zone in zones:
        if not isinstance(zone, dict):
            raise ValueError("Invalid protection zone.")
        for key in ("id", "name", "seed", "kind"):
            if not isinstance(zone.get(key), str) or not zone[key].strip():
                raise ValueError(f"A zone requires {key}.")
        if zone["id"] in ids:
            raise ValueError("Duplicate protection zone ID.")
        ids.add(zone["id"])
        if not isinstance(zone.get("confirmed"), bool) or not isinstance(zone.get("boundary_cts"), list) or any(not isinstance(i, str) for i in zone["boundary_cts"]):
            raise ValueError("Invalid CT assignments or confirmation.")
        if not isinstance(zone.get("actions"), list):
            raise ValueError("Zone actions must be a list.")
        for action in zone["actions"]:
            for key in ("component", "stage", "supply"):
                if not isinstance(action.get(key), str) or not action[key].strip():
                    raise ValueError(f"Each action requires {key}.")
            if action.get("load") not in LOADS:
                raise ValueError("Unknown coil or motor load.")
            units = action.get("units")
            if isinstance(units, bool) or not isinstance(units, int) or not 1 <= units <= 100:
                raise ValueError("Units must be an integer between 1 and 100.")


def evaluate_zones(netlist, zones):
    validate_zones(zones)
    components = {c["id"]: c for c in netlist["components"]}
    coil_supplies = defaultdict(set)
    for zone in zones:
        for action in zone["actions"]:
            coil_supplies[(action["component"], action["load"])].add(action["supply"])
    reports, supply_peaks = [], {}
    for zone in zones:
        members, warnings = zone_members(netlist, zone)
        if not zone["confirmed"]:
            warnings.append("CT boundaries and trip assignments have not been confirmed.")
        elif zone.get("topology_signature") != topology_signature(netlist):
            warnings.append("Connectivity or switch status has changed. Reconfirm this zone.")
        if not zone["actions"]:
            warnings.append("No operating loads assigned.")
        stages, seen = defaultdict(list), {}
        for action in zone["actions"]:
            ident, key = action["component"], action["load"]
            component = components.get(ident)
            if component is None:
                warnings.append(f"Assigned equipment {ident} is missing.")
                continue
            if len(coil_supplies[(ident, key)]) > 1:
                warnings.append(f"{ident} {LOADS[key]} has inconsistent DC supply assignments across stages or zones.")
            if key.startswith("trip_coil") and component.get("properties", {}).get("status", "Closed") != "Closed":
                warnings.append(f"{ident}: trip assigned to open/maintenance equipment; review its operating state.")
            # Each physical coil has one supply and unit count within a stage.
            identity = (action["stage"], ident, key)
            if identity in seen:
                if seen[identity] != (action["supply"], action["units"]):
                    warnings.append(f"Conflicting supply or units for {ident} {LOADS[key]}.")
                continue
            seen[identity] = action["supply"], action["units"]
            current = component.get("properties", {}).get(key)
            if isinstance(current, bool) or not isinstance(current, (int, float)) or not isfinite(current) or current <= 0:
                warnings.append(f"{component.get('label', ident)} {LOADS[key]} needs a positive current.")
                continue
            stages[action["stage"]].append({"name": f"{component.get('label', ident)} — {LOADS[key]}",
                "component": ident, "load": key, "supply": action["supply"], "quantity": action["units"],
                "amps_per_unit": current, "total_amps": current * action["units"],
                "note": f"{zone['name']} / {action['stage']} / {action['supply']}"})
        scenarios = []
        for stage, loads in stages.items():
            # Inrush and running current are sequential states of the same motor.
            inrush = {load["component"] for load in loads if load["load"] == "motor_inrush_a"}
            if any(load["load"] == "motor_run_a" and load["component"] in inrush for load in loads):
                warnings.append(f"{stage}: motor inrush and run assigned simultaneously for the same motor.")
            totals = defaultdict(float)
            for load in loads:
                totals[load["supply"]] += load["total_amps"]
            total = sum(totals.values())
            if not isfinite(total):
                warnings.append(f"{stage}: current exceeds the supported numeric range.")
                continue
            scenarios.append({"stage": stage, "loads": loads, "supplies": dict(totals), "peak_current_A": total})
        complete = not warnings
        if complete:
            for scenario in scenarios:
                for supply, current in scenario["supplies"].items():
                    if current > supply_peaks.get(supply, {}).get("peak_current_A", -1):
                        supply_peaks[supply] = {"peak_current_A": current, "zone": zone["name"], "stage": scenario["stage"]}
        reports.append({"id": zone["id"], "name": zone["name"], "kind": zone["kind"],
            "members": members, "boundaries": zone["boundary_cts"], "warnings": warnings,
            "complete": complete, "scenarios": scenarios})
    covered = {ident for report in reports for ident in report["members"]}
    warnings = [f"{component.get('label', ident)} is not covered by any zone; add or detect its protection zone."
                for ident, component in components.items()
                if kind(component) in ("bus", "xfmr_2w", "xfmr_3w") and ident not in covered]
    return {"zones": reports, "supply_peaks": supply_peaks, "warnings": warnings,
            "complete": bool(reports) and not warnings and all(r["complete"] for r in reports)}
