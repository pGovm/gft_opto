# Checks momentary-load study calculations against small diagrams with known expected currents.

"""Deterministic zone and simultaneous-load examples, without Qt."""
import copy
import unittest

from gft_opto.logic.protection_study import (
    evaluate_zones, propose_zones, topology_signature, validate_zones, zone_members, configuration_signature,
)


def example():
    def component(ident, typ, **props):
        return {"id": ident, "type": typ, "label": ident, "properties": props}
    def wire(a, ap, b, bp):
        return {"from_id": a, "from_port": ap, "to_id": b, "to_port": bp}
    return {"components": [component("bus", "bus"), component("ct1", "ct"), component("ct2", "ct"),
        component("cb1", "breaker", trip_coil_1_a=10, trip_coil_2_a=8, close_coil_a=12, motor_inrush_a=15, motor_run_a=3),
        component("cb2", "breaker", trip_coil_1_a=20), component("g1", "ground"), component("g2", "ground")],
        "connections": [wire("bus", "left", "ct1", "right"), wire("ct1", "left", "cb1", "right"),
            wire("cb1", "left", "g1", "node"), wire("bus", "right", "ct2", "left"),
            wire("ct2", "right", "cb2", "left"), wire("cb2", "right", "g2", "node")]}


def shared_bus_region():
    return {
        "components": [
            {"id": "bus_a", "type": "bus", "label": "Bus A", "properties": {}},
            {"id": "bus_b", "type": "bus", "label": "Bus B", "properties": {}},
            {"id": "ct_a", "type": "ct", "properties": {}},
            {"id": "ct_b", "type": "ct", "properties": {}},
            {"id": "cb_a", "type": "breaker", "properties": {"trip_coil_1_a": 10}},
            {"id": "cb_b", "type": "breaker", "properties": {"trip_coil_1_a": 20}},
            {"id": "g_a", "type": "ground", "properties": {}},
            {"id": "g_b", "type": "ground", "properties": {}},
        ],
        "connections": [
            ["bus_a", "right", "bus_b", "left"],
            ["bus_a", "left", "ct_a", "left"],
            ["ct_a", "right", "cb_a", "right"],
            ["cb_a", "left", "g_a", "node"],
            ["bus_b", "right", "ct_b", "left"],
            ["ct_b", "right", "cb_b", "right"],
            ["cb_b", "left", "g_b", "node"],
        ],
    }


def approve(netlist, zone):
    """Model explicit user review after intentional setup changes."""
    zone["confirmed"] = True
    zone["topology_signature"] = topology_signature(netlist)
    zone["configuration_signature"] = configuration_signature(zone)
    return zone


def confirmed(netlist):
    zone = propose_zones(netlist)[0]
    zone["actions"] = [
        {"component": ident, "load": "trip_coil_1_a", "stage": "Primary trip",
         "supply": "DC 1", "units": 1}
        for ident in zone["candidate_breakers"]
    ]
    return approve(netlist, zone)


class ProtectionZones(unittest.TestCase):
    def test_ct_boundaries_and_nearest_breaker_proposals(self):
        netlist = example()
        zone = propose_zones(netlist)[0]
        self.assertEqual(set(zone["boundary_cts"]), {"ct1", "ct2"})
        self.assertEqual(zone["candidate_breakers"], ["cb1", "cb2"])
        self.assertEqual(zone["actions"], [])
        members, warnings = zone_members(netlist, zone)
        self.assertEqual(set(members), {"bus", "ct1", "ct2"})
        self.assertFalse(warnings)
        report = evaluate_zones(netlist, [zone])
        self.assertFalse(report["complete"])
        self.assertEqual(report["supply_peaks"], {})

    def test_zone_trip_sum(self):
        netlist = example()
        report = evaluate_zones(netlist, [confirmed(netlist)])
        self.assertTrue(report["complete"])
        self.assertEqual(report["supply_peaks"]["DC 1"]["peak_current_A"], 30)

    def test_stages_not_added_and_separate_supply_coils(self):
        netlist = example()
        zone = confirmed(netlist)
        zone["actions"].extend([
            {"component": "cb1", "load": "trip_coil_2_a", "stage": "Primary trip", "supply": "DC 2", "units": 3},
            {"component": "cb1", "load": "close_coil_a", "stage": "Reclose", "supply": "DC 1", "units": 3},
            {"component": "cb1", "load": "motor_inrush_a", "stage": "Motor inrush", "supply": "DC 1", "units": 1},
            {"component": "cb1", "load": "motor_run_a", "stage": "Motor run", "supply": "DC 1", "units": 1}])
        approve(netlist, zone)
        report = evaluate_zones(netlist, [zone])
        self.assertTrue(report["complete"])
        self.assertEqual(report["supply_peaks"]["DC 1"]["peak_current_A"], 36)
        self.assertEqual(report["supply_peaks"]["DC 2"]["peak_current_A"], 24)

    def test_duplicate_coil_in_same_stage_is_counted_once_and_requires_review(self):
        netlist = example()
        zone = confirmed(netlist)
        zone["actions"].append(copy.deepcopy(zone["actions"][0]))
        approve(netlist, zone)
        report = evaluate_zones(netlist, [zone])
        self.assertFalse(report["complete"])
        self.assertEqual(report["supply_peaks"], {})
        self.assertIn("duplicate physical load", " ".join(report["zones"][0]["warnings"]))
        self.assertEqual(
            report["zones"][0]["scenarios"][0]["peak_current_A"],
            30,
        )
        zone["actions"][-1]["supply"] = "DC 2"
        self.assertFalse(evaluate_zones(netlist, [zone])["complete"])

    def test_same_load_in_separate_stages_is_not_a_duplicate(self):
        netlist = example()
        zone = confirmed(netlist)
        second_stage = copy.deepcopy(zone["actions"][0])
        second_stage["stage"] = "Breaker failure"
        zone["actions"].append(second_stage)
        approve(netlist, zone)
        report = evaluate_zones(netlist, [zone])
        self.assertTrue(report["complete"])
        self.assertEqual(len(report["zones"][0]["scenarios"]), 2)

    def test_multiple_buses_in_one_region_get_one_unconfirmed_proposal(self):
        netlist = shared_bus_region()
        proposals = propose_zones(netlist)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["seed"], "bus_a")
        self.assertEqual(proposals[0]["name"], "Bus A, Bus B Bus Zone")
        self.assertEqual(proposals[0]["actions"], [])
        self.assertEqual(proposals[0]["candidate_breakers"], ["cb_a", "cb_b"])
        self.assertIn("Multiple buses share this detected region", " ".join(
            zone_members(netlist, proposals[0])[1]
        ))
        proposals[0]["actions"] = [
            {"component": ident, "load": "trip_coil_1_a", "stage": "Primary trip",
             "supply": "DC 1", "units": 1}
            for ident in proposals[0]["candidate_breakers"]
        ]
        report = evaluate_zones(netlist, proposals)
        self.assertFalse(report["complete"])
        self.assertEqual(report["zones"][0]["scenarios"][0]["peak_current_A"], 30)
        self.assertIn("Multiple buses share this detected region", " ".join(
            report["zones"][0]["warnings"]
        ))

    def test_multiple_confirmed_fault_events_can_share_a_bus_region(self):
        netlist = shared_bus_region()
        proposal = propose_zones(netlist)[0]
        events = []
        for ident, seed, breaker in (
            ("event_a", "bus_a", "cb_a"),
            ("event_b", "bus_b", "cb_b"),
        ):
            event = copy.deepcopy(proposal)
            event.update(id=ident, name=f"{seed} fault", seed=seed, kind="Manual")
            event["actions"] = [
                {"component": breaker, "load": "trip_coil_1_a", "stage": "Primary trip",
                 "supply": "DC 1", "units": 1}
            ]
            events.append(approve(netlist, event))
        report = evaluate_zones(netlist, events)
        self.assertEqual(len(report["zones"]), 2)
        self.assertTrue(all(row["complete"] for row in report["zones"]))

    def test_overlapping_zone_faults_not_added(self):
        netlist = example()
        first = confirmed(netlist)
        second = copy.deepcopy(first)
        second.update(id="other", name="Other zone")
        second["actions"] = second["actions"][:1]
        approve(netlist, second)
        report = evaluate_zones(netlist, [first, second])
        self.assertTrue(report["complete"])
        self.assertEqual(report["supply_peaks"]["DC 1"]["peak_current_A"], 30)

    def test_unassigned_ct_does_not_stop_traversal(self):
        netlist = example()
        zone = confirmed(netlist)
        zone["boundary_cts"] = ["ct2"]
        self.assertIn("cb1", zone_members(netlist, zone)[0])

    def test_changed_switch_state_requires_confirmation(self):
        netlist = example()
        zone = confirmed(netlist)
        netlist["components"][3]["properties"]["status"] = "Open"
        report = evaluate_zones(netlist, [zone])
        self.assertFalse(report["complete"])
        self.assertIn("Reconfirm", " ".join(report["zones"][0]["warnings"]))

    def test_missing_currents_and_deleted_devices_incomplete(self):
        netlist = example()
        zone = confirmed(netlist)
        netlist["components"][3]["properties"].pop("trip_coil_1_a")
        report = evaluate_zones(netlist, [zone])
        self.assertFalse(report["complete"])
        self.assertEqual(report["supply_peaks"], {})
        zone["actions"][0]["component"] = "deleted"
        self.assertIn("missing", " ".join(evaluate_zones(netlist, [zone])["zones"][0]["warnings"]))

    def test_invalid_geometry_and_motor_overlap_flagged(self):
        netlist = example()
        zone = confirmed(netlist)
        netlist["connections"].pop(1)
        self.assertTrue(zone_members(netlist, zone)[1])
        netlist = example()
        zone = confirmed(netlist)
        zone["actions"].extend({"component": "cb1", "load": key, "stage": "Motor", "supply": "DC 1", "units": 1}
            for key in ("motor_inrush_a", "motor_run_a"))
        self.assertFalse(evaluate_zones(netlist, [zone])["complete"])

    def test_invalid_saved_units_rejected(self):
        netlist = example()
        zone = confirmed(netlist)
        zone["actions"][0]["units"] = -1
        with self.assertRaises(ValueError):
            validate_zones([zone])

    def test_transformer_zone(self):
        netlist = example()
        netlist["components"][0]["type"] = "xfmr_2w"
        for wire in netlist["connections"]:
            if wire["from_id"] == "bus":
                wire["from_port"] = {"left": "H", "right": "X"}[wire["from_port"]]
        zone = confirmed(netlist)
        self.assertEqual(zone["kind"], "Transformer")
        self.assertTrue(evaluate_zones(netlist, [zone])["complete"])

    def test_uncovered_bus_marks_study_incomplete(self):
        netlist = example()
        zone = confirmed(netlist)
        netlist["components"].append({"id": "other_bus", "type": "bus", "label": "Other bus", "properties": {}})
        zone["topology_signature"] = topology_signature(netlist)
        report = evaluate_zones(netlist, [zone])
        self.assertFalse(report["complete"])
        self.assertIn("not covered", report["warnings"][0])

    def test_ct_bypass_requires_review(self):
        netlist = example()
        zone = confirmed(netlist)
        netlist["connections"].append({"from_id": "ct1", "from_port": "left", "to_id": "bus", "to_port": "right"})
        self.assertIn("bypassed", " ".join(zone_members(netlist, zone)[1]))

    def test_same_physical_coil_supply_consistent_across_zones(self):
        netlist = example()
        first = confirmed(netlist)
        second = copy.deepcopy(first)
        second.update(id="other", name="Other")
        second["actions"][0]["supply"] = "DC 2"
        report = evaluate_zones(netlist, [first, second])
        self.assertFalse(report["complete"])
        self.assertFalse(report["supply_peaks"])

    def test_breaker_failure_stage_is_explicit_not_added_to_primary(self):
        netlist = example()
        zone = confirmed(netlist)
        zone["actions"] = [zone["actions"][0],
            {"component": "cb2", "load": "trip_coil_1_a", "stage": "Breaker failure", "supply": "DC 1", "units": 1}]
        approve(netlist, zone)
        report = evaluate_zones(netlist, [zone])
        self.assertTrue(report["complete"])
        self.assertEqual(report["supply_peaks"]["DC 1"]["peak_current_A"], 20)


if __name__ == "__main__":
    unittest.main()
