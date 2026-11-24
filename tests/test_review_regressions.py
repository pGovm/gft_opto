"""Additional non-Qt regressions found during the v1 re-audit.

These call the real study and scene-netlist adapter. Lightweight scene objects
exercise the adapter protocol only; they are not replacements for Qt GUI tests.
"""
import json
import unittest

from gft_opto.logic import protection_study as study
from gft_opto.logic.momentary_calc2 import netlist_from_scene
from test_protection_study import example


def approve(net, zone):
    if not zone['actions']:
        zone['actions'] = [
            {'component': ident, 'load': 'trip_coil_1_a', 'stage': 'Primary trip',
             'supply': 'DC 1', 'units': 1}
            for ident in zone.get('candidate_breakers', [])
        ]
    zone['confirmed'] = True
    zone['topology_signature'] = study.topology_signature(net)
    zone['configuration_signature'] = study.configuration_signature(zone)
    return zone


class Item:
    def __init__(self, ident, typ='bus'):
        self.instance_id = ident
        self.equip_type = typ
        self.properties = {}


class Wire:
    def __init__(self, a, ap, b, bp):
        self.from_item, self.from_port, self.to_item, self.to_port = a, ap, b, bp


class Scene:
    def __init__(self, items):
        self._items = items
        self.calls = 0

    def items(self):
        self.calls += 1
        return iter(self._items)


class ReviewRegressions(unittest.TestCase):
    def test_missing_configuration_signature_cannot_confirm_a_study(self):
        net = example()
        zone = approve(net, study.propose_zones(net)[0])
        del zone['configuration_signature']
        report = study.evaluate_zones(net, [zone])
        self.assertFalse(report['complete'])
        self.assertEqual(report['supply_peaks'], {})
        self.assertIn('Reconfirm', ' '.join(report['zones'][0]['warnings']))

    def test_empty_configuration_signature_cannot_bypass_review(self):
        net = example()
        zone = approve(net, study.propose_zones(net)[0])
        zone['configuration_signature'] = ''
        zone['actions'][0]['units'] = 3
        report = study.evaluate_zones(net, [zone])
        self.assertFalse(report['complete'])
        self.assertEqual(report['supply_peaks'], {})

    def test_reapproval_restores_missing_signature_without_changing_currents(self):
        net = example()
        zone = approve(net, study.propose_zones(net)[0])
        report = study.evaluate_zones(net, [zone])
        self.assertTrue(report['complete'])
        self.assertEqual(report['supply_peaks']['DC 1']['peak_current_A'], 30)

    def test_scene_netlist_does_not_collapse_colon_delimited_endpoints(self):
        a, b, z = Item('a'), Item('a:b'), Item('z')
        scene = Scene([a, b, z, Wire(a, 'b:c', z, 'left'), Wire(b, 'c', z, 'left')])
        net = netlist_from_scene(scene)
        self.assertEqual(len(net['connections']), 2)
        self.assertEqual(scene.calls, 1)

    def test_scene_netlist_ignores_connections_outside_the_scene(self):
        a, b = Item('a'), Item('b')
        net = netlist_from_scene(Scene([a, Wire(a, 'right', b, 'left')]))
        self.assertEqual(net['connections'], [])
        self.assertEqual([c['id'] for c in net['components']], ['a'])

    def test_scene_netlist_scope_is_object_identity_not_matching_id(self):
        a, b, impostor = Item('a'), Item('b'), Item('b')
        net = netlist_from_scene(Scene([a, b, Wire(a, 'right', impostor, 'left')]))
        self.assertEqual(net['connections'], [])

    def test_scene_netlist_reversed_duplicate_wire_is_deduplicated(self):
        a, b = Item('a'), Item('b')
        net = netlist_from_scene(Scene([a, b, Wire(a, 'right', b, 'left'), Wire(b, 'left', a, 'right')]))
        self.assertEqual(len(net['connections']), 1)

    def test_scene_netlist_dangling_wire_is_ignored(self):
        a = Item('a')
        net = netlist_from_scene(Scene([a, Wire(a, 'right', None, 'left')]))
        self.assertEqual(net['connections'], [])

    def test_scene_netlist_preserves_current_values_without_coercion(self):
        a = Item('breaker', 'breaker')
        a.properties = {'trip_coil_1_a': 12, 'motor_run_a': None}
        net = netlist_from_scene(Scene([a]))
        self.assertEqual(net['components'][0]['properties'], a.properties)
        net['components'][0]['properties']['trip_coil_1_a'] = 99
        self.assertEqual(a.properties['trip_coil_1_a'], 12)

    def test_scene_netlist_is_compatible_with_the_real_detector(self):
        a, c, b = Item('a'), Item('ct', 'ct'), Item('b', 'breaker')
        b.properties['trip_coil_1_a'] = 7
        net = netlist_from_scene(Scene([a, c, b, Wire(a, 'right', c, 'left'), Wire(c, 'right', b, 'left')]))
        regions = study.analyze_topology(net)
        self.assertEqual(len([r for r in regions if r['level'] == 'Primary']), 2)
        json.dumps(regions, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
