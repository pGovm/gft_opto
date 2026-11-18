"""Contracts between the drawing adapter, zone detector and study; not Qt runtime tests.

Scene fixtures implement the actual public item/connection interface. All
adapter, detector, study and arithmetic calls use unmodified production code.
How the GUI reads the study's results is checked by the GUI tests themselves.
"""
import ast
import copy
import json
from pathlib import Path
import random
import unittest

from gft_opto.logic import momentary_calc2 as calculator
from gft_opto.logic import protection_study as study
from gft_opto.logic import protection_zones as detector

PACKAGE = Path(study.__file__).resolve().parents[1]


class Symbol:
    def __init__(self, ident, typ, **props):
        self.instance_id, self.equip_type = ident, typ
        self.label = ident
        self.properties = props
        self._connections = []
    def connections(self):
        return list(self._connections)
    def visible_id(self):
        return self.label


class CT(Symbol):
    def __init__(self, ident, **props):
        super().__init__(ident, 'ct', **props)


class Wire:
    def __init__(self, a, ap, b, bp):
        self.from_item, self.from_port = a, ap
        self.to_item, self.to_port = b, bp
        a._connections.append(self)
        b._connections.append(self)


class Scene:
    def __init__(self, items):
        self.contents = items
    def items(self):
        return list(self.contents)


def fixture():
    a, bus, b = Symbol('cb1', 'breaker', trip_coil_1_a=10, close_coil_a=4), Symbol('bus', 'bus'), Symbol('cb2', 'breaker', trip_coil_1_a=20)
    left, right = CT('ct1'), CT('ct2')
    g1, g2 = Symbol('g1', 'ground'), Symbol('g2', 'ground')
    symbols = [g1, a, left, bus, right, b, g2]
    ports = [('node', 'left'), ('right', 'left'), ('right', 'left'), ('right', 'left'), ('right', 'left'), ('right', 'node')]
    wires = [Wire(symbols[i], p, symbols[i+1], q) for i, (p, q) in enumerate(ports)]
    return Scene(symbols + wires + [object()]), symbols, wires


def confirmed(net, zone):
    value = copy.deepcopy(zone)
    if not value["actions"]:
        value["actions"] = [
            {"component": ident, "load": "trip_coil_1_a", "stage": "Primary trip",
             "supply": "DC 1", "units": 1}
            for ident in value.get("candidate_breakers", [])
        ]
    value['confirmed'] = True
    value['topology_signature'] = study.topology_signature(net)
    value['configuration_signature'] = study.configuration_signature(value)
    return value


class CrossModuleContracts(unittest.TestCase):
    def test_expected_export_names_exist_for_every_gui_logic_import(self):
        modules = {'gft_opto.logic.protection_study': study,
                   'gft_opto.logic.protection_zones': detector,
                   'gft_opto.logic.momentary_calc2': calculator}
        count = 0
        for path in (PACKAGE / 'gui').glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node, ast.ImportFrom) and node.module in modules:
                    for alias in node.names:
                        self.assertTrue(hasattr(modules[node.module], alias.name), (path.name, alias.name))
                        count += 1
        self.assertGreater(count, 10)

    def test_original_scene_and_serialized_topology_catalogs_are_identical(self):
        scene, symbols, _ = fixture()
        net = calculator.netlist_from_scene(scene)
        direct = study.topology_records(detector.identify_zones(symbols, CT))
        self.assertEqual(direct, study.analyze_topology(net))

    def test_randomized_scene_and_netlist_paths_agree_for_all_builtin_types(self):
        rng = random.Random(962734)
        types = ['breaker', 'bus', 'ct', 'ground', 'junction', 'pt', 'xfmr_2w', 'xfmr_3w', 'disconnect', 'mos', 'custom']
        for case in range(250):
            symbols = [(CT(str(i)) if typ == 'ct' else Symbol(str(i), typ, symbol_type=typ))
                       for i, typ in enumerate(types)]
            wires = [Wire(rng.choice(symbols), rng.choice(['left','right','node']),
                          rng.choice(symbols), rng.choice(['left','right','node'])) for _ in range(rng.randrange(25))]
            rng.shuffle(symbols)
            rng.shuffle(wires)
            net = calculator.netlist_from_scene(Scene(symbols + wires))
            with self.subTest(case=case):
                self.assertEqual(study.topology_records(detector.identify_zones(symbols, CT)), study.analyze_topology(net))

    def test_selected_topology_region_survives_json_and_calculation(self):
        scene, _, _ = fixture()
        net = calculator.netlist_from_scene(scene)
        region = next(r for r in study.analyze_topology(net) if r['core'] == ['bus'])
        zone = study.propose_zones(net)[0]
        zone['topology_region'] = region['signature']
        zone = confirmed(net, zone)
        before = study.evaluate_zones(net, [zone])
        payload = json.loads(json.dumps({'version': 1, 'zones': [zone], 'netlist': net}, allow_nan=False))
        study.validate_zones(payload['zones'])
        after = study.evaluate_zones(payload['netlist'], payload['zones'])
        self.assertEqual(before, after)
        self.assertTrue(after['complete'])
        self.assertEqual(after['supply_peaks']['DC 1']['peak_current_A'], 30)

    def test_every_detected_footprint_can_be_resolved_for_display(self):
        scene, _, _ = fixture()
        net = calculator.netlist_from_scene(scene)
        for region in study.analyze_topology(net):
            zone = {'id': region['id'], 'name': 'Selected region', 'kind': 'Manual',
                    'seed': '', 'boundary_cts': region['boundaries'], 'actions': [],
                    'confirmed': False, 'topology_region': region['signature']}
            members, _ = study.zone_members(net, zone)
            self.assertEqual(members, region['members'])

    def test_invalidated_approval_withholds_final_values_in_gui_report_schema(self):
        scene, _, _ = fixture()
        net = calculator.netlist_from_scene(scene)
        zone = confirmed(net, study.propose_zones(net)[0])
        for edit in ('quantity', 'supply', 'status', 'wire'):
            n, z = copy.deepcopy(net), copy.deepcopy(zone)
            if edit == 'quantity': z['actions'][0]['units'] = 2
            elif edit == 'supply': z['actions'][0]['supply'] = 'DC 2'
            elif edit == 'status': n['components'][1]['properties']['status'] = 'Open'
            else: n['connections'].pop()
            with self.subTest(edit=edit):
                report = study.evaluate_zones(n, [z])
                self.assertFalse(report['complete'])
                self.assertEqual(report['supply_peaks'], {})
                self.assertTrue(report['zones'][0]['warnings'])

    def test_current_edit_recalculates_without_changing_topology_identity(self):
        scene, symbols, _ = fixture()
        net = calculator.netlist_from_scene(scene)
        zone = confirmed(net, study.propose_zones(net)[0])
        symbols[1].properties['trip_coil_1_a'] = 15
        updated = calculator.netlist_from_scene(scene)
        self.assertEqual(study.topology_signature(net), study.topology_signature(updated))
        report = study.evaluate_zones(updated, [zone])
        self.assertTrue(report['complete'])
        self.assertEqual(report['supply_peaks']['DC 1']['peak_current_A'], 35)

    def test_wire_only_region_retains_exact_terminal_identity_after_json(self):
        a, b = CT('a'), CT('b')
        first, second = Wire(a, 'left', b, 'left'), Wire(a, 'right', b, 'right')
        net = calculator.netlist_from_scene(Scene([a, b, first, second]))
        regions = [r for r in study.analyze_topology(net) if r['level'] == 'Primary']
        self.assertEqual(len(regions), 2)
        for region in regions:
            zone = {'id': region['id'], 'name': 'Wire only', 'kind': 'Manual', 'seed': '',
                    'boundary_cts': region['boundaries'], 'topology_region': region['signature'],
                    'actions': [], 'confirmed': False}
            report = study.evaluate_zones(net, json.loads(json.dumps([zone])))
            row = report['zones'][0]
            self.assertEqual(row['connections'], region['connections'])
            self.assertTrue(row['wire_only'])
            self.assertEqual(report['supply_peaks'], {})

    def test_multiple_supplies_and_stages_reach_separate_gui_report_fields(self):
        scene, _, _ = fixture()
        net = calculator.netlist_from_scene(scene)
        zone = study.propose_zones(net)[0]
        zone['actions'] = [
            {'component': ident, 'load': 'trip_coil_1_a', 'stage': 'Primary trip',
             'supply': 'DC 1', 'units': 1}
            for ident in zone['candidate_breakers']
        ]
        zone['actions'][1]['supply'] = 'DC 2'
        zone['actions'].append({'component': 'cb1', 'load': 'close_coil_a', 'stage': 'Reclose', 'supply': 'DC 1', 'units': 3})
        report = study.evaluate_zones(net, [confirmed(net, zone)])
        self.assertTrue(report['complete'])
        self.assertEqual({k: v['peak_current_A'] for k,v in report['supply_peaks'].items()}, {'DC 1': 12, 'DC 2': 20})
        self.assertEqual([s['supplies'] for s in report['zones'][0]['scenarios']], [{'DC 1': 10, 'DC 2': 20}, {'DC 1': 12}])

    def test_saved_zone_settings_survive_validation_and_evaluation_unchanged(self):
        scene, _, _ = fixture()
        net = calculator.netlist_from_scene(scene)
        zone = study.propose_zones(net)[0]
        zone['user_note'] = 'Do not lose this note'
        zone['actions'] = [
            {'component': ident, 'load': 'trip_coil_1_a', 'stage': 'Primary trip',
             'supply': 'DC 1', 'units': 1}
            for ident in zone['candidate_breakers']
        ]
        zone['actions'][0]['note'] = 'Operator note'
        data = json.loads(json.dumps([confirmed(net, zone)]))
        study.validate_zones(data)
        before = copy.deepcopy(data)
        study.evaluate_zones(net, data)
        self.assertEqual(data, before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
