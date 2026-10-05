"""
Protection zone identification — first-pass algorithm.

Operates directly on the live WorkspaceView scene graph (no netlist file
needed): each OneLineSymbolItem already tracks its own connections via
`_connections` / `add_connection()`, and each ConnectionItem stores
`from_item` / `from_port` / `to_item` / `to_port`.

Domain assumption (standard substation protection practice): zones of
protection are bounded by Current Transformers (CTs). Each CT sits on the
boundary of two adjacent zones, so zones legitimately overlap at CTs —
this is what allows one zone to be "enclosed" within another, per the
supporter feedback.

This is a sketch to validate the approach — not yet wired into the GUI.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Zone:
    """One protection zone."""

    zone_id: str
    # Non-boundary components that make up the "core" of this zone.
    core_items: set = field(default_factory=set)
    # CTs bounding this zone (shared with adjacent zones).
    boundary_items: set = field(default_factory=set)

    @property
    def all_items(self) -> set:
        return self.core_items | self.boundary_items

    def __repr__(self):
        core = sorted(i.instance_id for i in self.core_items)
        bound = sorted(i.instance_id for i in self.boundary_items)
        return f"Zone({self.zone_id}, core={core}, boundary={bound})"


# Identify boundary items (Configurable)

def _is_boundary_item(item, boundary_types) -> bool:
    """
    A component is a zone boundary if its class is in `boundary_types`.
    Defaults to CurrentTransformerItem, matching standard protection zone
    convention. Pass a different/larger set if your team wants breakers
    (or something else) treated as boundaries too.
    """
    return isinstance(item, tuple(boundary_types))


# ---------------------------------------------------------------------------
# Step 2: connected-component traversal, with boundary nodes cut out
# ---------------------------------------------------------------------------

def _neighbors(item):
    """All components directly wired to `item`, via its connections."""
    for conn in item.connections():
        other = conn.to_item if conn.from_item is item else conn.from_item
        if other is not None:
            yield other


def identify_zones(all_items, boundary_types) -> list[Zone]:
    """
    Partition the one-line diagram into protection zones.

    all_items: iterable of every OneLineSymbolItem in the scene
               (e.g. [i for i in scene.items() if isinstance(i, OneLineSymbolItem)])
    boundary_types: tuple of classes that mark a zone boundary, e.g.
               (CurrentTransformerItem,)
    """
    boundary_items = {i for i in all_items if _is_boundary_item(i, boundary_types)}
    non_boundary_items = set(all_items) - boundary_items

    visited = set()
    zones: list[Zone] = []
    zone_counter = 0

    for start in non_boundary_items:
        if start in visited:
            continue

        # BFS over non-boundary items only — this finds one zone's "core".
        core = set()
        touched_boundaries = set()
        queue = [start]
        while queue:
            current = queue.pop()
            if current in core:
                continue
            core.add(current)
            visited.add(current)

            for neighbor in _neighbors(current):
                if neighbor in boundary_items:
                    touched_boundaries.add(neighbor)
                elif neighbor not in core:
                    queue.append(neighbor)

        zone_counter += 1
        zones.append(
            Zone(
                zone_id=f"Zone-{zone_counter}",
                core_items=core,
                boundary_items=touched_boundaries,
            )
        )

    return zones


# ---------------------------------------------------------------------------
# Step 3: identify zones enclosed within another zone
# ---------------------------------------------------------------------------

def find_enclosed_zones(zones: list[Zone]) -> dict[str, list[str]]:
    """
    Returns {zone_id: [ids of zones it is enclosed within]}.

    A zone A is considered "enclosed" in zone B if every item in A's core
    also appears in B's core or boundary (i.e. A sits entirely inside
    B's footprint).

    IMPORTANT: under identify_zones() alone, every non-boundary item is
    assigned to exactly one zone's core, so cores are always disjoint —
    this check can never fire between two primary zones. Enclosure only
    becomes meaningful once composite zones exist (see
    generate_composite_zones below), since a composite zone's core is a
    strict superset of the primary zones merged into it. Always run this
    over `primary_zones + composite_zones` together, not primary zones
    alone.
    """
    enclosures: dict[str, list[str]] = {z.zone_id: [] for z in zones}

    for a in zones:
        for b in zones:
            if a is b:
                continue
            if a.core_items and a.core_items.issubset(b.all_items):
                enclosures[a.zone_id].append(b.zone_id)

    return enclosures


# ---------------------------------------------------------------------------
# Step 3b: generate composite (coarser) zones by merging across a shared CT
# ---------------------------------------------------------------------------

def _zones_by_boundary(zones: list[Zone]) -> dict:
    """Map each boundary item -> the zones that touch it via that boundary."""
    mapping: dict = {}
    for z in zones:
        for b in z.boundary_items:
            mapping.setdefault(b, []).append(z)
    return mapping


def _merge_across_boundary(zone_a: Zone, zone_b: Zone, boundary_item) -> Zone:
    """
    Merge two zones that share `boundary_item`, treating that CT as now
    interior to the merged zone (it's a component *within* the bigger
    zone, not a boundary of it) — e.g. merging a transformer's zone with
    an adjacent breaker's zone across the CT between them, producing the
    bay-level zone that encloses the transformer's own zone.
    """
    merged_core = zone_a.core_items | zone_b.core_items | {boundary_item}
    merged_boundary = (zone_a.boundary_items | zone_b.boundary_items) - {boundary_item}
    return Zone(
        zone_id=f"{zone_a.zone_id}+{zone_b.zone_id}",
        core_items=merged_core,
        boundary_items=merged_boundary,
    )


def generate_composite_zones(primary_zones: list[Zone]) -> list[Zone]:
    """
    Generate one level of coarser "composite" zones by merging every pair
    of directly-adjacent primary zones across the CT they share.

    This produces exactly the "bigger zone encapsulating the transformer,
    which also contains other components such as breakers" case: the
    transformer's own primary zone and the neighboring breaker's primary
    zone get merged into one composite zone, one level out.

    NOTE — scoped intentionally to ONE merge level (pairwise), not a full
    hierarchy: merging arbitrarily many levels out eventually collapses
    everything into a single zone covering the whole diagram, which isn't
    a meaningful "zone" for the battery load calc. Confirm with the team
    whether deeper composites (3+ devices merged) are ever needed before
    extending this — it may be that only specific, intentional bay
    boundaries should merge, not every adjacent pair automatically.
    """
    boundary_map = _zones_by_boundary(primary_zones)
    composites = []
    seen_signatures = set()

    for zone in primary_zones:
        for boundary_item in zone.boundary_items:
            for other in boundary_map.get(boundary_item, []):
                if other is zone:
                    continue
                merged = _merge_across_boundary(zone, other, boundary_item)
                signature = frozenset(i.instance_id for i in merged.core_items)
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)
                composites.append(merged)

    return composites


def independent_zones(zones: list[Zone], enclosures: dict[str, list[str]]) -> list[Zone]:
    """
    Zones NOT enclosed within any other zone — i.e. the set that should
    feed the momentary DC battery load calculation, per the supporter
    clarification ("focus on zones not enclosed within another zone for
    the calculation, but all zones should still be displayed").
    """
    return [z for z in zones if not enclosures[z.zone_id]]


# ---------------------------------------------------------------------------
# Example usage (once wired into the GUI)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Pseudocode — replace with real imports once integrated:
    #
    # from substation_gui4 import OneLineSymbolItem, CurrentTransformerItem
    #
    # all_items = [i for i in scene.items() if isinstance(i, OneLineSymbolItem)]
    # primary_zones = identify_zones(all_items, boundary_types=(CurrentTransformerItem,))
    # composite_zones = generate_composite_zones(primary_zones)
    # all_zones = primary_zones + composite_zones
    # enclosures = find_enclosed_zones(all_zones)
    # zones_for_battery_calc = independent_zones(all_zones, enclosures)
    #
    # for z in all_zones:
    #     print(z, "-> enclosed in:", enclosures[z.zone_id])
    pass
