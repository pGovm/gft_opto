"""
Tests for protection_zones.py against the real interface it depends on:
- item.connections() -> list of connection objects
- connection.from_item / connection.to_item
- item.instance_id (for readable output)
- isinstance(item, boundary_types) for CT detection

These fakes mimic OneLineSymbolItem/ConnectionItem closely enough to
exercise the algorithm without needing PySide6 installed.
"""

from gft_opto.logic.protection_zones import (
    identify_zones,
    find_enclosed_zones,
    independent_zones,
    generate_composite_zones,
)


class FakeItem:
    def __init__(self, instance_id):
        self.instance_id = instance_id
        self._connections = []

    def connections(self):
        return list(self._connections)

    def __repr__(self):
        return self.instance_id


class CT(FakeItem):
    """Stand-in for CurrentTransformerItem."""
    pass


class FakeConnection:
    def __init__(self, a, b):
        self.from_item = a
        self.to_item = b
        a._connections.append(self)
        b._connections.append(self)


def summarize(zones, enclosures=None):
    for z in zones:
        core = sorted(i.instance_id for i in z.core_items)
        bound = sorted(i.instance_id for i in z.boundary_items)
        line = f"  {z.zone_id}: core={core} boundary={bound}"
        if enclosures is not None:
            line += f"  enclosed_in={enclosures[z.zone_id]}"
        print(line)


def test_linear_topology_with_two_cts():
    """A - CT1 - B - CT2 - C  =>  3 zones, each bounded by the CT(s) it touches."""
    print("\n=== Test 1: Linear topology, A-CT1-B-CT2-C ===")
    a, ct1, b, ct2, c = FakeItem("A"), CT("CT1"), FakeItem("B"), CT("CT2"), FakeItem("C")
    FakeConnection(a, ct1)
    FakeConnection(ct1, b)
    FakeConnection(b, ct2)
    FakeConnection(ct2, c)

    all_items = [a, ct1, b, ct2, c]
    zones = identify_zones(all_items, boundary_types=(CT,))
    summarize(zones)

    assert len(zones) == 3, f"expected 3 zones, got {len(zones)}"
    cores = sorted(tuple(sorted(i.instance_id for i in z.core_items)) for z in zones)
    assert cores == [("A",), ("B",), ("C",)], f"unexpected cores: {cores}"

    zone_a = next(z for z in zones if a in z.core_items)
    zone_b = next(z for z in zones if b in z.core_items)
    zone_c = next(z for z in zones if c in z.core_items)
    assert zone_a.boundary_items == {ct1}
    assert zone_b.boundary_items == {ct1, ct2}
    assert zone_c.boundary_items == {ct2}
    print("PASS")


def test_no_boundaries_forms_one_zone():
    """No CTs anywhere => everything connected collapses into a single zone."""
    print("\n=== Test 2: No CTs, fully connected network ===")
    a, b, c = FakeItem("A"), FakeItem("B"), FakeItem("C")
    FakeConnection(a, b)
    FakeConnection(b, c)

    zones = identify_zones([a, b, c], boundary_types=(CT,))
    summarize(zones)
    assert len(zones) == 1, f"expected 1 zone, got {len(zones)}"
    assert zones[0].core_items == {a, b, c}
    assert zones[0].boundary_items == set()
    print("PASS")


def test_disconnected_components_form_separate_zones():
    """Two electrically separate islands (no path between them) => 2 zones."""
    print("\n=== Test 3: Disconnected islands ===")
    a, b = FakeItem("A"), FakeItem("B")
    FakeConnection(a, b)
    x, y = FakeItem("X"), FakeItem("Y")
    FakeConnection(x, y)

    zones = identify_zones([a, b, x, y], boundary_types=(CT,))
    summarize(zones)
    assert len(zones) == 2, f"expected 2 zones, got {len(zones)}"
    print("PASS")


def test_isolated_item_with_no_connections():
    """A component with zero connections should still form its own zone."""
    print("\n=== Test 4: Fully isolated component ===")
    a = FakeItem("A")
    zones = identify_zones([a], boundary_types=(CT,))
    summarize(zones)
    assert len(zones) == 1
    assert zones[0].core_items == {a}
    assert zones[0].boundary_items == set()
    print("PASS")


def test_bus_with_multiple_cts_shared_boundary():
    """
    A bus (B) with three CTs hanging off it, each leading to a feeder.
    This is the realistic case that matters for substations: one zone
    (the bus zone) sharing boundaries with three adjacent feeder zones.
    """
    print("\n=== Test 5: Bus with 3 CTs (realistic substation shape) ===")
    bus = FakeItem("BUS")
    ct1, ct2, ct3 = CT("CT1"), CT("CT2"), CT("CT3")
    f1, f2, f3 = FakeItem("F1"), FakeItem("F2"), FakeItem("F3")
    FakeConnection(bus, ct1)
    FakeConnection(ct1, f1)
    FakeConnection(bus, ct2)
    FakeConnection(ct2, f2)
    FakeConnection(bus, ct3)
    FakeConnection(ct3, f3)

    all_items = [bus, ct1, ct2, ct3, f1, f2, f3]
    zones = identify_zones(all_items, boundary_types=(CT,))
    summarize(zones)

    assert len(zones) == 4, f"expected 4 zones (bus + 3 feeders), got {len(zones)}"
    bus_zone = next(z for z in zones if bus in z.core_items)
    assert bus_zone.boundary_items == {ct1, ct2, ct3}, "bus zone should touch all 3 CTs"
    print("PASS")


def test_enclosure_detection_never_fires_under_normal_partitioning():
    """
    IMPORTANT FINDING: because identify_zones() assigns every non-boundary
    item to exactly ONE zone's core (via the `visited` set), zone cores
    are always disjoint. find_enclosed_zones()'s subset check
    (a.core_items.issubset(b.all_items)) can therefore only be true if
    a.core is empty or a == b — a real, distinct zone's core can never be
    a subset of another zone's core+boundary, because none of its items
    are shared. This means, as currently implemented, enclosure detection
    will not fire for any topology produced by identify_zones() ALONE —
    composite zones (see Test 8) are required for enclosure to mean
    anything.
    """
    print("\n=== Test 6: Enclosure detection on primary zones only (no composites) ===")
    a, ct1, b, ct2, c = FakeItem("A"), CT("CT1"), FakeItem("B"), CT("CT2"), FakeItem("C")
    FakeConnection(a, ct1)
    FakeConnection(ct1, b)
    FakeConnection(b, ct2)
    FakeConnection(ct2, c)

    zones = identify_zones([a, ct1, b, ct2, c], boundary_types=(CT,))
    enclosures = find_enclosed_zones(zones)
    summarize(zones, enclosures)

    total_enclosures = sum(len(v) for v in enclosures.values())
    print(f"  total enclosure relationships found: {total_enclosures}")
    assert total_enclosures == 0, (
        "Expected 0 enclosures among primary zones alone — this is "
        "expected and is why composite zones (Test 8) are needed."
    )
    print("CONFIRMED (expected): primary zones alone never show enclosure; "
          "see Test 8 for the fix.")


def test_transformer_enclosed_in_bay_zone_with_breaker():
    """
    Reproduces the exact scenario described: a transformer bounded by two
    BCTs (bushing CTs) has its own small zone, but there's a bigger "bay"
    zone that encloses the transformer AND an adjacent breaker.

    Topology:  Feeder -- CT_in -- Transformer -- CT_out -- Breaker -- CT_far -- Bus

    - Primary zone "Transformer": core={Transformer}, boundary={CT_in, CT_out}
    - Primary zone "Breaker":     core={Breaker},     boundary={CT_out, CT_far}
    - Composite (merge across CT_out): core={Transformer, Breaker, CT_out},
      boundary={CT_in, CT_far}
    - The Transformer's primary zone should now be ENCLOSED within that
      composite bay zone, matching the described real-world relationship.
    """
    print("\n=== Test 8: Transformer zone enclosed in a bigger bay zone (with breaker) ===")
    feeder = FakeItem("FEEDER")
    ct_in = CT("CT_IN")
    xfmr = FakeItem("TRANSFORMER")
    ct_out = CT("CT_OUT")
    breaker = FakeItem("BREAKER")
    ct_far = CT("CT_FAR")
    bus = FakeItem("BUS")

    FakeConnection(feeder, ct_in)
    FakeConnection(ct_in, xfmr)
    FakeConnection(xfmr, ct_out)
    FakeConnection(ct_out, breaker)
    FakeConnection(breaker, ct_far)
    FakeConnection(ct_far, bus)

    all_items = [feeder, ct_in, xfmr, ct_out, breaker, ct_far, bus]
    primary_zones = identify_zones(all_items, boundary_types=(CT,))
    print("Primary zones:")
    summarize(primary_zones)

    composite_zones = generate_composite_zones(primary_zones)
    print("Composite zones (one merge level out):")
    summarize(composite_zones)

    all_zones = primary_zones + composite_zones
    enclosures = find_enclosed_zones(all_zones)
    print("Enclosure results:")
    summarize(all_zones, enclosures)

    xfmr_zone = next(z for z in primary_zones if xfmr in z.core_items)
    breaker_zone = next(z for z in primary_zones if breaker in z.core_items)

    # The transformer's own zone must now show up as enclosed in at least
    # one composite zone that also contains the breaker. It may also be
    # enclosed in other composites (e.g. merged with the feeder instead)
    # — that's expected, since a device can sit at the boundary of more
    # than one possible coarser zone. Search all of them rather than
    # assuming a fixed order (set iteration order isn't guaranteed).
    enclosing_ids = enclosures[xfmr_zone.zone_id]
    assert enclosing_ids, "Expected the transformer's zone to be enclosed in a composite zone"

    enclosing_candidates = [z for z in all_zones if z.zone_id in enclosing_ids]
    enclosing_zone = next(
        (z for z in enclosing_candidates if breaker in z.core_items), None
    )
    assert enclosing_zone is not None, (
        "Expected at least one enclosing composite zone to contain the breaker"
    )
    assert xfmr in enclosing_zone.core_items
    print(f"PASS — transformer zone is enclosed in: {enclosing_zone}")

    # And the transformer's primary zone should now be excluded from the
    # "independent" set used for the battery load calc, per the supporter
    # clarification, while the bigger bay zone remains independent.
    indep = independent_zones(all_zones, enclosures)
    indep_ids = {z.zone_id for z in indep}
    assert xfmr_zone.zone_id not in indep_ids, (
        "Transformer's own (enclosed) zone should be excluded from the battery load calc set"
    )
    assert enclosing_zone.zone_id in indep_ids, (
        "The bigger bay zone should remain in the battery load calc set"
    )
    print("PASS — independent_zones() correctly excludes the enclosed transformer "
          "zone and keeps the bay zone")


def test_independent_zones_matches_full_list_given_no_enclosures():
    """Given the finding above, independent_zones() will always return
    every zone, since none are ever marked enclosed."""
    print("\n=== Test 7: independent_zones() given no enclosures ever fire ===")
    a, ct1, b = FakeItem("A"), CT("CT1"), FakeItem("B")
    FakeConnection(a, ct1)
    FakeConnection(ct1, b)

    zones = identify_zones([a, ct1, b], boundary_types=(CT,))
    enclosures = find_enclosed_zones(zones)
    indep = independent_zones(zones, enclosures)
    print(f"  zones: {len(zones)}, independent_zones: {len(indep)}")
    assert len(indep) == len(zones), (
        "independent_zones() should currently return everything, since "
        "nothing is ever flagged as enclosed."
    )
    print("PASS (but flags the same underlying gap as Test 6)")


if __name__ == "__main__":
    test_linear_topology_with_two_cts()
    test_no_boundaries_forms_one_zone()
    test_disconnected_components_form_separate_zones()
    test_isolated_item_with_no_connections()
    test_bus_with_multiple_cts_shared_boundary()
    test_enclosure_detection_never_fires_under_normal_partitioning()
    test_transformer_enclosed_in_bay_zone_with_breaker()
    test_independent_zones_matches_full_list_given_no_enclosures()
    print("\nAll tests completed.")
