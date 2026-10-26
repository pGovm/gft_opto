"""Regression contracts for the pulled breaker-settings revision and v3 merge.

These call real study/settings functions, not Qt substitutes. Approval changes
are deliberate: hardware multipliers and supply edits require fresh review;
current-only edits still recalculate without changing the review signature.
"""
import copy
import json
import unittest
from pathlib import Path
from gft_opto.logic import protection_study as study
from gft_opto.logic.breaker_settings import breaker_settings, resolve_breaker_action
from test_protection_study import example


def approve(net, zone):
    zone['confirmed'] = True
    zone['topology_signature'] = study.topology_signature(net)
    zone['configuration_signature'] = study.configuration_signature(zone)
    return zone


def modern_case():
    net = example()
    cb = next(c for c in net['components'] if c['id'] == 'cb1')
    cb['properties'].update(coil_current_basis='per_mechanism',
        mechanism_arrangement='independent', pole_count=3,
        trip_coil_1_supply='Battery A', motor_supply='Motor DC')
    zone = study.propose_zones(net)[0]
    zone['actions'] = [{'component': 'cb1', 'load': 'trip_coil_1_a',
                       'stage': 'Primary trip', 'operation': 'all_poles'}]
    return net, cb, approve(net, zone)


class TeammateMerge(unittest.TestCase):
    def test_all_poles_uses_equipment_multiplier(self):
        net, cb, zone = modern_case()
        result = study.evaluate_zones(net, [zone])
        self.assertTrue(result['complete'])
        self.assertEqual(result['supply_peaks']['Battery A']['peak_current_A'], 30)

    def test_one_pole_reapproval_uses_one_mechanism(self):
        net, cb, zone = modern_case()
        zone['actions'][0]['operation'] = 'one_pole'
        approve(net, zone)
        self.assertEqual(study.evaluate_zones(net, [zone])['supply_peaks']['Battery A']['peak_current_A'], 10)

    def test_equipment_multiplier_edits_require_reconfirmation(self):
        for key, value in [('pole_count', 2), ('coil_current_basis', 'whole_breaker'),
                           ('mechanism_arrangement', 'shared')]:
            with self.subTest(key=key):
                net, cb, zone = modern_case()
                cb['properties'][key] = value
                result = study.evaluate_zones(net, [zone])
                self.assertFalse(result['complete'])
                self.assertEqual(result['supply_peaks'], {})
                self.assertIn('Reconfirm', ' '.join(result['zones'][0]['warnings']))

    def test_equipment_supply_edit_requires_reconfirmation(self):
        net, cb, zone = modern_case()
        cb['properties']['trip_coil_1_supply'] = 'Battery B'
        self.assertFalse(study.evaluate_zones(net, [zone])['complete'])
        approve(net, zone)
        self.assertEqual(study.evaluate_zones(net, [zone])['supply_peaks']['Battery B']['peak_current_A'], 30)

    def test_current_edit_recalculates_without_reapproval(self):
        net, cb, zone = modern_case()
        old = study.topology_signature(net)
        cb['properties']['trip_coil_1_a'] = 12
        self.assertEqual(study.topology_signature(net), old)
        self.assertEqual(study.evaluate_zones(net, [zone])['supply_peaks']['Battery A']['peak_current_A'], 36)

    def test_motor_is_not_multiplied_by_independent_poles(self):
        net, cb, zone = modern_case()
        zone['actions'] = [{'component': 'cb1', 'load': 'motor_inrush_a',
                            'stage': 'Motor inrush', 'operation': 'device'}]
        approve(net, zone)
        self.assertEqual(study.evaluate_zones(net, [zone])['supply_peaks']['Motor DC']['peak_current_A'], 15)

    def test_one_pole_with_shared_mechanism_is_incomplete(self):
        net, cb, zone = modern_case()
        cb['properties']['mechanism_arrangement'] = 'shared'
        zone['actions'][0]['operation'] = 'one_pole'
        approve(net, zone)
        self.assertFalse(study.evaluate_zones(net, [zone])['complete'])

    def test_modern_rows_without_legacy_fields_survive_json(self):
        net, cb, zone = modern_case()
        restored = json.loads(json.dumps([zone]))
        study.validate_zones(restored)
        self.assertTrue(study.evaluate_zones(net, restored)['complete'])
        self.assertNotIn('supply', restored[0]['actions'][0])
        self.assertNotIn('units', restored[0]['actions'][0])

    def test_evaluation_does_not_add_resolution_fields_to_saved_rows(self):
        net, cb, zone = modern_case()
        saved = copy.deepcopy((net, zone))
        study.evaluate_zones(net, [zone])
        self.assertEqual((net, zone), saved)

    def test_legacy_rows_keep_their_totals_without_equipment_settings(self):
        net = example()
        zone = approve(net, study.propose_zones(net)[0])
        self.assertEqual(study.evaluate_zones(net, [zone])['supply_peaks']['DC 1']['peak_current_A'], 30)

    def test_conflicting_legacy_supply_is_not_invented_as_dc1(self):
        component = {'id': 'cb1', 'type': 'breaker', 'properties': {}}
        zones = [{'actions': [
            {'component': 'cb1', 'load': 'trip_coil_1_a', 'supply': 'A', 'units': 1},
            {'component': 'cb1', 'load': 'trip_coil_1_a', 'supply': 'B', 'units': 1}]}]
        self.assertEqual(breaker_settings(component, zones)['trip_coil_1_supply'], '')
        with self.assertRaisesRegex(ValueError, 'supply'):
            resolve_breaker_action(component, {'load':'trip_coil_1_a', 'operation':'all_poles'}, zones)

    def test_explicit_equipment_supply_resolves_legacy_ambiguity(self):
        component = {'id': 'cb1', 'type': 'breaker', 'properties': {'trip_coil_1_supply':'Reviewed A'}}
        zones = [{'actions': [
            {'component': 'cb1', 'load': 'trip_coil_1_a', 'supply': 'A', 'units': 1},
            {'component': 'cb1', 'load': 'trip_coil_1_a', 'supply': 'B', 'units': 1}]}]
        self.assertEqual(resolve_breaker_action(component, {'load':'trip_coil_1_a', 'operation':'all_poles'}, zones), ('Reviewed A', 1))

    def test_unsupported_legacy_multiplier_is_not_silently_discarded(self):
        net, cb, zone = modern_case()
        zone['actions'] = [{'component':'cb1', 'load':'trip_coil_1_a', 'stage':'Primary trip', 'supply':'Battery A', 'units':2}]
        approve(net, zone)
        self.assertFalse(study.evaluate_zones(net, [zone])['complete'])

    def test_legacy_motor_multiplier_requires_review_when_converted(self):
        net, cb, zone = modern_case()
        zone['actions'] = [{'component':'cb1', 'load':'motor_inrush_a', 'stage':'Motor', 'supply':'Motor DC', 'units':3}]
        approve(net, zone)
        self.assertFalse(study.evaluate_zones(net, [zone])['complete'])

    def test_modern_missing_component_reports_incomplete_not_keyerror(self):
        net, cb, zone = modern_case()
        zone['actions'][0]['component'] = 'missing'
        approve(net, zone)
        result = study.evaluate_zones(net, [zone])
        self.assertFalse(result['complete'])
        self.assertEqual(result['supply_peaks'], {})

    def test_missing_configuration_approval_blocks_modern_rows(self):
        net, cb, zone = modern_case()
        del zone['configuration_signature']
        self.assertFalse(study.evaluate_zones(net, [zone])['complete'])

    def test_hardware_settings_do_not_change_detected_regions(self):
        net, cb, zone = modern_case()
        before = study.analyze_topology(net)
        cb['properties'].update(pole_count=2, trip_coil_1_supply='Changed')
        self.assertEqual(study.analyze_topology(net), before)

    def test_new_settings_invalid_values_withhold_final_peaks(self):
        for key, value in [('pole_count', True), ('pole_count', 4),
                           ('coil_current_basis','unknown'), ('mechanism_arrangement','unknown'),
                           ('trip_coil_1_supply','')]:
            with self.subTest(key=key, value=value):
                net, cb, zone = modern_case()
                cb['properties'][key] = value
                approve(net, zone)
                result = study.evaluate_zones(net, [zone])
                self.assertFalse(result['complete'])
                self.assertEqual(result['supply_peaks'], {})

    def test_modern_duplicate_rows_count_once_per_stage(self):
        net, cb, zone = modern_case()
        zone['actions'].append(copy.deepcopy(zone['actions'][0]))
        approve(net, zone)
        self.assertEqual(study.evaluate_zones(net, [zone])['supply_peaks']['Battery A']['peak_current_A'], 30)

    def test_new_operations_stay_separate_across_stages(self):
        net, cb, zone = modern_case()
        zone['actions'].append({'component':'cb1', 'load':'trip_coil_1_a',
                                'stage':'Backup trip', 'operation':'one_pole'})
        approve(net, zone)
        result = study.evaluate_zones(net, [zone])
        self.assertEqual(result['supply_peaks']['Battery A']['peak_current_A'], 30)
        self.assertEqual(sorted(s['peak_current_A'] for s in result['zones'][0]['scenarios']), [10, 30])

class SuppliedLayout(unittest.TestCase):
    def fixture(self):
        layout = json.loads((Path(__file__).parent / "layouts/two_zone_demo.json").read_text())
        net = {"components": [{"id": c["id"], "type": c["symbol"],
                "label": c["display_id"] or c["name"], "properties": copy.deepcopy(c["properties"])}
                for c in layout["components"]], "connections": copy.deepcopy(layout["connections"])}
        return net, copy.deepcopy(layout["zones"])

    def test_old_demo_confirmation_needs_explicit_review(self):
        net, zones = self.fixture()
        before = copy.deepcopy((net, zones))
        result = study.evaluate_zones(net, zones)
        self.assertFalse(result["complete"])
        self.assertEqual(result["supply_peaks"], {})
        self.assertEqual((net, zones), before)

    def test_reviewed_demo_currents_match_hand_sums(self):
        net, zones = self.fixture()
        for zone in zones:
            approve(net, zone)
        result = study.evaluate_zones(net, zones)
        self.assertTrue(result["complete"], result)
        rows = {row["id"]: {s["stage"]: s["supplies"] for s in row["scenarios"]}
                for row in result["zones"]}
        self.assertEqual(rows["transformer_zone"]["Primary trip"], {"DC 1": 10+12, "DC 2": 8+9})
        self.assertEqual(rows["bus_zone"]["Primary trip"], {"DC 1": 12+6+7, "DC 2": 9+5+6})
        self.assertEqual(rows["bus_zone"]["Reclose"], {"DC 1": 10})
        self.assertEqual(rows["bus_zone"]["Motor inrush"], {"DC 1": 18})
        self.assertEqual(rows["bus_zone"]["Motor run"], {"DC 1": 3})
        self.assertEqual(result["supply_peaks"]["DC 1"]["peak_current_A"], 25)
        self.assertEqual(result["supply_peaks"]["DC 2"]["peak_current_A"], 20)

    def test_demo_explicit_boundaries_have_expected_equipment(self):
        net, zones = self.fixture()
        for zone in zones:
            members, warnings = study.zone_members(net, zone)
            if zone["id"] == "transformer_zone":
                self.assertEqual(set(members), {"ct_1", "ct_2", "xfmr_2w_1"})
            else:
                self.assertEqual(set(members), {"bus_1", "ct_3", "ct_4", "ct_5"})
            self.assertEqual(warnings, [])


if __name__ == '__main__':
    unittest.main()
