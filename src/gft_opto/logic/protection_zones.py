"""CT-cut topology regions for the breaker-based momentary calculation.

The GUI interface is unchanged: items have instance_id and connections();
connections expose from_item/from_port/to_item/to_port. No Qt import is needed.

Non-boundary equipment is internally connected, as in the original model.
Named terminals on a boundary item are NOT internally connected. This preserves
CT-to-CT conductors and branches attached to the SAME CT terminal without adding
fake equipment to the scene. Unnamed boundary terminals are treated separately
per electrical connection (the legacy item-only interface).

These are topology regions/candidate composites, not inferred operating groups
or approved PDF outlines. Momentary results must still be evaluated separately.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import hashlib
import json
from itertools import combinations
from typing import Any, Iterable

Endpoint = tuple[str, str | None]
ConnectionKey = tuple[Endpoint, Endpoint]


def _item_id(item: Any) -> str:
    value = getattr(item, "instance_id", None)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Every component must have a nonempty string instance_id")
    return value


def _port(connection: Any, attribute: str) -> str | None:
    value = getattr(connection, attribute, None)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"{attribute} must be a nonempty string or None")
    return value


def connection_key(connection: Any) -> ConnectionKey:
    """Undirected, port-aware electrical identity (not object memory address).

    Duplicate connection objects with the same terminals describe the same
    electrical conductor. Separate wires between different terminals stay
    distinct. Original objects are retained in Zone.connection_items.
    """
    endpoints = (
        (_item_id(connection.from_item), _port(connection, "from_port")),
        (_item_id(connection.to_item), _port(connection, "to_port")),
    )
    return tuple(sorted(endpoints, key=lambda end: (end[0], end[1] or "")))


@dataclass
class Zone:
    """A topology region. all_items contains real components, never wires.

    connection_items contains original GUI connection objects. In particular,
    a conductor between CTs can have an empty core, two boundary components,
    and a nonempty connection_items set. Draw that wire rather than a synthetic
    symbol. The first three constructor arguments retain their original meaning.
    """
    zone_id: str
    core_items: set = field(default_factory=set)
    boundary_items: set = field(default_factory=set)
    connection_items: set = field(default_factory=set)

    @property
    def all_items(self) -> set:
        return self.core_items | self.boundary_items

    @property
    def connection_keys(self) -> frozenset[ConnectionKey]:
        return frozenset(connection_key(c) for c in self.connection_items)

    @property
    def is_wire_only(self) -> bool:
        return not self.core_items and bool(self.connection_items)

    def __repr__(self) -> str:
        core = sorted(_item_id(i) for i in self.core_items)
        boundary = sorted(_item_id(i) for i in self.boundary_items)
        return (f"Zone({self.zone_id}, core={core}, boundary={boundary}, "
                f"wires={len(self.connection_keys)})")


def zone_signature(zone: Zone) -> dict:
    """Serializable identity including terminals, so parallel regions differ."""
    return {
        "core": sorted(_item_id(i) for i in zone.core_items),
        "boundary": sorted(_item_id(i) for i in zone.boundary_items),
        "connections": sorted(zone.connection_keys, key=lambda k: json.dumps(k)),
    }


def stable_zone_id(zone: Zone, namespace: str = "Zone") -> str:
    """Stable for unchanged IDs/members/wires, not a persistent ID after edits.

    Use a drawing/project namespace when combining independent circuits. No
    input iteration order, process hash seed or Python object address is used.
    """
    if not isinstance(namespace, str) or not namespace.strip():
        raise ValueError("namespace must be a nonempty string")
    encoded = json.dumps(zone_signature(zone), sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return f"{namespace}-{hashlib.sha256(encoded).hexdigest()[:24]}"


def _name_zone(zone: Zone) -> Zone:
    zone.zone_id = stable_zone_id(zone)
    return zone


def _boundary_classes(boundary_types: Iterable[type] | type) -> tuple[type, ...]:
    types = (boundary_types,) if isinstance(boundary_types, type) else tuple(boundary_types)
    if any(not isinstance(t, type) for t in types):
        raise TypeError("boundary_types must contain classes")
    return types


def _is_boundary_item(item: Any, boundary_types: Iterable[type] | type) -> bool:
    return isinstance(item, _boundary_classes(boundary_types))


def _neighbors(item: Any):
    """Legacy helper: ignore stale connection entries unrelated to this item."""
    for connection in item.connections():
        if connection.from_item is item:
            other = connection.to_item
        elif connection.to_item is item:
            other = connection.from_item
        else:
            continue
        if other is not None:
            yield other


class _DisjointSets:
    """Union/find over real equipment and internal boundary-terminal tokens."""
    def __init__(self) -> None:
        self.parent: dict = {}
        self.rank: dict = {}

    def add(self, node: tuple) -> None:
        if node not in self.parent:
            self.parent[node] = node
            self.rank[node] = 0

    def find(self, node: tuple) -> tuple:
        root = node
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[node] != node:
            parent = self.parent[node]
            self.parent[node] = root
            node = parent
        return root

    def union(self, left: tuple, right: tuple) -> None:
        self.add(left)
        self.add(right)
        a, b = self.find(left), self.find(right)
        if a == b:
            return
        if self.rank[a] < self.rank[b]:
            a, b = b, a
        self.parent[b] = a
        if self.rank[a] == self.rank[b]:
            self.rank[a] += 1


def identify_zones(all_items: Iterable[Any], boundary_types: Iterable[type] | type) -> list[Zone]:
    """Partition equipment AND its in-scope connections at boundary terminals.

    Both inputs may be generators. Connections leaving the supplied item set,
    dangling endpoints and stale unrelated list entries are ignored. Isolated
    non-boundary components have singleton zones; an isolated boundary with no
    conductor has no region. Duplicate component IDs are rejected.

    This retains the original item-level connectivity convention for ordinary
    components; it does not interpret switch status, current ratings or relays.
    """
    types = _boundary_classes(boundary_types)
    items = list(all_items)  # Snapshot once: never consume an iterator twice.
    registry: dict[str, Any] = {}
    for item in items:
        name = _item_id(item)
        if name in registry and registry[name] is not item:
            raise ValueError(f"Duplicate component ID: {name}")
        if not callable(getattr(item, "connections", None)):
            raise TypeError(f"{name}: expected a connections() method")
        registry[name] = item
    items = list(registry.values())
    scope = set(items)
    boundaries = {i for i in items if isinstance(i, types)}
    ordinary = scope - boundaries
    edges: dict[ConnectionKey, set] = defaultdict(set)

    for item in items:
        for connection in item.connections():
            a = getattr(connection, "from_item", None)
            b = getattr(connection, "to_item", None)
            if a is not item and b is not item:
                continue
            if a is None or b is None or a not in scope or b not in scope:
                continue
            edges[connection_key(connection)].add(connection)

    groups = _DisjointSets()
    for item in ordinary:
        groups.add(("item", item))

    def terminal(endpoint: Endpoint, key: ConnectionKey) -> tuple:
        name, port = endpoint
        item = registry[name]
        if item in ordinary:
            return ("item", item)
        # A missing port does not prove two attached wires use the same side.
        side = ("port", port) if port is not None else ("implicit", key)
        return ("boundary_terminal", item, side)

    for key in edges:
        groups.union(terminal(key[0], key), terminal(key[1], key))

    by_root: dict[tuple, Zone] = {}

    def region(node: tuple) -> Zone:
        root = groups.find(node)
        if root not in by_root:
            by_root[root] = Zone("")
        return by_root[root]

    for item in ordinary:
        region(("item", item)).core_items.add(item)
    for key, originals in edges.items():
        zone = region(terminal(key[0], key))
        zone.connection_items.update(originals)
        for name, _ in key:
            if registry[name] in boundaries:
                zone.boundary_items.add(registry[name])

    zones = sorted((_name_zone(z) for z in by_root.values()), key=lambda z: z.zone_id)
    _validate_zones(zones)
    return zones


def _validate_zones(zones: list[Zone]) -> None:
    ids = set()
    for zone in zones:
        if not isinstance(zone.zone_id, str) or not zone.zone_id.strip():
            raise ValueError("Every zone must have a nonempty string zone_id")
        if zone.zone_id in ids:
            raise ValueError(f"Duplicate zone ID: {zone.zone_id}")
        ids.add(zone.zone_id)
        if zone.core_items & zone.boundary_items:
            raise ValueError(f"{zone.zone_id}: core and boundary components must be disjoint")


def find_enclosed_zones(zones: Iterable[Zone]) -> dict[str, list[str]]:
    """Strict containment of complete component AND conductor footprints.

    Equal regions are not mutually enclosed. Empty-core conductor regions can
    be enclosed. Including wires prevents a zone on one pair of CT terminals
    being called enclosed in a different wire between the same CT components.
    This is topological containment, not geometric point-in-polygon testing.
    """
    zones = list(zones)
    _validate_zones(zones)
    footprints = [(z.all_items, z.connection_keys) for z in zones]
    enclosures = {z.zone_id: [] for z in zones}
    for i, a in enumerate(zones):
        ai, aw = footprints[i]
        if not ai and not aw:
            continue
        for j, b in enumerate(zones):
            if i == j:
                continue
            bi, bw = footprints[j]
            if ai <= bi and aw <= bw and (ai < bi or aw < bw):
                enclosures[a.zone_id].append(b.zone_id)
        enclosures[a.zone_id].sort()
    return enclosures


def _zones_by_boundary(zones: list[Zone]) -> dict:
    mapping: dict = defaultdict(list)
    for zone in zones:
        for boundary in zone.boundary_items:
            mapping[boundary].append(zone)
    return dict(mapping)


def _merge_across_boundary(zone_a: Zone, zone_b: Zone, boundary_item: Any) -> Zone:
    """Legacy single-boundary helper; public generator checks full incidence."""
    if boundary_item not in zone_a.boundary_items & zone_b.boundary_items:
        raise ValueError("The boundary must be shared by both zones")
    core = zone_a.core_items | zone_b.core_items | {boundary_item}
    return _name_zone(Zone(
        "", core, (zone_a.boundary_items | zone_b.boundary_items) - core,
        zone_a.connection_items | zone_b.connection_items,
    ))


def generate_composite_zones(primary_zones: Iterable[Zone]) -> list[Zone]:
    """One pairwise merge level, retaining all original conductors.

    Wire-only primary zones participate, so consecutive CTs no longer erase
    adjacency. All boundaries wholly internal to a selected pair are removed
    together (important when the same pair shares two CTs). A boundary also
    touching a third primary region stays a boundary; it is not silently cut.

    This does NOT recursively merge through arbitrary CT chains or infer the
    meaning of colored PDF outlines. Candidates may overlap and are not summed
    automatically for the momentary calculation.
    """
    primary = list(primary_zones)
    _validate_zones(primary)
    by_id = {z.zone_id: z for z in primary}
    boundary_map = _zones_by_boundary(primary)
    pairs: set[tuple[str, str]] = set()
    for adjacent in boundary_map.values():
        for a, b in combinations(adjacent, 2):
            pairs.add(tuple(sorted((a.zone_id, b.zone_id))))
    composites: dict[str, Zone] = {}
    primary_ids = set(by_id)
    for a_id, b_id in sorted(pairs):
        a, b = by_id[a_id], by_id[b_id]
        selected = {a_id, b_id}
        internal = {
            boundary for boundary in a.boundary_items & b.boundary_items
            if {z.zone_id for z in boundary_map[boundary]} <= selected
        }
        if not internal:
            # No boundary can be made interior without excluding a third side.
            continue
        core = a.core_items | b.core_items | internal
        zone = _name_zone(Zone(
            "", core, (a.boundary_items | b.boundary_items) - core,
            a.connection_items | b.connection_items,
        ))
        if zone.zone_id not in primary_ids:
            composites.setdefault(zone.zone_id, zone)
    return sorted(composites.values(), key=lambda z: z.zone_id)


def independent_zones(zones: Iterable[Zone], enclosures: dict[str, list[str]]) -> list[Zone]:
    """Legacy name for outermost regions, NOT mutually disjoint load groups.

    Breakers may appear in multiple separate results. Deduplicate breaker IDs
    within one result; do not sum separate results merely because they are here.
    """
    zones = list(zones)
    _validate_zones(zones)
    missing = {z.zone_id for z in zones} - set(enclosures)
    if missing:
        raise ValueError(f"Missing enclosure entries: {sorted(missing)}")
    return [z for z in zones if not enclosures[z.zone_id]]