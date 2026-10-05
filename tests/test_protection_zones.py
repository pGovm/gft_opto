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


def _plain_list(names):
    """Join a list of names the way a person would say them out loud."""
    names = list(names)
    if not names:
        return "none"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def describe_zone(zone, label=None):
    """One plain-English sentence describing what's inside a zone and what guards it."""
    core = sorted(i.instance_id for i in zone.core_items)
    bound = sorted(i.instance_id for i in zone.boundary_items)
    name = label or zone.zone_id
    contains = _plain_list(core) if core else "nothing (boundary-only)"
    if bound:
        guard = f"guarded by current transformer(s) {_plain_list(bound)}"
    else:
        guard = "not guarded by any current transformer (open end of the network)"
    return f"  • {name}: contains {contains} — {guard}"


def report_header(title, plain_english):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'-' * 70}")
    print(f"  What this checks: {plain_english}")
    print(f"{'=' * 70}")


def report_pass(plain_conclusion):
    print(f"\n  ✅ PASS — {plain_conclusion}")


def summarize(zones, enclosures=None, labels=None):
    """Kept for any older callers; prints the plain-English zone list."""
    labels = labels or {}
    for z in zones:
        line = describe_zone(z, labels.get(z.zone_id))
        if enclosures is not None and enclosures.get(z.zone_id):
            enclosed_in = [labels.get(e, e) for e in enclosures[z.zone_id]]
            line += f"\n      → sits entirely inside: {_plain_list(enclosed_in)}"
        print(line)


def test_linear_topology_with_two_cts():
    """A - CT1 - B - CT2 - C  =>  3 zones, each bounded by the CT(s) it touches."""
    report_header(
        "TEST 1 — Three components in a row, split by two current transformers",
        "A simple chain (A — CT1 — B — CT2 — C). Each current transformer (CT) "
        "should mark the edge of a protection zone, and the component in the "
        "middle should be watched by BOTH of its neighboring CTs — that overlap "
        "is intentional and matches real substation protection design.",
    )
    a, ct1, b, ct2, c = FakeItem("A"), CT("CT1"), FakeItem("B"), CT("CT2"), FakeItem("C")
    FakeConnection(a, ct1)
    FakeConnection(ct1, b)
    FakeConnection(b, ct2)
    FakeConnection(ct2, c)

    all_items = [a, ct1, b, ct2, c]
    zones = identify_zones(all_items, boundary_types=(CT,))
    labels = {z.zone_id: f"Zone around {sorted(i.instance_id for i in z.core_items)[0]}" for z in zones}
    print("\n  Zones the tool identified:")
    summarize(zones, labels=labels)

    assert len(zones) == 3, f"expected 3 zones, got {len(zones)}"
    cores = sorted(tuple(sorted(i.instance_id for i in z.core_items)) for z in zones)
    assert cores == [("A",), ("B",), ("C",)], f"unexpected cores: {cores}"

    zone_a = next(z for z in zones if a in z.core_items)
    zone_b = next(z for z in zones if b in z.core_items)
    zone_c = next(z for z in zones if c in z.core_items)
    assert zone_a.boundary_items == {ct1}
    assert zone_b.boundary_items == {ct1, ct2}
    assert zone_c.boundary_items == {ct2}
    report_pass(
        "the tool correctly split the chain into 3 zones, and correctly gave "
        "the middle component (B) protection from both CTs on either side of it."
    )


def test_no_boundaries_forms_one_zone():
    """No CTs anywhere => everything connected collapses into a single zone."""
    report_header(
        "TEST 2 — A network with no protection devices at all",
        "If a group of components is wired together with no current transformers "
        "anywhere, there's nothing to split them into separate zones — they should "
        "all be treated as one unprotected zone. This checks the tool doesn't "
        "invent fake zone boundaries where none exist.",
    )
    a, b, c = FakeItem("A"), FakeItem("B"), FakeItem("C")
    FakeConnection(a, b)
    FakeConnection(b, c)

    zones = identify_zones([a, b, c], boundary_types=(CT,))
    print("\n  Zones the tool identified:")
    summarize(zones)
    assert len(zones) == 1, f"expected 1 zone, got {len(zones)}"
    assert zones[0].core_items == {a, b, c}
    assert zones[0].boundary_items == set()
    report_pass("all 3 components correctly merged into a single zone.")


def test_disconnected_components_form_separate_zones():
    """Two electrically separate islands (no path between them) => 2 zones."""
    report_header(
        "TEST 3 — Two completely separate circuits on the same diagram",
        "If the diagram contains two groups of equipment that aren't wired to "
        "each other at all, the tool should never lump them into the same zone, "
        "since they have nothing electrically to do with one another.",
    )
    a, b = FakeItem("A"), FakeItem("B")
    FakeConnection(a, b)
    x, y = FakeItem("X"), FakeItem("Y")
    FakeConnection(x, y)

    zones = identify_zones([a, b, x, y], boundary_types=(CT,))
    print("\n  Zones the tool identified:")
    summarize(zones)
    assert len(zones) == 2, f"expected 2 zones, got {len(zones)}"
    report_pass("the two disconnected circuits were correctly kept as 2 separate zones.")


def test_isolated_item_with_no_connections():
    """A component with zero connections should still form its own zone."""
    report_header(
        "TEST 4 — A single component with nothing connected to it",
        "A basic stability check: the tool should never crash or error out "
        "on an incomplete or in-progress diagram — a lone, unconnected "
        "component should still produce a valid (if trivial) zone.",
    )
    a = FakeItem("A")
    zones = identify_zones([a], boundary_types=(CT,))
    print("\n  Zones the tool identified:")
    summarize(zones)
    assert len(zones) == 1
    assert zones[0].core_items == {a}
    assert zones[0].boundary_items == set()
    report_pass("no crash, and the lone component correctly got its own zone.")


def test_bus_with_multiple_cts_shared_boundary():
    """
    A bus (B) with three CTs hanging off it, each leading to a feeder.
    This is the realistic case that matters for substations: one zone
    (the bus zone) sharing boundaries with three adjacent feeder zones.
    """
    report_header(
        "TEST 5 — A bus feeding three separate circuits (realistic substation layout)",
        "This is the shape that actually matters for GFT_OPTO: one bus with "
        "three current transformers, each leading out to a different feeder. "
        "The bus's own zone should be watched by all 3 CTs simultaneously, "
        "while each feeder gets its own separate zone.",
    )
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
    print("\n  Zones the tool identified:")
    summarize(zones)

    assert len(zones) == 4, f"expected 4 zones (bus + 3 feeders), got {len(zones)}"
    bus_zone = next(z for z in zones if bus in z.core_items)
    assert bus_zone.boundary_items == {ct1, ct2, ct3}, "bus zone should touch all 3 CTs"
    report_pass(
        "the bus correctly got its own zone watched by all 3 current transformers, "
        "with each feeder getting a separate zone of its own."
    )


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
    report_header(
        "TEST 6 — Checking for \"zones inside zones\" with only the basic zones",
        "Sponsors asked for zones that sit fully inside a bigger zone to be "
        "excluded from the battery load calculation. This test confirms that, "
        "using only the most basic zone breakdown, no zone is ever found "
        "inside another — which makes sense, since at this basic level every "
        "zone only touches its own unique piece of equipment. (The next test "
        "shows how the tool handles the real \"zone inside a zone\" case.)",
    )
    a, ct1, b, ct2, c = FakeItem("A"), CT("CT1"), FakeItem("B"), CT("CT2"), FakeItem("C")
    FakeConnection(a, ct1)
    FakeConnection(ct1, b)
    FakeConnection(b, ct2)
    FakeConnection(ct2, c)

    zones = identify_zones([a, ct1, b, ct2, c], boundary_types=(CT,))
    enclosures = find_enclosed_zones(zones)
    print("\n  Zones the tool identified:")
    summarize(zones, enclosures)

    total_enclosures = sum(len(v) for v in enclosures.values())
    print(f"\n  Zones found sitting inside another zone: {total_enclosures}")
    assert total_enclosures == 0, (
        "Expected 0 enclosures among primary zones alone — this is "
        "expected and is why composite zones (Test 8) are needed."
    )
    report_pass(
        "as expected, no zone was found enclosed in another at this basic level."
    )


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
    report_header(
        "TEST 8 — The real-world example: a transformer's zone inside a bigger zone",
        "This is the specific case the project sponsors described: a transformer "
        "has its own small protection zone, but there's also a bigger zone that "
        "wraps around the transformer AND an adjacent breaker. The small "
        "transformer zone should be correctly recognized as sitting INSIDE that "
        "bigger zone — so it gets left out of the battery load calculation, "
        "while the bigger zone is the one actually used for that calculation.",
    )
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
    print("\n  Step 1 — the basic, device-by-device zones:")
    summarize(primary_zones)

    composite_zones = generate_composite_zones(primary_zones)
    print("\n  Step 2 — bigger zones formed by combining neighboring devices:")
    summarize(composite_zones)

    all_zones = primary_zones + composite_zones
    enclosures = find_enclosed_zones(all_zones)
    print("\n  Step 3 — checking which zones sit inside a bigger zone:")
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
    report_pass(
        "the transformer's small zone was correctly recognized as sitting "
        "inside a bigger zone that also contains the breaker — exactly the "
        "relationship the sponsors described."
    )

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
    report_pass(
        "the transformer's small (enclosed) zone was correctly LEFT OUT of the "
        "battery load calculation, while the bigger zone that contains it was "
        "correctly KEPT IN — so the load only gets counted once, not twice."
    )


def test_independent_zones_matches_full_list_given_no_enclosures():
    """Given the finding above, independent_zones() will always return
    every zone, since none are ever marked enclosed."""
    report_header(
        "TEST 7 — Confirming the battery load calc gets ALL zones when none are enclosed",
        "A sanity check in the other direction from Test 8: when no zone is "
        "enclosed inside another, every zone should be included in the "
        "battery load calculation — nothing should be dropped by accident.",
    )
    a, ct1, b = FakeItem("A"), CT("CT1"), FakeItem("B")
    FakeConnection(a, ct1)
    FakeConnection(ct1, b)

    zones = identify_zones([a, ct1, b], boundary_types=(CT,))
    enclosures = find_enclosed_zones(zones)
    indep = independent_zones(zones, enclosures)
    print(f"\n  Zones identified: {len(zones)}  |  Zones used for the battery load calc: {len(indep)}")
    assert len(indep) == len(zones), (
        "independent_zones() should currently return everything, since "
        "nothing is ever flagged as enclosed."
    )
    report_pass("all zones were correctly included — none were dropped.")


if __name__ == "__main__":
    tests = [
        test_linear_topology_with_two_cts,
        test_no_boundaries_forms_one_zone,
        test_disconnected_components_form_separate_zones,
        test_isolated_item_with_no_connections,
        test_bus_with_multiple_cts_shared_boundary,
        test_enclosure_detection_never_fires_under_normal_partitioning,
        test_transformer_enclosed_in_bay_zone_with_breaker,
        test_independent_zones_matches_full_list_given_no_enclosures,
    ]
    for t in tests:
        t()

    print(f"\n{'=' * 70}")
    print("  SUMMARY FOR PROJECT SPONSORS")
    print(f"{'=' * 70}")
    print(f"""
  All {len(tests)} checks passed. In plain terms, this confirms the tool can:

    1. Correctly split a one-line diagram into protection zones, using
       current transformers as the boundary between zones.
    2. Correctly handle edge cases without crashing — no protection
       devices present, disconnected circuits, and lone components.
    3. Recognize the realistic substation shape of a bus feeding
       multiple circuits.
    4. Recognize when one protection zone (e.g. a transformer's own
       zone) sits fully inside a bigger zone (e.g. one that also
       includes an adjacent breaker) — and correctly count that
       bigger zone's load only once, rather than double-counting it
       with the smaller zone inside it.

  This logic is not yet connected to the tool's graphical interface —
  it has only been verified in isolation, against constructed test
  circuits, as shown above.
""")