"""ZONE-OBJECT-TEST-SUITE-V2: protection topology tests (no Qt).

Install this file as tests/test_protection_zones.py, replacing the old suite.
Run from the project root:
    python -m unittest discover -s tests -p test_protection_zones.py -v

Tests the scene-object API in gft_opto.logic.protection_zones.
This detector does not evaluate saved action rows, confirmation state,
stage currents or DC supplies; this suite does not emulate those APIs.
"""

import json
import random
import unittest
from collections import Counter

from gft_opto.logic.protection_zones import (
    Zone,
    connection_key,
    find_enclosed_zones,
    generate_composite_zones,
    identify_zones,
    independent_zones,
    stable_zone_id,
    zone_signature,
)


class FakeItem:
    """Minimal scene-item interface used by the real production detector."""

    def __init__(self, ident, equip_type="bus", **properties):
        self.instance_id = ident
        self.equip_type = equip_type
        self.properties = properties
        self._connections = []

    def connections(self):
        return list(self._connections)

    def __repr__(self):
        return f"{type(self).__name__}({self.instance_id!r})"


class FakeCT(FakeItem):
    def __init__(self, ident):
        super().__init__(ident, "ct")


class FakeBreaker(FakeItem):
    def __init__(self, ident, **properties):
        super().__init__(ident, "breaker", **properties)


class FakeConnection:
    """Retains endpoints and ports; permits dangling-end regression fixtures."""

    def __init__(self, a, ap, b, bp):
        self.from_item = a
        self.from_port = ap
        self.to_item = b
        self.to_port = bp
        for item in (a, b):
            if item is not None:
                item._connections.append(self)


def example():
    """Same equipment topology as the old netlist, now using scene objects.

    g1 -- cb1 -- ct1 -- bus -- ct2 -- cb2 -- g2
    """
    items = {
        "bus": FakeItem("bus"),
        "ct1": FakeCT("ct1"),
        "ct2": FakeCT("ct2"),
        "cb1": FakeBreaker("cb1", trip_coil_1_a=10),
        "cb2": FakeBreaker("cb2", trip_coil_1_a=20),
        "g1": FakeItem("g1", "ground"),
        "g2": FakeItem("g2", "ground"),
    }
    wires = [
        FakeConnection(items[a], ap, items[b], bp)
        for a, ap, b, bp in (
            ("bus", "left", "ct1", "right"),
            ("ct1", "left", "cb1", "right"),
            ("cb1", "left", "g1", "node"),
            ("bus", "right", "ct2", "left"),
            ("ct2", "right", "cb2", "left"),
            ("cb2", "right", "g2", "node"),
        )
    ]
    return items, wires


def item_ids(items):
    return {item.instance_id for item in items}


def signatures(zones):
    """Compare content, not incidental list position or Zone-1 style numbering."""
    return {
        zone.zone_id: json.dumps(zone_signature(zone), sort_keys=True)
        for zone in zones
    }


class ProtectionZones(unittest.TestCase):
    def zone_with_core(self, zones, expected):
        matches = [z for z in zones if item_ids(z.core_items) == set(expected)]
        self.assertEqual(len(matches), 1, f"Expected one core {expected!r}: {zones!r}")
        return matches[0]

    def test_ct_boundaries_partition_both_sides_of_the_circuit(self):
        items, wires = example()
        zones = identify_zones(items.values(), (FakeCT,))
        self.assertEqual(len(zones), 3)
        bus_zone = self.zone_with_core(zones, {"bus"})
        left_zone = self.zone_with_core(zones, {"cb1", "g1"})
        right_zone = self.zone_with_core(zones, {"cb2", "g2"})
        self.assertEqual(item_ids(bus_zone.boundary_items), {"ct1", "ct2"})
        self.assertEqual(item_ids(left_zone.boundary_items), {"ct1"})
        self.assertEqual(item_ids(right_zone.boundary_items), {"ct2"})
        self.assertEqual(bus_zone.connection_items, {wires[0], wires[3]})
        self.assertEqual(left_zone.connection_items, {wires[1], wires[2]})
        self.assertEqual(right_zone.connection_items, {wires[4], wires[5]})

    def test_every_ordinary_item_and_source_wire_belongs_to_one_primary_region(self):
        items, wires = example()
        zones = identify_zones(items.values(), FakeCT)
        self.assertEqual(
            Counter(item for z in zones for item in z.core_items),
            Counter(item for item in items.values() if not isinstance(item, FakeCT)),
        )
        self.assertEqual(Counter(w for z in zones for w in z.connection_items), Counter(wires))
        for zone in zones:
            self.assertIsInstance(zone, Zone)
            self.assertFalse(zone.core_items & zone.boundary_items)
            self.assertTrue(zone.all_items <= set(items.values()))

    def test_all_items_contains_components_not_wires_or_synthetic_items(self):
        items, wires = example()
        for zone in identify_zones(items.values(), FakeCT):
            self.assertEqual(zone.all_items, zone.core_items | zone.boundary_items)
            self.assertTrue(zone.all_items.isdisjoint(wires))
            self.assertTrue(all(i is items[i.instance_id] for i in zone.all_items))

    def test_ct_to_ct_wire_is_a_real_empty_core_region(self):
        a, b = FakeCT("ct1"), FakeCT("ct2")
        wire = FakeConnection(a, "right", b, "left")
        zones = identify_zones([a, b], FakeCT)
        self.assertEqual(len(zones), 1)
        zone = zones[0]
        self.assertEqual(zone.core_items, set())
        self.assertEqual(zone.boundary_items, {a, b})
        self.assertEqual(zone.connection_items, {wire})
        self.assertEqual(zone.connection_keys, frozenset({connection_key(wire)}))
        self.assertTrue(zone.is_wire_only)

    def test_parallel_ct_wires_on_different_terminals_remain_distinct(self):
        a, b = FakeCT("a"), FakeCT("b")
        first = FakeConnection(a, "left", b, "left")
        second = FakeConnection(a, "right", b, "right")
        zones = identify_zones([a, b], FakeCT)
        self.assertEqual(len(zones), 2)
        self.assertEqual({frozenset(z.connection_items) for z in zones},
                         {frozenset({first}), frozenset({second})})
        self.assertEqual(len({z.zone_id for z in zones}), 2)
        self.assertTrue(all(z.is_wire_only for z in zones))
        self.assertTrue(all(not parents for parents in find_enclosed_zones(zones).values()))

    def test_branches_on_the_same_ct_terminal_remain_connected(self):
        a, b, ct = FakeItem("a"), FakeItem("b"), FakeCT("ct")
        first = FakeConnection(a, "right", ct, "left")
        second = FakeConnection(ct, "left", b, "left")
        zones = identify_zones([a, ct, b], FakeCT)
        self.assertEqual(len(zones), 1)
        self.assertEqual(zones[0].core_items, {a, b})
        self.assertEqual(zones[0].connection_items, {first, second})

    def test_different_ct_terminals_are_not_internally_joined(self):
        a, b, ct = FakeItem("a"), FakeItem("b"), FakeCT("ct")
        FakeConnection(a, "right", ct, "left")
        FakeConnection(ct, "right", b, "left")
        zones = identify_zones([a, ct, b], FakeCT)
        self.assertEqual({frozenset(z.core_items) for z in zones},
                         {frozenset({a}), frozenset({b})})

    def test_legacy_unnamed_ct_terminals_stay_separate_per_connection(self):
        a, b, ct = FakeItem("a"), FakeItem("b"), FakeCT("ct")
        FakeConnection(a, None, ct, None)
        FakeConnection(ct, None, b, None)
        zones = identify_zones([a, ct, b], FakeCT)
        self.assertEqual(len(zones), 2)
        self.assertEqual({frozenset(z.core_items) for z in zones},
                         {frozenset({a}), frozenset({b})})

    def test_item_and_boundary_type_generators_work(self):
        items, _ = example()
        expected = signatures(identify_zones(items.values(), FakeCT))
        actual = identify_zones((i for i in items.values()), (t for t in [FakeCT]))
        self.assertEqual(signatures(actual), expected)

    def test_no_boundary_classes_makes_one_connected_region(self):
        items, wires = example()
        zones = identify_zones(items.values(), ())
        self.assertEqual(len(zones), 1)
        self.assertEqual(zones[0].core_items, set(items.values()))
        self.assertEqual(zones[0].boundary_items, set())
        self.assertEqual(zones[0].connection_items, set(wires))

    def test_empty_input_and_isolated_components(self):
        self.assertEqual(identify_zones([], FakeCT), [])
        self.assertEqual(identify_zones([FakeCT("ct")], FakeCT), [])
        a, b = FakeItem("a"), FakeItem("b")
        zones = identify_zones([a, b], FakeCT)
        self.assertEqual({frozenset(z.core_items) for z in zones},
                         {frozenset({a}), frozenset({b})})
        self.assertTrue(all(not z.is_wire_only for z in zones))

    def test_out_of_scope_paths_cannot_join_in_scope_items(self):
        a, b, outside = FakeItem("a"), FakeItem("b"), FakeItem("outside")
        FakeConnection(a, "right", outside, "left")
        FakeConnection(outside, "right", b, "left")
        zones = identify_zones([a, b], FakeCT)
        self.assertEqual(len(zones), 2)
        self.assertEqual(set.union(*(z.all_items for z in zones)), {a, b})
        self.assertTrue(all(not z.connection_items for z in zones))

    def test_dangling_endpoints_and_unrelated_stale_entries_are_ignored(self):
        a, b, c = FakeItem("a"), FakeItem("b"), FakeItem("c")
        FakeConnection(a, "right", None, None)
        valid = FakeConnection(b, "right", c, "left")
        a._connections.append(valid)  # This connection is not incident on a.
        zones = identify_zones([a, b, c], FakeCT)
        isolated = self.zone_with_core(zones, {"a"})
        self.assertFalse(isolated.connection_items)
        self.assertEqual(self.zone_with_core(zones, {"b", "c"}).connection_items, {valid})

    def test_duplicate_component_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate component ID"):
            identify_zones([FakeItem("same"), FakeItem("same")], FakeCT)

    def test_repeated_reference_to_the_same_item_is_allowed(self):
        item = FakeItem("a")
        zones = identify_zones([item, item], FakeCT)
        self.assertEqual(len(zones), 1)
        self.assertEqual(zones[0].core_items, {item})

    def test_invalid_component_ids_and_boundary_types_are_rejected(self):
        for ident in (None, "", " ", 7):
            with self.subTest(ident=ident), self.assertRaises(ValueError):
                identify_zones([FakeItem(ident)], FakeCT)
        with self.assertRaises(TypeError):
            identify_zones([], ["ct"])
        item = FakeItem("bad")
        item.connections = None
        with self.assertRaises(TypeError):
            identify_zones([item], FakeCT)

    def test_connection_keys_ignore_direction_but_preserve_ports(self):
        a, b = FakeCT("a"), FakeCT("b")
        first = FakeConnection(a, "left", b, "right")
        reversed_wire = FakeConnection(b, "right", a, "left")
        different = FakeConnection(a, "right", b, "left")
        self.assertEqual(connection_key(first), connection_key(reversed_wire))
        self.assertNotEqual(connection_key(first), connection_key(different))

    def test_invalid_connection_ports_are_rejected(self):
        for invalid in ("", " ", 5):
            with self.subTest(port=invalid):
                a, ct = FakeItem("a"), FakeCT("ct")
                FakeConnection(a, "right", ct, invalid)
                with self.assertRaises(ValueError):
                    identify_zones([a, ct], FakeCT)

    def test_duplicate_wire_objects_are_retained_with_one_electrical_key(self):
        a, b = FakeCT("a"), FakeCT("b")
        first = FakeConnection(a, "right", b, "left")
        before = identify_zones([a, b], FakeCT)[0].zone_id
        second = FakeConnection(b, "left", a, "right")
        zones = identify_zones([a, b], FakeCT)
        self.assertEqual(len(zones), 1)
        self.assertEqual(zones[0].connection_items, {first, second})
        self.assertEqual(len(zones[0].connection_keys), 1)
        self.assertEqual(zones[0].zone_id, before)

    def test_ids_and_signatures_are_stable_across_order_and_object_recreation(self):
        items, _ = example()
        expected = signatures(identify_zones(items.values(), FakeCT))
        for seed in (0, 1, 42, 12345):
            with self.subTest(seed=seed):
                cloned, _ = example()
                order = list(cloned.values())
                rng = random.Random(seed)
                rng.shuffle(order)
                for item in order:
                    rng.shuffle(item._connections)
                self.assertEqual(signatures(identify_zones(order, FakeCT)), expected)

    def test_ids_are_sorted_and_use_the_public_stable_id_function(self):
        items, _ = example()
        zones = identify_zones(items.values(), FakeCT)
        self.assertEqual([z.zone_id for z in zones], sorted(z.zone_id for z in zones))
        for zone in zones:
            self.assertEqual(zone.zone_id, stable_zone_id(zone))
            self.assertTrue(stable_zone_id(zone, "DrawingA").startswith("DrawingA-"))
            self.assertNotEqual(stable_zone_id(zone, "DrawingA"),
                                stable_zone_id(zone, "DrawingB"))
            with self.assertRaises(ValueError):
                stable_zone_id(zone, " ")
            json.dumps(zone_signature(zone))  # The public signature is serializable.

    def test_current_and_switch_status_edits_do_not_change_topology_identity(self):
        items, _ = example()
        before = signatures(identify_zones(items.values(), FakeCT))
        items["cb1"].properties.update(status="Open", trip_coil_1_a=99)
        self.assertEqual(signatures(identify_zones(items.values(), FakeCT)), before)

    def test_transformer_is_ordinary_equipment_not_a_special_zone_kind(self):
        items, _ = example()
        items["bus"].equip_type = "xfmr_2w"
        zones = identify_zones(items.values(), FakeCT)
        zone = self.zone_with_core(zones, {"bus"})
        self.assertEqual(item_ids(zone.boundary_items), {"ct1", "ct2"})
        self.assertEqual(len(zones), 3)

    def test_bypassing_a_ct_changes_regions_without_inventing_validation_warnings(self):
        items, _ = example()
        FakeConnection(items["bus"], "right", items["cb1"], "left")
        zones = identify_zones(items.values(), FakeCT)
        self.assertEqual(len(zones), 2)
        self.zone_with_core(zones, {"bus", "cb1", "g1"})

    def test_composites_merge_only_one_pairwise_level_and_keep_wires(self):
        items, wires = example()
        primary = identify_zones(items.values(), FakeCT)
        composites = generate_composite_zones(iter(primary))
        self.assertEqual(len(composites), 2)
        left = self.zone_with_core(composites, {"bus", "ct1", "cb1", "g1"})
        right = self.zone_with_core(composites, {"bus", "ct2", "cb2", "g2"})
        self.assertEqual(item_ids(left.boundary_items), {"ct2"})
        self.assertEqual(item_ids(right.boundary_items), {"ct1"})
        self.assertEqual(left.connection_items, {wires[0], wires[1], wires[2], wires[3]})
        self.assertEqual(right.connection_items, {wires[0], wires[3], wires[4], wires[5]})
        self.assertTrue(all(not {items["cb1"], items["cb2"]} <= z.core_items
                            for z in composites))

    def test_ct_gap_region_participates_in_composites_and_enclosure(self):
        a, b, ct1, ct2 = FakeItem("a"), FakeItem("b"), FakeCT("ct1"), FakeCT("ct2")
        FakeConnection(a, "right", ct1, "left")
        gap = FakeConnection(ct1, "right", ct2, "left")
        FakeConnection(ct2, "right", b, "left")
        primary = identify_zones([a, ct1, ct2, b], FakeCT)
        self.assertEqual(len(primary), 3)
        wire_zone = next(z for z in primary if z.is_wire_only)
        self.assertEqual(wire_zone.connection_items, {gap})
        composites = generate_composite_zones(primary)
        self.assertEqual(len(composites), 2)
        enclosures = find_enclosed_zones(primary + composites)
        self.assertEqual(set(enclosures[wire_zone.zone_id]), {z.zone_id for z in composites})
        self.assertEqual({z.zone_id for z in independent_zones(primary + composites, enclosures)},
                         {z.zone_id for z in composites})

    def test_all_fully_internal_shared_cts_are_removed_together(self):
        a, b, ct1, ct2 = FakeItem("a"), FakeItem("b"), FakeCT("ct1"), FakeCT("ct2")
        wires = {
            FakeConnection(a, "left", ct1, "left"),
            FakeConnection(ct1, "right", b, "left"),
            FakeConnection(a, "right", ct2, "left"),
            FakeConnection(ct2, "right", b, "right"),
        }
        primary = identify_zones([a, b, ct1, ct2], FakeCT)
        self.assertEqual(len(primary), 2)
        composites = generate_composite_zones(primary)
        self.assertEqual(len(composites), 1)
        self.assertEqual(composites[0].core_items, {a, b, ct1, ct2})
        self.assertFalse(composites[0].boundary_items)
        self.assertEqual(composites[0].connection_items, wires)

    def test_boundary_touching_a_third_region_is_not_silently_made_internal(self):
        a, b, c = FakeItem("a"), FakeItem("b"), FakeItem("c")
        shared, pair = FakeCT("shared"), FakeCT("pair")
        primary = [
            Zone("a", {a}, {shared, pair}),
            Zone("b", {b}, {shared, pair}),
            Zone("c", {c}, {shared}),
        ]
        composites = generate_composite_zones(primary)
        self.assertEqual(len(composites), 1)
        self.assertEqual(composites[0].core_items, {a, b, pair})
        self.assertEqual(composites[0].boundary_items, {shared})

    def test_three_way_boundary_alone_does_not_produce_partial_composites(self):
        ct = FakeCT("ct")
        primary = [Zone(name, {FakeItem(name)}, {ct}) for name in ("a", "b", "c")]
        self.assertEqual(generate_composite_zones(primary), [])

    def test_equal_footprints_do_not_mutually_enclose_each_other(self):
        a, ct = FakeItem("a"), FakeCT("ct")
        wire = FakeConnection(a, "right", ct, "left")
        first = Zone("first", {a}, {ct}, {wire})
        second = Zone("second", {a}, {ct}, {wire})
        enclosures = find_enclosed_zones([first, second])
        self.assertEqual(enclosures, {"first": [], "second": []})
        self.assertEqual(independent_zones([first, second], enclosures), [first, second])

    def test_enclosure_requires_boundaries_and_wires_not_just_a_core_subset(self):
        a, b, ct = FakeItem("a"), FakeItem("b"), FakeCT("ct")
        wire = FakeConnection(a, "right", ct, "left")
        inner = Zone("inner", {a}, {ct}, {wire})
        missing_boundary = Zone("missing_boundary", {a, b}, set(), {wire})
        missing_wire = Zone("missing_wire", {a, b}, {ct}, set())
        outer = Zone("outer", {a, b}, {ct}, {wire})
        enclosures = find_enclosed_zones([inner, missing_boundary, missing_wire, outer])
        self.assertEqual(enclosures["inner"], ["outer"])

    def test_empty_regions_do_not_claim_enclosure_and_nested_footprints_do(self):
        a, b, c = FakeItem("a"), FakeItem("b"), FakeItem("c")
        zones = [Zone("empty"), Zone("inner", {a}), Zone("middle", {a, b}),
                 Zone("outer", {a, b, c})]
        enclosures = find_enclosed_zones(iter(zones))
        self.assertEqual(enclosures["empty"], [])
        self.assertEqual(enclosures["inner"], ["middle", "outer"])
        self.assertEqual(enclosures["middle"], ["outer"])
        self.assertEqual({z.zone_id for z in independent_zones(iter(zones), enclosures)},
                         {"empty", "outer"})

    def test_invalid_zone_ids_and_overlapping_core_boundary_sets_are_rejected(self):
        item = FakeItem("a")
        invalid_cases = (
            [Zone("")],
            [Zone("same"), Zone("same")],
            [Zone("bad", {item}, {item})],
        )
        for zones in invalid_cases:
            for function in (find_enclosed_zones, generate_composite_zones):
                with self.subTest(function=function.__name__, zones=zones), self.assertRaises(ValueError):
                    function(zones)

    def test_missing_enclosure_entries_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Missing enclosure entries"):
            independent_zones([Zone("a", {FakeItem("a")})], {})

    def test_outermost_regions_can_share_breakers_and_are_separate_candidates(self):
        a, b, c = FakeBreaker("a"), FakeBreaker("b"), FakeBreaker("c")
        ct1, ct2 = FakeCT("ct1"), FakeCT("ct2")
        for left, lp, right, rp in (
            (a, "right", ct1, "left"), (ct1, "right", b, "left"),
            (b, "right", ct2, "left"), (ct2, "right", c, "left"),
        ):
            FakeConnection(left, lp, right, rp)
        primary = identify_zones([a, ct1, b, ct2, c], FakeCT)
        all_zones = primary + generate_composite_zones(primary)
        outermost = independent_zones(all_zones, find_enclosed_zones(all_zones))
        breaker_sets = [frozenset(i.instance_id for i in z.all_items if isinstance(i, FakeBreaker))
                        for z in outermost]
        self.assertEqual(set(breaker_sets), {frozenset({"a", "b"}), frozenset({"b", "c"})})
        self.assertEqual(set.intersection(*(set(ids) for ids in breaker_sets)), {"b"})
        # Union IDs BEFORE forming an intentionally combined result, not after
        # adding currents from separate candidates. No operating group is inferred.
        self.assertEqual(set.union(*(set(ids) for ids in breaker_sets)), {"a", "b", "c"})


if __name__ == "__main__":
    unittest.main()
