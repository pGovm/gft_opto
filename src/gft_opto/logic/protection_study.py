# Validates configured protection scenarios and calculates their momentary DC loads and per-supply peaks using detected regions.

"""Editable protection studies on top of the CT-terminal topology detector.

The topology API in protection_zones.py remains unchanged. This module owns
JSON-safe study settings, proposal/review, validation and per-stage/per-supply
momentary calculations. It has no Qt dependency. Topology candidates are NOT
operating scenarios: only explicitly configured actions enter calculations.
"""
from __future__ import annotations

from gft_opto.gui import messages

from collections import defaultdict, deque
from dataclasses import dataclass, field
import hashlib
import json
import math
import copy
from typing import Any

from gft_opto.logic.momentary_calc2 import Load, Scenario, NETLIST_PROPERTY_LOAD_MAP
from gft_opto.logic.breaker_settings import resolve_breaker_action, breaker_settings, SETTING_KEYS
from gft_opto.logic.protection_zones import (
    Zone, connection_key, identify_zones, generate_composite_zones,
    find_enclosed_zones, zone_signature, is_open_switch,
)

LOADS = dict(NETLIST_PROPERTY_LOAD_MAP)
STAGES = ("Primary trip", "Backup trip", "Breaker failure", "Reclose", "Motor inrush", "Motor run")
ZONE_KINDS = ("Bus", "Transformer", "Line", "Manual")
_PASSIVE = {"ct", "pt", "ground", "junction", "bus", "surge_arrester", "incoming_line", "line_trap"}


def kind(component: dict) -> str:
    """Canonical symbol type, including custom-library built-in symbols."""
    props = component.get("properties") or {}
    value = props.get("symbol_type") or component.get("type") or component.get("symbol") or "custom"
    value = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    return {"cb": "breaker", "circuit_breaker": "breaker", "bct": "ct",
            "current_transformer": "ct", "bushing_ct": "ct", "busbar": "bus",
            "transformer": "xfmr_2w", "potential_transformer": "pt"}.get(value, value)


def _text(value: Any, label: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(messages.PROTECTION_STUDY_MUST_BE.format(label, 'a string' if empty else 'a nonempty string'))
    return value


def _json(value: Any) -> Any:
    """Normalize tuples to JSON arrays; reject NaN instead of saving it."""
    return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode("utf-8")).hexdigest()


@dataclass(eq=False)
class _Item:
    instance_id: str
    record: dict
    _connections: list = field(default_factory=list)

    def connections(self):
        return list(self._connections)

    def visible_id(self):
        return self.record.get("label") or self.instance_id


class _Boundary(_Item):
    pass


@dataclass(eq=False)
class _Wire:
    from_item: _Item
    from_port: str | None
    to_item: _Item
    to_port: str | None


class _Graph:
    """Validated netlist adapter. Connections retain their named terminals."""
    def __init__(self, netlist: dict, boundary_ids: set[str] | None = None):
        if not isinstance(netlist, dict):
            raise ValueError(messages.PROTECTION_STUDY_NETLIST_MUST_BE_AN_OBJECT)
        components, connections = netlist.get("components"), netlist.get("connections")
        if not isinstance(components, list) or not isinstance(connections, list):
            raise ValueError(messages.PROTECTION_STUDY_NETLIST_COMPONENTS_AND_CONNECTIONS_MUST_BE_LISTS)
        self.items: dict[str, _Item] = {}
        self.records: dict[str, dict] = {}
        self.wires: list[_Wire] = []
        for component in components:
            if not isinstance(component, dict):
                raise ValueError(messages.PROTECTION_STUDY_EACH_NETLIST_COMPONENT_MUST_BE_AN_OBJECT)
            ident = _text(component.get("id"), "Component ID")
            if ident in self.items:
                raise ValueError(messages.PROTECTION_STUDY_DUPLICATE_COMPONENT_ID.format(ident))
            if not isinstance(component.get("properties", {}), dict):
                raise ValueError(messages.PROTECTION_STUDY_PROPERTIES_MUST_BE_AN_OBJECT.format(ident))
            boundary = kind(component) == "ct" and (boundary_ids is None or ident in boundary_ids)
            self.items[ident] = (_Boundary if boundary else _Item)(ident, component)
            self.records[ident] = component
        for record in connections:
            if isinstance(record, dict):
                a, ap, b, bp = (record.get(k) for k in ("from_id", "from_port", "to_id", "to_port"))
            elif isinstance(record, (list, tuple)) and len(record) == 4:
                a, ap, b, bp = record
            else:
                raise ValueError(messages.PROTECTION_STUDY_EACH_CONNECTION_NEEDS_TWO_COMPONENT_IDS_AND_TWO_TERMINALS)
            if not isinstance(a, str) or not isinstance(b, str) or a not in self.items or b not in self.items:
                raise ValueError(messages.PROTECTION_STUDY_WIRE_REFERENCES_AN_UNKNOWN_COMPONENT.format(a, b))
            for port in (ap, bp):
                if port is not None:
                    _text(port, "Terminal")
            wire = _Wire(self.items[a], ap, self.items[b], bp)
            self.items[a]._connections.append(wire)
            self.items[b]._connections.append(wire)
            self.wires.append(wire)

    def primary(self) -> list[Zone]:
        return identify_zones(self.items.values(), _Boundary, respect_switch_state=True)


def topology_signature(netlist: dict) -> str:
    """Review signature, intentionally distinct from detector region identity.

    Includes connectivity, symbol types, switching state and breaker settings.
    It excludes drawing
    geometry, labels and current values: currents are re-read and validated for
    every evaluation. The prefix invalidates approvals made by the retired API.
    """
    graph = _Graph(netlist)
    payload = {
        "version": 3,
        "components": sorted((i, kind(c), str(c.get("properties", {}).get("status", "Closed")))
                             for i, c in graph.records.items()),
        "connections": sorted({connection_key(w) for w in graph.wires}, key=lambda x: json.dumps(x)),
        # Supply and mechanism settings affect approved quantities/assignments,
        # unlike entered current magnitudes. Region identities remain unchanged.
        "breaker_settings": sorted((i, breaker_settings(c))
                                   for i, c in graph.records.items() if kind(c) == "breaker"),
    }
    return "ct-study-v3:" + _digest(payload)


def configuration_signature(zone: dict) -> str:
    """Prevent a stored confirmation from surviving later assignment edits."""
    return _digest({key: zone.get(key) for key in
                    ("kind", "seed", "boundary_cts", "actions", "topology_region")})


def _validate_region(signature: Any) -> None:
    if not isinstance(signature, dict) or set(signature) != {"core", "boundary", "connections"}:
        raise ValueError(messages.PROTECTION_STUDY_DETECTED_REGION_MUST_CONTAIN_CORE_BOUNDARY_AND_CONNECTIONS)
    for key in ("core", "boundary"):
        values = signature[key]
        if not isinstance(values, list):
            raise ValueError(messages.PROTECTION_STUDY_DETECTED_REGION_MUST_BE_A_LIST.format(key))
        for value in values:
            _text(value, f"Detected-region {key} ID")
        if len(values) != len(set(values)):
            raise ValueError(messages.PROTECTION_STUDY_DUPLICATE_DETECTED_REGION_ID.format(key))
    if set(signature["core"]) & set(signature["boundary"]):
        raise ValueError(messages.PROTECTION_STUDY_DETECTED_REGION_CORE_AND_BOUNDARIES_MUST_BE_DISJOINT)
    if not isinstance(signature["connections"], list):
        raise ValueError(messages.PROTECTION_STUDY_DETECTED_REGION_CONNECTIONS_MUST_BE_A_LIST)
    for edge in signature["connections"]:
        if not isinstance(edge, (list, tuple)) or len(edge) != 2:
            raise ValueError(messages.PROTECTION_STUDY_INVALID_DETECTED_REGION_CONNECTION)
        for endpoint in edge:
            if not isinstance(endpoint, (list, tuple)) or len(endpoint) != 2:
                raise ValueError(messages.PROTECTION_STUDY_INVALID_DETECTED_REGION_TERMINAL)
            _text(endpoint[0], "Detected-region component ID")
            if endpoint[1] is not None:
                _text(endpoint[1], "Detected-region terminal")


def validate_zones(zones: list[dict]) -> None:
    """Validate stored settings without needing a scene or mutating the input.

    Missing/deleted equipment and incomplete draft assignments are reported at
    evaluation time. Malformed structures and impossible unit counts cannot be
    saved. Unknown extra metadata is retained for compatibility.
    """
    if not isinstance(zones, list):
        raise ValueError(messages.PROTECTION_STUDY_ZONES_MUST_BE_A_LIST)
    ids = set()
    for zone in zones:
        if not isinstance(zone, dict):
            raise ValueError(messages.PROTECTION_STUDY_EACH_SAVED_ZONE_MUST_BE_AN_OBJECT_NOT_A)
        ident = _text(zone.get("id"), "Zone ID")
        if ident in ids:
            raise ValueError(messages.PROTECTION_STUDY_DUPLICATE_ZONE_ID.format(ident))
        ids.add(ident)
        _text(zone.get("name"), f"{ident}: zone name")
        if zone.get("kind") not in ZONE_KINDS:
            raise ValueError(messages.PROTECTION_STUDY_UNKNOWN_ZONE_KIND.format(ident))
        _text(zone.get("seed"), f"{ident}: seed", empty=True)
        if not isinstance(zone.get("confirmed"), bool):
            raise ValueError(messages.PROTECTION_STUDY_CONFIRMED_MUST_BE_TRUE_OR_FALSE.format(ident))
        boundaries = zone.get("boundary_cts")
        if not isinstance(boundaries, list):
            raise ValueError(messages.PROTECTION_STUDY_BOUNDARY_CTS_MUST_BE_A_LIST.format(ident))
        for boundary in boundaries:
            _text(boundary, "Boundary CT ID")
        if len(set(boundaries)) != len(boundaries):
            raise ValueError(messages.PROTECTION_STUDY_DUPLICATE_BOUNDARY_CTS.format(ident))
        actions = zone.get("actions")
        if not isinstance(actions, list):
            raise ValueError(messages.PROTECTION_STUDY_ACTIONS_MUST_BE_A_LIST.format(ident))
        for action in actions:
            if not isinstance(action, dict):
                raise ValueError(messages.PROTECTION_STUDY_EACH_ACTION_MUST_BE_AN_OBJECT.format(ident))
            _text(action.get("component"), "Action component", empty=True)
            if not isinstance(action.get("load"), str) or action["load"] not in LOADS:
                raise ValueError(messages.PROTECTION_STUDY_UNKNOWN_LOAD_PROPERTY.format(ident))
            # Draft rows may be blank, but cannot produce a complete result.
            _text(action.get("stage"), "Action stage", empty=True)
            if "operation" in action:
                if action["operation"] not in ("all_poles", "one_pole", "device", "legacy"):
                    raise ValueError(messages.PROTECTION_STUDY_UNKNOWN_OPERATING_ACTION.format(ident))
                if "supply" in action:
                    _text(action["supply"], "DC supply", empty=True)
            else:
                _text(action.get("supply"), "DC supply", empty=True)
            if "operation" not in action or "units" in action:
                units = action.get("units")
                if isinstance(units, bool) or not isinstance(units, int) or not 1 <= units <= 100:
                    raise ValueError(messages.PROTECTION_STUDY_UNITS_MUST_BE_AN_INTEGER_FROM_1_TO_100.format(ident))
        for key in ("topology_signature", "configuration_signature"):
            if key in zone and not isinstance(zone[key], str):
                raise ValueError(messages.PROTECTION_STUDY_MUST_BE_A_STRING.format(ident, key))
        if "topology_region" in zone:
            _validate_region(zone["topology_region"])


def _label(item) -> str:
    method = getattr(item, "visible_id", None)
    return str(method() if callable(method) else item.instance_id)


def topology_records(primary: list[Zone], composite: list[Zone] | None = None) -> list[dict]:
    """JSON-safe display catalog; accepts actual GUI Zone objects as well."""
    composite = generate_composite_zones(primary) if composite is None else composite
    regions = primary + composite
    parents = find_enclosed_zones(regions)
    result = []
    for level, subset in (("Primary", primary), ("Composite", composite)):
        for region in subset:
            signature = _json(zone_signature(region))
            labels = [_label(i) for i in sorted(region.core_items or region.boundary_items,
                                               key=lambda x: x.instance_id)]
            title = ", ".join(labels[:3]) + (" ..." if len(labels) > 3 else "")
            result.append({
                "id": region.zone_id,
                "name": f"{level} {'wire-only' if region.is_wire_only else 'region'}: {title}",
                "level": level, "core": signature["core"],
                "boundaries": signature["boundary"],
                "members": sorted(i.instance_id for i in region.all_items),
                "connections": signature["connections"],
                "wire_only": region.is_wire_only, "signature": signature,
                "enclosed_by": parents[region.zone_id], "calculated": False,
            })
    return result


def analyze_topology(netlist: dict) -> list[dict]:
    return topology_records(_Graph(netlist).primary())


def _geometry_warnings(graph: _Graph, region: Zone, zone: dict) -> list[str]:
    warnings = []
    if not zone["boundary_cts"] and zone["kind"] != "Manual":
        warnings.append(messages.PROTECTION_STUDY_NO_BOUNDARY_CTS_ASSIGNED_REVIEW_THE_REGION_OR_USE)
    touched = {item.instance_id for item in region.boundary_items}
    for ident in zone["boundary_cts"]:
        if ident not in graph.items:
            warnings.append(messages.PROTECTION_STUDY_BOUNDARY_CT_IS_MISSING.format(ident))
            continue
        if kind(graph.records[ident]) != "ct":
            warnings.append(messages.PROTECTION_STUDY_BOUNDARY_IS_NOT_A_CT.format(ident))
            continue
        if ident not in touched:
            warnings.append(messages.PROTECTION_STUDY_BOUNDARY_CT_IS_DISCONNECTED_FROM_THE_PROTECTED_REGION.format(ident))
            continue
        all_ports, region_ports = set(), set()
        for wire in graph.items[ident].connections():
            for item, port in ((wire.from_item, wire.from_port), (wire.to_item, wire.to_port)):
                if item.instance_id != ident:
                    continue
                all_ports.add(port)
                if connection_key(wire) in region.connection_keys:
                    region_ports.add(port)
        named_ports = all_ports - {None}
        if None in all_ports:
            warnings.append(messages.PROTECTION_STUDY_BOUNDARY_CT_HAS_AN_UNNAMED_TERMINAL_VERIFY_ITS_TWO.format(ident))
        elif len(named_ports) < 2:
            warnings.append(messages.PROTECTION_STUDY_BOUNDARY_CT_HAS_AN_UNCONNECTED_TERMINAL.format(ident))
        if len(region_ports - {None}) > 1:
            warnings.append(messages.PROTECTION_STUDY_BOUNDARY_CT_IS_BYPASSED_MULTIPLE_CT_TERMINALS_REACH_THIS.format(ident))
    return warnings


def _resolve_region(netlist: dict, zone: dict) -> tuple[Zone | None, list[str]]:
    boundaries = set(zone["boundary_cts"])
    snapshot = zone.get("topology_region")
    if snapshot is not None:
        # A detector composite cannot be reconstructed by just cutting its outer
        # CTs: that could absorb a third branch. Match the full footprint instead.
        graph = _Graph(netlist)
        primary = graph.primary()
        candidates = primary + generate_composite_zones(primary)
        matches = [r for r in candidates if _json(zone_signature(r)) == _json(snapshot)]
        if len(matches) != 1:
            return None, [messages.PROTECTION_STUDY_THE_SELECTED_DETECTED_REGION_CHANGED_OR_IS_MISSING_SELECT]
        region = matches[0]
        if boundaries != {i.instance_id for i in region.boundary_items}:
            return None, [messages.PROTECTION_STUDY_STORED_CT_BOUNDARIES_DO_NOT_MATCH_THE_SELECTED_DETECTED]
        if zone["seed"] and zone["seed"] not in {i.instance_id for i in region.core_items}:
            return None, [messages.PROTECTION_STUDY_PROTECTED_EQUIPMENT_DOES_NOT_BELONG_TO_THE_SELECTED_DETECTED]
    else:
        graph = _Graph(netlist, boundaries)
        seed = graph.items.get(zone["seed"])
        if seed is None:
            return None, [messages.PROTECTION_STUDY_PROTECTED_EQUIPMENT_IS_MISSING_OR_NOT_SELECTED.format(zone['seed'])]
        matches = [r for r in graph.primary() if seed in r.core_items]
        if len(matches) != 1:
            return None, [messages.PROTECTION_STUDY_PROTECTED_EQUIPMENT_MUST_BE_INSIDE_THE_REGION_NOT_AN]
        region = matches[0]
    return region, _geometry_warnings(graph, region, zone)


def zone_members(netlist: dict, zone: dict) -> tuple[list[str], list[str]]:
    validate_zones([zone])
    region, warnings = _resolve_region(netlist, zone)
    return (sorted(i.instance_id for i in region.all_items) if region else []), warnings


def _trip_candidates(graph: _Graph, region: Zone) -> list[str]:
    """Conservative nearest-breaker suggestions, always requiring user review.

    Stop each outward branch at its first breaker or a different protected bus/
    transformer. This is a topology convenience, not relay or trip-logic inference.
    """
    inside = {i.instance_id for i in region.all_items}
    breakers = {i.instance_id for i in region.core_items
                if kind(i.record) == "breaker" and not is_open_switch(i)}
    queue = deque()
    for boundary in region.boundary_items:
        for wire in boundary.connections():
            if connection_key(wire) not in region.connection_keys:
                other = wire.to_item if wire.from_item is boundary else wire.from_item
                queue.append(other)
    seen = set(inside)
    while queue:
        item = queue.popleft()
        if item.instance_id in seen:
            continue
        seen.add(item.instance_id)
        typ = kind(item.record)
        if is_open_switch(item):
            continue
        if typ == "breaker":
            breakers.add(item.instance_id)
            continue
        if typ in {"bus", "xfmr_2w", "xfmr_3w", "ground", "pt", "surge_arrester", "incoming_line", "line_trap"}:
            continue
        for wire in item.connections():
            queue.append(wire.to_item if wire.from_item is item else wire.from_item)
    return sorted(breakers)


def _proposal_ct_boundaries(region: Zone) -> set[str]:
    """Extend a draft to CTs across an unbranched single-breaker bay.

    Consecutive CT groups and closed isolators/junctions may lie on either side
    of the breaker. Retain the last CT reached, never an isolator or breaker as
    a substitute boundary. Branches, open devices, a second breaker, protected
    assets, unnamed terminals and loops stop expansion. This proposes overlap;
    it does not assign relay cores or change explicit user boundaries.
    """
    result = set()
    for boundary in region.boundary_items:
        current = boundary
        visited = {current.instance_id}
        outer = current.instance_id
        breakers = 0
        inward = [w for w in current.connections() if connection_key(w) in region.connection_keys]
        while len(inward) == 1:
            entry = inward[0]
            entry_port = entry.from_port if entry.from_item is current else entry.to_port
            outward = [w for w in current.connections() if w is not entry]
            if entry_port is None or len(outward) != 1:
                break
            wire = outward[0]
            exit_port = wire.from_port if wire.from_item is current else wire.to_port
            other = wire.to_item if wire.from_item is current else wire.from_item
            typ = kind(other.record)
            if (exit_port is None or (exit_port == entry_port and kind(current.record) != "junction")
                    or other.instance_id in visited or is_open_switch(other)):
                break
            if typ == "breaker":
                if breakers:
                    break
                breakers += 1
            elif typ not in {"ct", "disconnect", "mos", "junction"}:
                break
            # No through-path is established by a branch or a dangling device.
            if len(other.connections()) != 2:
                break
            ports = [w.from_port if w.from_item is other else w.to_port
                     for w in other.connections()]
            if None in ports or (typ != "junction" and len(set(ports)) != 2):
                break
            current = other
            visited.add(current.instance_id)
            inward = [wire]
            if typ == "ct":
                outer = current.instance_id
        result.add(outer)
    return result


def propose_zones(netlist: dict) -> list[dict]:
    """Propose asset and terminal-line regions with candidate CT overlap.

    Line terminals get a proposal alongside buses and transformers only when
    their primary region has CT boundaries and no bus/transformer, and expansion
    includes a closed breaker. This avoids suggesting a new line event solely
    because a metering tap can reach a remote bay through a branched bus.
    A breaker-only drawing retains its fallback proposals.
    No proposal is confirmed, no coil current or relay assignment is invented.
    """
    graph = _Graph(netlist)
    primary = graph.primary()
    seeds = [i for i in graph.items.values() if kind(i.record) in {"bus", "xfmr_2w", "xfmr_3w"}]
    line_seeds = []
    for region in primary:
        if not region.boundary_items or any(kind(i.record) in {"bus", "xfmr_2w", "xfmr_3w"}
                                            for i in region.core_items):
            continue
        terminals = [i for i in region.core_items if kind(i.record) == "incoming_line"]
        if not terminals:
            continue
        candidate_graph = _Graph(netlist, _proposal_ct_boundaries(region))
        seed = candidate_graph.items[terminals[0].instance_id]
        candidate = next((r for r in candidate_graph.primary() if seed in r.core_items), None)
        if (candidate is not None
                and not any(kind(i.record) in {"bus", "xfmr_2w", "xfmr_3w"} for i in candidate.core_items)
                and any(kind(i.record) == "breaker" and not is_open_switch(i) for i in candidate.core_items)):
            line_seeds.extend(terminals)
    if not seeds:
        seeds = [i for i in graph.items.values()
                 if kind(i.record) == "breaker" and not is_open_switch(i)]
        # Prefer the actual line endpoint over a separate breaker-only event.
        line_regions = [r for r in primary if any(i in r.core_items for i in line_seeds)]
        covered_breakers = set()
        for region in line_regions:
            covered_breakers.update(_trip_candidates(graph, region))
        seeds = [i for i in seeds if i.instance_id not in covered_breakers]
    seeds.extend(line_seeds)
    result = []
    footprints = set()
    for seed in sorted(seeds, key=lambda i: (kind(i.record) != "bus", i.instance_id)):
        region = next((r for r in primary if seed in r.core_items), None)
        if region is None:
            continue
        # Select the first breakers from the original CT partition. Searching
        # outward again after extending to opposite-side CTs could walk through
        # a neighboring line node and incorrectly add its next breaker.
        trip_candidates = _trip_candidates(graph, region)
        # Neighboring schemes may use CT groups on opposite sides of a breaker.
        # This candidate expansion retains that overlap for explicit review.
        boundaries = _proposal_ct_boundaries(region)
        expanded_graph = _Graph(netlist, boundaries)
        expanded_seed = expanded_graph.items[seed.instance_id]
        expanded = next((r for r in expanded_graph.primary() if expanded_seed in r.core_items), None)
        if expanded is not None:
            region = expanded
        category = "Bus" if kind(seed.record) == "bus" else (
            "Transformer" if kind(seed.record).startswith("xfmr") else "Line")
        if category == "Transformer":
            assets = [kind(i.record) for i in region.core_items]
            if "bus" in assets or sum(t in {"xfmr_2w", "xfmr_3w"} for t in assets) != 1:
                # Shared bus/transformer topology cannot establish an individual
                # transformer scheme. Keep the raw region available for review.
                continue
        footprint = (category, _digest(zone_signature(region)))
        if footprint in footprints:
            continue
        footprints.add(footprint)
        result.append({
            "id": "study-" + _digest(seed.instance_id)[:20],
            "name": f"{_label(seed)} {category} Zone", "kind": category,
            "seed": seed.instance_id,
            "boundary_cts": sorted(i.instance_id for i in region.boundary_items),
            "actions": [{"component": ident, "load": "trip_coil_1_a", "stage": "Primary trip",
                         "supply": "DC 1", "units": 1} for ident in trip_candidates],
            "confirmed": False,
        })
    return result


def add_missing_proposals(netlist: dict, zones: list[dict]) -> list[dict]:
    """Return existing settings plus missing seeds, without reusing an event ID.

    Selecting another region can change an existing event's seed without changing
    its ID. A newly proposed event for the old seed must get a distinct ID.
    Existing settings, confirmations and extra metadata are deep-copied intact.
    """
    validate_zones(zones)
    result = copy.deepcopy(zones)
    seeds = {z["seed"] for z in result}
    used_ids = {z["id"] for z in result}
    footprints = set()
    for existing in result:
        region, warnings = _resolve_region(netlist, existing)
        if region is not None and not warnings:
            footprints.add((existing["kind"], _digest(zone_signature(region))))
    for proposed in propose_zones(netlist):
        if proposed["seed"] in seeds:
            continue
        region, warnings = _resolve_region(netlist, proposed)
        footprint = (proposed["kind"], _digest(zone_signature(region))) if region is not None and not warnings else None
        if footprint is not None and footprint in footprints:
            continue
        if footprint is not None:
            footprints.add(footprint)
        base = proposed["id"]
        suffix = 2
        while proposed["id"] in used_ids:
            proposed["id"] = f"{base}-{suffix}"
            suffix += 1
        used_ids.add(proposed["id"])
        seeds.add(proposed["seed"])
        result.append(proposed)
    return result



def _physical_key(action: dict) -> tuple[str, str]:
    load = "motor" if action["load"] in {"motor_run_a", "motor_inrush_a"} else action["load"]
    return action["component"], load


def _stage_key(stage: str):
    return (STAGES.index(stage) if stage in STAGES else len(STAGES), stage)


def _evaluate_actions(zone: dict, graph: _Graph, voltage: float) -> tuple[list[dict], list[str]]:
    warnings = []
    grouped: dict[str, list[dict]] = defaultdict(list)
    for action in zone["actions"]:
        grouped[action["stage"].strip()].append(action)
    if not grouped:
        return [], [messages.PROTECTION_STUDY_NO_OPERATING_LOADS_ASSIGNED_TO_THIS_ZONE]
    results = []
    for stage, actions in sorted(grouped.items(), key=lambda pair: _stage_key(pair[0])):
        if not stage:
            warnings.append(messages.PROTECTION_STUDY_AN_OPERATING_STAGE_IS_MISSING)
        seen = {}
        scenario = Scenario(f"{zone['name']} / {stage}", voltage)
        by_supply: dict[str, Scenario] = {}
        metadata = []
        for action in actions:
            if "_resolution_error" in action:
                warnings.append(messages.DIAGNOSTIC_WITH_CONTEXT.format(action['component'], action['_resolution_error']))
                continue
            ident, key, units = action["component"], action["load"], action["units"]
            supply = action["supply"].strip()
            if not supply:
                warnings.append(messages.PROTECTION_STUDY_DC_SUPPLY_ASSIGNMENT_IS_MISSING.format(ident))
                continue
            physical = _physical_key(action)
            assignment = (key, supply, units)
            if physical in seen:
                if assignment != seen[physical]:
                    if physical[1] == "motor" and key != seen[physical][0]:
                        warnings.append(messages.PROTECTION_STUDY_MOTOR_INRUSH_AND_RUNNING_CURRENT_OVERLAP_IN_STAGE.format(ident, stage))
                    else:
                        warnings.append(messages.PROTECTION_STUDY_DUPLICATE_PHYSICAL_LOAD_HAS_INCONSISTENT_UNITS_OR_DC_SUPPLY.format(ident, stage))
                continue
            seen[physical] = assignment
            component = graph.records.get(ident)
            if component is None:
                warnings.append(messages.PROTECTION_STUDY_OPERATING_EQUIPMENT_IS_MISSING.format(ident))
                continue
            if kind(component) in _PASSIVE:
                warnings.append(messages.PROTECTION_STUDY_PASSIVE_EQUIPMENT_CANNOT_SUPPLY_AN_OPERATING_COIL_MOTOR_LOAD.format(ident))
                continue
            value = component.get("properties", {}).get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                warnings.append(messages.PROTECTION_STUDY_CURRENT_IS_MISSING_OR_NOT_NUMERIC.format(ident, LOADS[key]))
                continue
            try:
                valid = math.isfinite(value) and value > 0 and math.isfinite(value * units)
            except OverflowError:
                valid = False
            if not valid:
                warnings.append(messages.PROTECTION_STUDY_CURRENT_MUST_BE_FINITE_AND_GREATER_THAN_ZERO.format(ident, LOADS[key]))
                continue
            load = Load(f"{component.get('label') or ident} - {LOADS[key]}", value, units,
                        note=f"{ident}; {stage}; {supply}")
            scenario.add_load(load)
            by_supply.setdefault(supply, Scenario(f"{zone['name']} / {stage} / {supply}", voltage)).add_load(load)
            metadata.append({"component": ident, "load": key, "stage": stage, "supply": supply})
        # Guard accumulation overflow, not just individual invalid currents.
        try:
            finite_total = math.isfinite(sum(load.total_amps() for load in scenario.loads))
        except OverflowError:
            finite_total = False
        if not finite_total:
            warnings.append(messages.PROTECTION_STUDY_STAGE_TOTAL_CURRENT_EXCEEDS_THE_SUPPORTED_NUMERIC_RANGE.format(stage))
            continue
        calculated = scenario.calculate()
        for detail, source in zip(calculated["loads"], metadata):
            detail.update(source)
        supplies = {name: sc.calculate()["peak_current_A"] for name, sc in sorted(by_supply.items())}
        results.append({"stage": stage, "peak_current_A": calculated["peak_current_A"],
                        "supplies": supplies, "loads": calculated["loads"],
                        "advisories": calculated["warnings"]})
    return results, warnings


def evaluate_zones(netlist: dict, zones: list[dict]) -> dict:
    """Calculate configured events, never the sum of overlapping topology regions.

    Each stage is simultaneous within that zone. Different stages and zone
    faults remain separate; each DC supply gets its maximum event current.
    Exact duplicate load rows count once. Ambiguous, stale or incomplete studies
    have provisional rows, but no final supply_peaks.
    """
    validate_zones(zones)
    graph = _Graph(netlist)
    original_zones = {zone["id"]: zone for zone in zones}
    resolved = copy.deepcopy(zones)
    for zone in resolved:
        for action in zone["actions"]:
            component = graph.records.get(action["component"])
            if component is not None and kind(component) == "breaker" and (
                    "operation" in action or any(key in component.get("properties", {}) for key in SETTING_KEYS)):
                try:
                    action["supply"], action["units"] = resolve_breaker_action(component, action, zones)
                except ValueError as error:
                    action["_resolution_error"] = str(error)
            elif "operation" in action:
                action.setdefault("supply", "")
                action["units"] = 1
                if action["operation"] not in ("device", "all_poles"):
                    action["_resolution_error"] = messages.PROTECTION_STUDY_REVIEW_THIS_DEVICE_S_OPERATING_ACTION
    zones = resolved
    voltage = netlist.get("voltage_V", 125.0)
    try:
        valid_voltage = (not isinstance(voltage, bool) and isinstance(voltage, (int, float))
                         and math.isfinite(voltage) and voltage > 0)
    except OverflowError:
        valid_voltage = False
    if not valid_voltage:
        raise ValueError(messages.PROTECTION_STUDY_DC_VOLTAGE_MUST_BE_A_FINITE_POSITIVE_NUMBER)
    current_signature = topology_signature(netlist)
    report = {"complete": False, "zones": [], "warnings": [], "supply_peaks": {},
              "topology_regions": topology_records(graph.primary())}
    # A motor is one physical load even though inrush/run have different fields.
    assignments = defaultdict(lambda: defaultdict(set))
    for zone in zones:
        for action in zone["actions"]:
            if "_resolution_error" not in action and action["component"] and action["supply"].strip():
                assignments[_physical_key(action)][action["supply"].strip()].add(zone["id"])
    conflicts = defaultdict(list)
    for physical, supplies in assignments.items():
        if len(supplies) > 1:
            message = messages.PROTECTION_STUDY_HAS_INCONSISTENT_DC_SUPPLY_ASSIGNMENTS_ACROSS_THE_STUDY.format(physical[0], physical[1])
            for owners in supplies.values():
                for ident in owners:
                    conflicts[ident].append(message)
    for zone in zones:
        region, warnings = _resolve_region(netlist, zone)
        if not zone["confirmed"]:
            warnings.append(messages.PROTECTION_STUDY_CONFIRM_THIS_ZONE_S_CT_BOUNDARIES_OPERATING_LOADS_STAGES)
        elif zone.get("topology_signature") != current_signature:
            warnings.append(messages.PROTECTION_STUDY_RECONFIRM_THIS_ZONE_CONNECTIVITY_SWITCHING_STATE_OR_BREAKER_SETTINGS)
        original = original_zones[zone["id"]]
        if zone["confirmed"] and zone.get("configuration_signature") != configuration_signature(original):
            warnings.append(messages.PROTECTION_STUDY_RECONFIRM_THIS_ZONE_ITS_SAVED_OPERATING_ASSIGNMENTS_OR_CT)
        scenarios, action_warnings = _evaluate_actions(zone, graph, voltage)
        warnings.extend(action_warnings)
        warnings.extend(conflicts[zone["id"]])
        warnings = list(dict.fromkeys(warnings))
        report["zones"].append({
            "id": zone["id"], "name": zone["name"], "kind": zone["kind"],
            "members": sorted(i.instance_id for i in region.all_items) if region else [],
            "core": sorted(i.instance_id for i in region.core_items) if region else [],
            "boundaries": sorted(zone["boundary_cts"]),
            "connections": _json(zone_signature(region)["connections"]) if region else [],
            "wire_only": region.is_wire_only if region else False,
            "complete": not warnings, "warnings": warnings, "scenarios": scenarios,
            "enclosed_by": [],
        })
    # Each adapted graph uses different Python objects. Use stable item IDs for
    # configured-study containment instead of comparing object addresses.
    for inner in report["zones"]:
        ai = set(inner["members"])
        aw = {_digest(w) for w in inner["connections"]}
        if not ai and not aw:
            continue
        for outer in report["zones"]:
            if inner is outer:
                continue
            bi = set(outer["members"])
            bw = {_digest(w) for w in outer["connections"]}
            if ai <= bi and aw <= bw and (ai < bi or aw < bw):
                inner["enclosed_by"].append(outer["id"])
        inner["enclosed_by"].sort()
    covered = {i for row in report["zones"] for i in row["core"]}
    uncovered = sorted(i for i, c in graph.records.items()
                       if kind(c) in {"bus", "xfmr_2w", "xfmr_3w"} and i not in covered)
    if uncovered:
        report["warnings"].append(messages.PROTECTION_STUDY_PROTECTED_EQUIPMENT_NOT_COVERED_BY_A_CONFIGURED_ZONE.format(", ".join(uncovered)))
    if not zones:
        report["warnings"].append(messages.PROTECTION_STUDY_NO_OPERATING_ZONES_CONFIGURED_DETECTED_TOPOLOGY_REGIONS_ARE_DISPLAY)
    report["complete"] = bool(zones) and not report["warnings"] and all(r["complete"] for r in report["zones"])
    if report["complete"]:
        for row in report["zones"]:
            for scenario in row["scenarios"]:
                for supply, current in scenario["supplies"].items():
                    previous = report["supply_peaks"].get(supply)
                    if previous is None or current > previous["peak_current_A"]:
                        report["supply_peaks"][supply] = {"peak_current_A": current, "zone": row["name"],
                                                          "zone_id": row["id"], "stage": scenario["stage"]}
    return report
