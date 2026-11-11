"""Topology-derived feeder and opposite-side CT overlap candidates."""
import copy
import unittest

from gft_opto.logic.protection_study import (
    add_missing_proposals, configuration_signature, evaluate_zones,
    propose_zones, topology_signature, zone_members,
)


def component(ident, typ, **properties):
    return {"id": ident, "type": typ, "label": ident, "properties": properties}


def wire(a, b, ap="right", bp="left"):
    return {"from_id": a, "from_port": ap, "to_id": b, "to_port": bp}


def assign_candidates(zone):
    """Proposals only suggest breakers; assign them as the reviewed trip loads."""
    zone["actions"] = [{"component": ident, "load": "trip_coil_1_a", "stage": "Primary trip",
                        "supply": "DC 1", "units": 1} for ident in zone["candidate_breakers"]]
    return zone


def station():
    net = {"components": [component("bus", "bus")], "connections": []}
    for index, current in ((1, 10), (2, 20)):
        ids = ["bus", f"bus-ct-{index}", f"cb-{index}", f"line-ct-{index}", f"line-{index}"]
        for ident, typ in zip(ids[1:], ("ct", "breaker", "ct", "incoming_line")):
            net["components"].append(component(ident, typ, **({"trip_coil_1_a": current} if typ == "breaker" else {})))
        net["connections"].extend(wire(a, b) for a, b in zip(ids, ids[1:]))
    return net


class SchemeProposals(unittest.TestCase):
    def test_bus_and_two_feeders_are_proposed_together_with_breaker_overlap(self):
        net = station()
        original = copy.deepcopy(net)
        zones = propose_zones(net)
        self.assertEqual(net, original)
        self.assertEqual(len(zones), 3)
        bus = next(z for z in zones if z["kind"] == "Bus")
        self.assertEqual(set(bus["boundary_cts"]), {"line-ct-1", "line-ct-2"})
        bus_members, warnings = zone_members(net, bus)
        self.assertFalse(warnings)
        self.assertTrue({"cb-1", "cb-2"} <= set(bus_members))
        for index in (1, 2):
            line = next(z for z in zones if z["seed"] == f"line-{index}")
            self.assertEqual(line["kind"], "Line")
            self.assertEqual(line["boundary_cts"], [f"bus-ct-{index}"])
            members, warnings = zone_members(net, line)
            self.assertFalse(warnings)
            self.assertIn(f"cb-{index}", members)
            self.assertNotIn("bus", members)
            self.assertNotIn(f"cb-{3-index}", members)
            self.assertEqual(line["candidate_breakers"], [f"cb-{index}"])
            self.assertEqual(line["actions"], [])
        self.assertTrue(all(not z["confirmed"] for z in zones))

    def test_separate_faults_keep_correct_momentary_peaks(self):
        net = station()
        zones = propose_zones(net)
        for zone in zones:
            assign_candidates(zone)
            zone["confirmed"] = True
            zone["topology_signature"] = topology_signature(net)
            zone["configuration_signature"] = configuration_signature(zone)
        report = evaluate_zones(net, zones)
        self.assertTrue(report["complete"], report)
        self.assertEqual(report["supply_peaks"]["DC 1"]["peak_current_A"], 30)
        line_rows = [r for r in report["zones"] if r["kind"] == "Line"]
        self.assertEqual(sorted(r["scenarios"][0]["supplies"]["DC 1"] for r in line_rows), [10, 20])

    def test_three_breaker_loop_uses_two_adjacent_breakers_per_scheme(self):
        net = {"components": [component("bus", "bus"), component("a", "junction"),
                              component("b", "junction"), component("line-a", "incoming_line"),
                              component("line-b", "incoming_line")],
               "connections": [wire("a", "line-a"), wire("b", "line-b")]}
        for index, (left, right) in enumerate((("bus", "a"), ("a", "b"), ("b", "bus")), 1):
            chain = [left, f"ct-{index}-l", f"cb-{index}", f"ct-{index}-r", right]
            net["components"].extend([component(chain[1], "ct"), component(chain[2], "breaker"),
                                      component(chain[3], "ct")])
            net["connections"].extend(wire(a, b) for a, b in zip(chain, chain[1:]))
        zones = propose_zones(net)
        self.assertEqual(len(zones), 3)
        candidates = {z["seed"]: set(z["candidate_breakers"]) for z in zones}
        self.assertEqual(candidates, {"bus": {"cb-1", "cb-3"}, "line-a": {"cb-1", "cb-2"},
                                   "line-b": {"cb-2", "cb-3"}})

    def test_bus_tie_schemes_overlap_at_closed_breaker(self):
        net = station()
        net["components"] = [c for c in net["components"] if not c["id"].endswith("2")]
        net["connections"] = [w for w in net["connections"] if not w["to_id"].endswith("2")]
        next(c for c in net["components"] if c["id"] == "line-1")["type"] = "bus"
        zones = propose_zones(net)
        self.assertEqual(len(zones), 2)
        for zone in zones:
            self.assertEqual(zone["kind"], "Bus")
            members, warnings = zone_members(net, zone)
            self.assertFalse(warnings)
            self.assertIn("cb-1", members)
            self.assertFalse({"bus", "line-1"} <= set(members))

    def test_transformer_and_bus_schemes_overlap_at_the_intervening_breaker(self):
        net = station()
        next(c for c in net["components"] if c["id"] == "line-1")["type"] = "xfmr_2w"
        zones = propose_zones(net)
        transformer = next(z for z in zones if z["kind"] == "Transformer")
        self.assertEqual(transformer["boundary_cts"], ["bus-ct-1"])
        members, warnings = zone_members(net, transformer)
        self.assertFalse(warnings)
        self.assertIn("cb-1", members)
        self.assertNotIn("bus", members)

    def test_shared_unseparated_transformers_do_not_get_individual_schemes(self):
        net = station()
        net["components"].extend([component("xf-a", "xfmr_2w"), component("xf-b", "xfmr_2w")])
        net["connections"].extend([wire("bus", "xf-a"), wire("bus", "xf-b")])
        zones = propose_zones(net)
        self.assertFalse(any(z["kind"] == "Transformer" for z in zones))
        self.assertEqual(sum(z["kind"] == "Bus" for z in zones), 1)

    def test_open_breaker_prevents_overlap_and_does_not_suggest_remote_trip(self):
        net = station()
        next(c for c in net["components"] if c["id"] == "cb-1")["properties"]["status"] = "Open"
        zones = propose_zones(net)
        bus = next(z for z in zones if z["kind"] == "Bus")
        self.assertIn("bus-ct-1", bus["boundary_cts"])
        self.assertNotIn("line-ct-1", bus["boundary_cts"])
        self.assertEqual(bus["candidate_breakers"], ["cb-2"])
        self.assertFalse(any(z["seed"] == "line-1" for z in zones))

    def test_no_line_proposal_for_a_terminal_already_in_a_bus_region(self):
        net = station()
        net["components"].append(component("tap", "incoming_line"))
        net["connections"].append(wire("bus", "tap"))
        self.assertFalse(any(z["seed"] == "tap" for z in propose_zones(net)))

    def test_meter_ct_on_a_branched_bus_does_not_invent_another_line_scheme(self):
        net = station()
        net["components"].extend([component("join", "junction"), component("meter", "ct"),
                                  component("metered-terminal", "incoming_line")])
        next(w for w in net["connections"] if w["to_id"] == "bus-ct-1")["from_id"] = "join"
        net["connections"].extend([wire("bus", "join"), wire("join", "meter"),
                                   wire("meter", "metered-terminal")])
        self.assertFalse(any(z["seed"] == "metered-terminal" for z in propose_zones(net)))

    def test_expansion_stops_at_branches_and_does_not_cross_second_breaker(self):
        for barrier in ("branch", "second-breaker", "unnamed", "same-terminal"):
            with self.subTest(barrier=barrier):
                net = station()
                if barrier == "branch":
                    net["components"].append(component("tap", "junction"))
                    net["connections"].append(wire("cb-1", "tap"))
                elif barrier == "second-breaker":
                    net["components"].append(component("extra-cb", "breaker"))
                    next(w for w in net["connections"] if w["from_id"] == "cb-1")["to_id"] = "extra-cb"
                    net["connections"].append(wire("extra-cb", "line-ct-1"))
                else:
                    next(w for w in net["connections"] if w["from_id"] == "cb-1")["from_port"] = (
                        None if barrier == "unnamed" else "left")
                bus = next(z for z in propose_zones(net) if z["kind"] == "Bus")
                self.assertIn("bus-ct-1", bus["boundary_cts"])

    def test_unbranched_junction_and_closed_isolator_allow_opposite_side_ct(self):
        net = station()
        net["components"].extend([component("join", "junction"), component("ds", "disconnect", status="Closed")])
        next(w for w in net["connections"] if w["from_id"] == "cb-1")["to_id"] = "join"
        net["connections"].extend([wire("join", "ds", ap="left"), wire("ds", "line-ct-1")])
        bus = next(z for z in propose_zones(net) if z["kind"] == "Bus")
        self.assertIn("line-ct-1", bus["boundary_cts"])
        next(c for c in net["components"] if c["id"] == "ds")["properties"]["status"] = "Open"
        bus = next(z for z in propose_zones(net) if z["kind"] == "Bus")
        self.assertIn("bus-ct-1", bus["boundary_cts"])

    def test_existing_explicit_near_ct_assignment_remains_unchanged(self):
        net = station()
        saved = propose_zones(net)[0]
        saved["boundary_cts"] = ["bus-ct-1", "bus-ct-2"]
        saved["name"] = "Reviewed bus scheme"
        original = copy.deepcopy(saved)
        result = add_missing_proposals(net, [saved])
        self.assertEqual(result[0], original)
        self.assertEqual(saved, original)
        self.assertEqual(len(result), 3)

    def test_line_only_drawing_does_not_add_a_redundant_breaker_region(self):
        net = station()
        net["components"][0]["type"] = "incoming_line"
        zones = propose_zones(net)
        self.assertTrue(zones)
        self.assertTrue(all(z["kind"] == "Line" for z in zones))
        self.assertFalse(any(z["seed"].startswith("cb-") for z in zones))


if __name__ == "__main__":
    unittest.main()
