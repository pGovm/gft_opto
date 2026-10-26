# Checks that topology detection, configuration validation, and current calculations work together without Qt.

"""Regression checks for the real detector/study/calculator integration (no Qt)."""
import copy
import json
import unittest
from unittest.mock import patch

from gft_opto.logic import protection_study as study
from test_protection_study import example, confirmed


def gap_netlist():
    return {
        "components": [
            {"id": "left", "type": "breaker", "properties": {"trip_coil_1_a": 5}},
            {"id": "ct1", "type": "ct", "properties": {}},
            {"id": "ct2", "type": "ct", "properties": {}},
            {"id": "right", "type": "breaker", "properties": {"trip_coil_1_a": 7}},
        ],
        "connections": [
            {"from_id": "left", "from_port": "right", "to_id": "ct1", "to_port": "left"},
            {"from_id": "ct1", "from_port": "right", "to_id": "ct2", "to_port": "left"},
            {"from_id": "ct2", "from_port": "right", "to_id": "right", "to_port": "left"},
        ],
    }


def approve(netlist, zone):
    zone["confirmed"] = True
    zone["topology_signature"] = study.topology_signature(netlist)
    zone["configuration_signature"] = study.configuration_signature(zone)
    return zone


class StudyIntegration(unittest.TestCase):
    def setUp(self):
        self.net = example()
        self.zone = confirmed(self.net)

    def messages(self, report):
        return " ".join(report["warnings"] + [w for row in report["zones"] for w in row["warnings"]])

    def assert_incomplete(self, netlist, zones, word=None):
        report = study.evaluate_zones(netlist, zones)
        self.assertFalse(report["complete"])
        self.assertEqual(report["supply_peaks"], {})
        if word:
            self.assertIn(word.lower(), self.messages(report).lower())
        return report

    def test_json_safe_catalog_and_results_do_not_modify_input(self):
        before = copy.deepcopy((self.net, self.zone))
        report = study.evaluate_zones(self.net, [self.zone])
        self.assertEqual((self.net, self.zone), before)
        self.assertEqual(json.loads(json.dumps(report, allow_nan=False)), report)
        self.assertEqual(len(report["topology_regions"]), 5)
        self.assertTrue(all(not r["calculated"] for r in report["topology_regions"]))
        self.assertEqual(report["supply_peaks"]["DC 1"]["peak_current_A"], 30)

    def test_detector_and_calculator_are_executed_not_replaced(self):
        with patch.object(study, "identify_zones", wraps=study.identify_zones) as detector, \
             patch.object(study, "Scenario", wraps=study.Scenario) as scenario:
            report = study.evaluate_zones(self.net, [self.zone])
        self.assertTrue(report["complete"])
        self.assertGreater(detector.call_count, 0)
        self.assertGreater(scenario.call_count, 0)

    def test_ct_gap_is_selectable_and_saved_without_synthetic_equipment(self):
        net = gap_netlist()
        catalog = study.analyze_topology(net)
        regions = [r for r in catalog if r["wire_only"] and r["level"] == "Primary"]
        self.assertEqual(len(regions), 1)
        gap = regions[0]
        zone = {"id": "gap", "name": "CT gap", "kind": "Manual", "seed": "",
                "boundary_cts": gap["boundaries"], "topology_region": gap["signature"],
                "confirmed": False, "actions": [{"component": "right", "load": "trip_coil_1_a",
                    "stage": "Primary trip", "supply": "DC 1", "units": 1}]}
        approve(net, zone)
        report = study.evaluate_zones(net, json.loads(json.dumps([zone])))
        self.assertTrue(report["complete"], self.messages(report))
        self.assertEqual(report["zones"][0]["core"], [])
        self.assertTrue(report["zones"][0]["wire_only"])
        self.assertEqual(report["zones"][0]["members"], ["ct1", "ct2"])
        self.assertEqual(report["supply_peaks"]["DC 1"]["peak_current_A"], 7)
        self.assertEqual(len(net["components"]), 4)

    def test_composite_snapshot_resolves_exact_wire_membership(self):
        region = next(r for r in study.analyze_topology(self.net) if r["level"] == "Composite")
        zone = copy.deepcopy(self.zone)
        zone["topology_region"] = region["signature"]
        zone["boundary_cts"] = region["boundaries"]
        approve(self.net, zone)
        report = study.evaluate_zones(self.net, [zone])
        self.assertTrue(report["complete"], self.messages(report))
        self.assertEqual(report["zones"][0]["connections"], region["connections"])
        self.assertEqual(report["zones"][0]["members"], region["members"])

    def test_parallel_terminal_regions_are_distinct_not_mutually_enclosed(self):
        net = {"components": [{"id": i, "type": "ct"} for i in ("a", "b")],
               "connections": [["a", "left", "b", "left"], ["a", "right", "b", "right"]]}
        regions = [r for r in study.analyze_topology(net) if r["level"] == "Primary"]
        self.assertEqual(len(regions), 2)
        self.assertNotEqual(regions[0]["id"], regions[1]["id"])
        for region in regions:
            self.assertEqual(len(region["connections"]), 1)
            self.assertTrue(region["wire_only"])
            self.assertNotIn(regions[1]["id"] if region is regions[0] else regions[0]["id"],
                             region["enclosed_by"])
        report = self.assert_incomplete(net, [], "display-only")
        self.assertEqual(report["zones"], [])

    def test_changed_detected_region_is_not_silently_replaced(self):
        region = next(r for r in study.analyze_topology(self.net) if r["core"] == ["bus"])
        self.zone["topology_region"] = region["signature"]
        approve(self.net, self.zone)
        self.net["connections"].pop(0)
        report = self.assert_incomplete(self.net, [self.zone], "selected detected region changed")
        self.assertEqual(report["zones"][0]["members"], [])

    def test_snapshot_boundary_mismatch_requires_review(self):
        region = next(r for r in study.analyze_topology(self.net) if r["core"] == ["bus"])
        self.zone["topology_region"] = region["signature"]
        self.zone["boundary_cts"].pop()
        approve(self.net, self.zone)
        self.assert_incomplete(self.net, [self.zone], "boundaries do not match")

    def test_snapshot_seed_must_be_inside_core(self):
        region = next(r for r in study.analyze_topology(self.net) if r["core"] == ["bus"])
        self.zone["topology_region"] = region["signature"]
        self.zone["seed"] = "cb1"
        approve(self.net, self.zone)
        self.assert_incomplete(self.net, [self.zone], "does not belong")

    def test_legacy_settings_retained_but_old_approval_is_not_trusted(self):
        self.zone["topology_signature"] = "legacy-approval"
        self.zone["custom_notes"] = {"author": "local", "keep": True}
        original = copy.deepcopy(self.zone)
        study.validate_zones([self.zone])
        report = self.assert_incomplete(self.net, [self.zone], "Reconfirm")
        self.assertEqual(self.zone, original)
        self.assertEqual(report["zones"][0]["scenarios"][0]["peak_current_A"], 30)

    def test_changed_assignment_invalidates_new_confirmation(self):
        approve(self.net, self.zone)
        self.zone["actions"][0]["units"] = 2
        self.assert_incomplete(self.net, [self.zone], "saved operating assignments")

    def test_reapproval_of_changed_assignment_restores_result(self):
        approve(self.net, self.zone)
        self.zone["actions"][0]["units"] = 2
        approve(self.net, self.zone)
        report = study.evaluate_zones(self.net, [self.zone])
        self.assertTrue(report["complete"])
        self.assertEqual(report["supply_peaks"]["DC 1"]["peak_current_A"], 40)

    def test_review_signature_ignores_labels_positions_currents(self):
        before = study.topology_signature(self.net)
        self.net["components"][0]["label"] = "Renamed bus"
        self.net["components"][0]["position"] = [1, 2]
        self.net["components"][3]["properties"]["trip_coil_1_a"] = 15
        self.assertEqual(study.topology_signature(self.net), before)
        report = study.evaluate_zones(self.net, [self.zone])
        self.assertTrue(report["complete"])
        self.assertEqual(report["supply_peaks"]["DC 1"]["peak_current_A"], 35)

    def test_review_signature_changes_for_status_or_symbol_type(self):
        before = study.topology_signature(self.net)
        for field, value in (("status", "Open"), ("symbol_type", "disconnect")):
            with self.subTest(field=field):
                net = copy.deepcopy(self.net)
                net["components"][3]["properties"][field] = value
                self.assertNotEqual(before, study.topology_signature(net))

    def test_reversed_duplicate_and_reordered_wires_have_same_identity(self):
        original = study.analyze_topology(self.net)
        signature = study.topology_signature(self.net)
        self.net["components"].reverse()
        self.net["connections"] = [[w["to_id"], w["to_port"], w["from_id"], w["from_port"]]
                                   for w in reversed(self.net["connections"])]
        self.net["connections"].append(copy.deepcopy(self.net["connections"][0]))
        self.assertEqual(study.topology_signature(self.net), signature)
        self.assertEqual(study.analyze_topology(self.net), original)

    def test_every_primary_wire_has_one_catalog_owner(self):
        regions = [r for r in study.analyze_topology(gap_netlist()) if r["level"] == "Primary"]
        wires = [json.dumps(w) for r in regions for w in r["connections"]]
        self.assertEqual(len(wires), 3)
        self.assertEqual(len(set(wires)), 3)

    def test_different_dc_supplies_are_not_reported_as_one_supply_peak(self):
        self.zone["actions"][1]["supply"] = "DC 2"
        approve(self.net, self.zone)
        report = study.evaluate_zones(self.net, [self.zone])
        self.assertTrue(report["complete"])
        self.assertEqual(report["supply_peaks"]["DC 1"]["peak_current_A"], 10)
        self.assertEqual(report["supply_peaks"]["DC 2"]["peak_current_A"], 20)

    def test_exact_duplicates_are_deduplicated_per_stage_not_globally(self):
        action = copy.deepcopy(self.zone["actions"][0])
        self.zone["actions"].append(copy.deepcopy(action))
        action["stage"] = "Backup trip"
        self.zone["actions"].append(action)
        approve(self.net, self.zone)
        report = study.evaluate_zones(self.net, [self.zone])
        totals = {r["stage"]: r["peak_current_A"] for r in report["zones"][0]["scenarios"]}
        self.assertEqual(totals, {"Primary trip": 30, "Backup trip": 10})
        self.assertEqual(report["supply_peaks"]["DC 1"]["peak_current_A"], 30)

    def test_same_stage_with_outer_whitespace_is_one_stage(self):
        self.zone["actions"][1]["stage"] = " Primary trip "
        approve(self.net, self.zone)
        report = study.evaluate_zones(self.net, [self.zone])
        self.assertTrue(report["complete"])
        self.assertEqual(len(report["zones"][0]["scenarios"]), 1)
        self.assertEqual(report["supply_peaks"]["DC 1"]["peak_current_A"], 30)

    def test_duplicate_quantity_conflict_is_not_silently_summed(self):
        duplicate = copy.deepcopy(self.zone["actions"][0])
        duplicate["units"] = 3
        self.zone["actions"].append(duplicate)
        self.assert_incomplete(self.net, [self.zone], "inconsistent units")

    def test_missing_supply_or_stage_withholds_final_peaks(self):
        for field in ("supply", "stage"):
            with self.subTest(field=field):
                zone = copy.deepcopy(self.zone)
                zone["actions"][0][field] = "  "
                self.assert_incomplete(self.net, [zone], "missing")

    def test_inrush_and_running_in_separate_stages_are_valid(self):
        self.zone["actions"] = [
            {"component": "cb1", "load": "motor_inrush_a", "stage": "Motor inrush", "supply": "DC 1", "units": 1},
            {"component": "cb1", "load": "motor_run_a", "stage": "Motor run", "supply": "DC 1", "units": 1},
        ]
        approve(self.net, self.zone)
        report = study.evaluate_zones(self.net, [self.zone])
        self.assertTrue(report["complete"])
        self.assertEqual(report["supply_peaks"]["DC 1"]["peak_current_A"], 15)

    def test_motor_is_one_physical_supply_assignment_across_stages(self):
        self.zone["actions"] = [
            {"component": "cb1", "load": "motor_inrush_a", "stage": "Motor inrush", "supply": "DC 1", "units": 1},
            {"component": "cb1", "load": "motor_run_a", "stage": "Motor run", "supply": "DC 2", "units": 1},
        ]
        self.assert_incomplete(self.net, [self.zone], "inconsistent DC supply")

    def test_one_incomplete_zone_withholds_all_final_peaks(self):
        other = copy.deepcopy(self.zone)
        other.update(id="other", confirmed=False)
        report = self.assert_incomplete(self.net, [self.zone, other], "Confirm")
        self.assertTrue(report["zones"][0]["complete"])
        self.assertEqual(report["zones"][0]["scenarios"][0]["peak_current_A"], 30)

    def test_equal_configured_footprints_are_not_mutually_enclosed(self):
        other = copy.deepcopy(self.zone)
        other["id"] = "other"
        report = study.evaluate_zones(self.net, [self.zone, other])
        self.assertTrue(all(not z["enclosed_by"] for z in report["zones"]))
        self.assertEqual(report["supply_peaks"]["DC 1"]["peak_current_A"], 30)

    def test_invalid_selected_currents_are_not_assumed_zero(self):
        for value in (None, 0, -1, True, "10", float("nan"), float("inf"), 10 ** 400):
            with self.subTest(value=repr(value)[:30]):
                net = copy.deepcopy(self.net)
                net["components"][3]["properties"]["trip_coil_1_a"] = value
                self.assert_incomplete(net, [self.zone], "current")

    def test_unassigned_currents_are_not_added(self):
        self.net["components"][3]["properties"]["close_coil_a"] = 900
        self.net["components"][3]["properties"]["motor_inrush_a"] = 800
        report = study.evaluate_zones(self.net, [self.zone])
        self.assertEqual(report["supply_peaks"]["DC 1"]["peak_current_A"], 30)

    def test_accumulation_overflow_is_incomplete_not_infinity(self):
        for component in self.net["components"]:
            if component["id"] in ("cb1", "cb2"):
                component["properties"]["trip_coil_1_a"] = 1e308
        report = self.assert_incomplete(self.net, [self.zone], "numeric range")
        json.dumps(report, allow_nan=False)

    def test_passive_component_cannot_be_an_operating_load(self):
        self.zone["actions"][0]["component"] = "bus"
        self.net["components"][0]["properties"]["trip_coil_1_a"] = 99
        self.assert_incomplete(self.net, [self.zone], "passive")

    def test_no_action_rows_cannot_confirm_zero_current(self):
        self.zone["actions"] = []
        self.assert_incomplete(self.net, [self.zone], "No operating loads")

    def test_deleted_or_wrong_type_boundary_remains_visible(self):
        for boundary in ("deleted", "cb1"):
            with self.subTest(boundary=boundary):
                zone = copy.deepcopy(self.zone)
                zone["boundary_cts"].append(boundary)
                self.assert_incomplete(self.net, [zone], "missing" if boundary == "deleted" else "not a CT")

    def test_unconnected_ct_side_requires_review(self):
        self.net["connections"] = [w for w in self.net["connections"]
                                    if not (w["from_id"] == "ct1" and w["to_id"] == "cb1")]
        self.zone["topology_signature"] = study.topology_signature(self.net)
        self.assert_incomplete(self.net, [self.zone], "unconnected terminal")

    def test_draft_empty_seed_and_rows_can_be_saved_but_not_calculated(self):
        self.zone.update(seed="", confirmed=False, actions=[
            {"component": "", "load": "trip_coil_1_a", "stage": "", "supply": "", "units": 1}])
        study.validate_zones([self.zone])
        self.assert_incomplete(self.net, [self.zone])

    def test_malformed_settings_are_rejected(self):
        changes = [({"id": ""}), ({"name": " "}), ({"kind": "unexpected"}),
                   ({"seed": None}), ({"confirmed": 1}), ({"actions": None}),
                   ({"boundary_cts": ["ct1", "ct1"]}), ({"topology_region": {"core": []}})]
        for change in changes:
            with self.subTest(change=change):
                zone = copy.deepcopy(self.zone)
                zone.update(change)
                with self.assertRaises(ValueError):
                    study.validate_zones([zone])
        for units in (True, 0, -1, 1.5, 101):
            with self.subTest(units=units):
                zone = copy.deepcopy(self.zone)
                zone["actions"][0]["units"] = units
                with self.assertRaises(ValueError):
                    study.validate_zones([zone])
        with self.assertRaises(ValueError):
            study.validate_zones([self.zone, copy.deepcopy(self.zone)])

    def test_malformed_netlists_fail_before_a_result_is_returned(self):
        for mutate in (
            lambda n: n["components"].append(copy.deepcopy(n["components"][0])),
            lambda n: n["connections"].append(["missing", "left", "bus", "right"]),
            lambda n: n["connections"].append(["bus", "", "ct1", "left"]),
            lambda n: n.update(voltage_V=float("nan")),
            lambda n: n.update(voltage_V=-1),
        ):
            net = copy.deepcopy(self.net)
            mutate(net)
            with self.assertRaises(ValueError):
                study.evaluate_zones(net, [self.zone])

    def test_breaker_only_proposals_and_type_aliases(self):
        net = gap_netlist()
        net["components"][0]["type"] = "circuit_breaker"
        net["components"][1]["type"] = "bushing_ct"
        proposed = study.propose_zones(net)
        self.assertEqual({z["seed"] for z in proposed}, {"left", "right"})
        self.assertTrue(all(z["kind"] == "Line" and not z["confirmed"] for z in proposed))
        self.assertEqual(len([r for r in study.analyze_topology(net) if r["level"] == "Primary"]), 3)


if __name__ == "__main__":
    unittest.main()
