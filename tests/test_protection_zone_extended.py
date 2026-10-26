"""Independent synthetic checks for the delivered v2 detector/study source.

Run with the project's source on PYTHONPATH. Uses only the Python standard
library and the production gft_opto modules. No production functions are mocked.
The graph oracle uses explicit adjacency plus DFS, not production union/find.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
import copy
import json
import random
import unittest

from gft_opto.logic import protection_zones as detector
from gft_opto.logic import protection_study as study


class Item:
    def __init__(self, ident):
        self.instance_id = ident
        self._connections = []

    def connections(self):
        return list(self._connections)


class CT(Item):
    pass


class Wire:
    def __init__(self, a, ap, b, bp):
        self.from_item, self.from_port = a, ap
        self.to_item, self.to_port = b, bp
        for item in (a, b):
            if item is not None:
                item._connections.append(self)


def edge_key(w):
    ends = [(w.from_item.instance_id, w.from_port), (w.to_item.instance_id, w.to_port)]
    return tuple(sorted(ends, key=lambda e: (e[0], '' if e[1] is None else e[1])))


def footprint(zone):
    return (frozenset(i.instance_id for i in zone.core_items),
            frozenset(i.instance_id for i in zone.boundary_items),
            frozenset(edge_key(w) for w in zone.connection_items))


def identities(zones):
    return {zone.zone_id: footprint(zone) for zone in zones}


def oracle(items):
    """Return complete component/conductor footprints and retained wire objects."""
    registry = {i.instance_id: i for i in items}
    scope = {id(i) for i in items}
    originals = defaultdict(set)
    for item in items:
        for wire in item.connections():
            a, b = wire.from_item, wire.to_item
            if a is not item and b is not item:
                continue
            if a is None or b is None or id(a) not in scope or id(b) not in scope:
                continue
            originals[edge_key(wire)].add(wire)

    adjacency = defaultdict(set)

    def vertex(endpoint, key):
        ident, port = endpoint
        if not isinstance(registry[ident], CT):
            return ('equipment', ident)
        # Unnamed CT sides cannot be assumed to share a terminal.
        return ('terminal', ident, ('named', port) if port is not None else ('unnamed', key))

    for item in items:
        if not isinstance(item, CT):
            adjacency[('equipment', item.instance_id)]
    for key in originals:
        a, b = vertex(key[0], key), vertex(key[1], key)
        adjacency[a].add(b)
        adjacency[b].add(a)

    visited, expected = set(), []
    for start in list(adjacency):
        if start in visited:
            continue
        stack, component = [start], set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(adjacency[current] - component)
        visited.update(component)
        core = frozenset(v[1] for v in component if v[0] == 'equipment')
        bounds = frozenset(v[1] for v in component if v[0] == 'terminal')
        edges = frozenset(key for key in originals if vertex(key[0], key) in component)
        wires = set().union(*(originals[key] for key in edges)) if edges else set()
        expected.append(((core, bounds, edges), wires))
    return expected


def random_graph(seed):
    r = random.Random(seed)
    n = r.randint(1, 35)
    items = [(CT if r.random() < .45 else Item)(f'item:{i}') for i in range(n)]
    wires = []
    for _ in range(r.randint(0, 75)):
        a, b = r.choice(items), r.choice(items)
        ap, bp = r.choice(['left', 'right', 'aux', None]), r.choice(['left', 'right', 'aux', None])
        w = Wire(a, ap, b, bp)
        wires.append(w)
        if r.random() < .18:
            wires.append(Wire(b, bp, a, ap))
    outside = Item('outside')
    Wire(items[0], 'left', outside, 'right')
    Wire(items[-1], 'left', None, None)
    if n > 2:
        # A completely unrelated stale list entry must not be treated as an edge.
        foreign = Wire(outside, 'left', Item('other outside'), 'right')
        items[1]._connections.append(foreign)
    return items, wires


def radial_netlist(n=4):
    components = [{'id': 'bus', 'type': 'bus', 'properties': {}}]
    connections = []
    for index in range(n):
        ct, cb, ground = f'ct{index}', f'cb{index}', f'g{index}'
        components += [
            {'id': ct, 'type': 'ct', 'properties': {}},
            {'id': cb, 'type': 'breaker', 'properties': {
                'trip_coil_1_a': 3 + index, 'trip_coil_2_a': 5 + index,
                'close_coil_a': 7 + index, 'motor_inrush_a': 13 + index,
                'motor_run_a': 2 + index}},
            {'id': ground, 'type': 'ground', 'properties': {}},
        ]
        connections += [
            ['bus', f'tap{index}', ct, 'left'], [ct, 'right', cb, 'left'],
            [cb, 'right', ground, 'node'],
        ]
    return {'system_name': 'Synthetic radial fixture', 'voltage_V': 125.,
            'components': components, 'connections': connections}


def approve(net, zone):
    zone['confirmed'] = True
    zone['topology_signature'] = study.topology_signature(net)
    zone['configuration_signature'] = study.configuration_signature(zone)
    return zone


def event(net, ident, actions):
    return approve(net, {
        'id': ident, 'name': ident, 'kind': 'Bus', 'seed': 'bus',
        'boundary_cts': [c['id'] for c in net['components'] if c['type'] == 'ct'],
        'actions': actions, 'confirmed': False,
    })


def action(cb='cb0', amps='trip_coil_1_a', stage='Primary trip', supply='DC 1', units=1):
    return {'component': cb, 'load': amps, 'stage': stage, 'supply': supply, 'units': units}


class IndependentTopologyChecks(unittest.TestCase):
    def assert_matches_oracle(self, items):
        expected = oracle(items)
        zones = detector.identify_zones(items, CT)
        self.assertEqual(Counter(footprint(z) for z in zones), Counter(f for f, _ in expected))
        self.assertEqual(Counter(w for z in zones for w in z.connection_items),
                         Counter(w for _, wires in expected for w in wires))
        self.assertEqual(Counter(i for z in zones for i in z.core_items),
                         Counter(i for i in items if not isinstance(i, CT)))
        return zones

    def test_1000_random_topologies_against_independent_dfs(self):
        for seed in range(1000):
            with self.subTest(seed=seed):
                items, _ = random_graph(seed)
                self.assert_matches_oracle(items)

    def test_all_32768_graphs_of_a_two_ct_six_terminal_model(self):
        # Exhaustive simple undirected graphs over six explicit terminals.
        # This finite model is not an enumeration of every possible circuit.
        endpoints = [(0, 'p'), (1, 'left'), (1, 'right'),
                     (2, 'left'), (2, 'right'), (3, 'p')]
        possible = list(combinations(endpoints, 2))
        for mask in range(1 << len(possible)):
            with self.subTest(mask=mask):
                items = [Item('a'), CT('ct1'), CT('ct2'), Item('b')]
                for index, ((a, ap), (b, bp)) in enumerate(possible):
                    if mask & (1 << index):
                        Wire(items[a], ap, items[b], bp)
                self.assert_matches_oracle(items)

    def test_200_order_direction_generator_and_recreation_invariances(self):
        for seed in range(200):
            with self.subTest(seed=seed):
                items, _ = random_graph(seed)
                baseline = detector.identify_zones(items, CT)
                expected = identities(baseline + detector.generate_composite_zones(baseline))
                r = random.Random(seed + 2000)
                r.shuffle(items)
                for item in items:
                    r.shuffle(item._connections)
                changed = detector.identify_zones(iter(items), iter([CT]))
                self.assertEqual(expected, identities(changed + detector.generate_composite_zones(changed)))
                clones = {i.instance_id: type(i)(i.instance_id) for i in items}
                for fp, _ in oracle(items):
                    for (a, ap), (b, bp) in fp[2]:
                        Wire(clones[b], bp, clones[a], ap)
                changed = detector.identify_zones(clones.values(), CT)
                self.assertEqual(expected, identities(changed + detector.generate_composite_zones(changed)))

    def test_200_composite_footprints_and_incidence_against_pair_oracle(self):
        for seed in range(200):
            with self.subTest(seed=seed):
                items, _ = random_graph(seed)
                primary = detector.identify_zones(items, CT)
                expected = set()
                for i, a in enumerate(primary):
                    for b in primary[i + 1:]:
                        shared = a.boundary_items & b.boundary_items
                        internal = {ct for ct in shared if all(
                            z is a or z is b or ct not in z.boundary_items for z in primary)}
                        if not internal:
                            continue
                        core = a.core_items | b.core_items | internal
                        bounds = (a.boundary_items | b.boundary_items) - core
                        keys = {edge_key(w) for w in a.connection_items | b.connection_items}
                        expected.add((frozenset(x.instance_id for x in core),
                                      frozenset(x.instance_id for x in bounds), frozenset(keys)))
                composites = detector.generate_composite_zones(primary)
                self.assertEqual(Counter(footprint(z) for z in composites), Counter(expected))
                all_zones = primary + composites
                parents = detector.find_enclosed_zones(all_zones)
                for z in all_zones:
                    own = footprint(z)
                    members, keys = own[0] | own[1], own[2]
                    expected_parents = []
                    for other in all_zones:
                        fp = footprint(other)
                        if members <= fp[0] | fp[1] and keys <= fp[2] and (
                                members != fp[0] | fp[1] or keys != fp[2]):
                            expected_parents.append(other.zone_id)
                    self.assertEqual(parents[z.zone_id], sorted(expected_parents))
                outer = detector.independent_zones(all_zones, parents)
                self.assertEqual({z.zone_id for z in outer}, {z.zone_id for z in all_zones if not parents[z.zone_id]})

    def test_long_ct_chain_preserves_all_299_wire_only_regions(self):
        items = [CT(f'ct{i}') for i in range(300)]
        wires = [Wire(a, 'right', b, 'left') for a, b in zip(items, items[1:])]
        zones = self.assert_matches_oracle(items)
        self.assertEqual(len(zones), 299)
        self.assertTrue(all(z.is_wire_only for z in zones))
        self.assertEqual(len(detector.generate_composite_zones(zones)), 298)
        self.assertEqual(set().union(*(z.connection_items for z in zones)), set(wires))

    def test_parallel_named_and_unnamed_terminal_regions_remain_distinct(self):
        a, b = CT('a'), CT('b')
        for ap, bp in [('left', 'left'), ('right', 'right'), (None, None)]:
            Wire(a, ap, b, bp)
        zones = self.assert_matches_oracle([a, b])
        self.assertEqual(len(zones), 3)
        self.assertTrue(all(not v for v in detector.find_enclosed_zones(zones).values()))


class IndependentStudyChecks(unittest.TestCase):
    def test_250_random_multievent_totals_against_independent_arithmetic(self):
        fields = ['trip_coil_1_a', 'trip_coil_2_a', 'close_coil_a']
        stages = ['Primary trip', 'Backup trip', 'Reclose']
        for seed in range(250):
            with self.subTest(seed=seed):
                r = random.Random(70000 + seed)
                n = r.randint(1, 10)
                net = radial_netlist(n)
                by_id = {c['id']: c for c in net['components']}
                for c in net['components']:
                    if c['type'] == 'breaker':
                        for field in fields:
                            c['properties'][field] = r.randint(1, 50)
                supply_map = {(f'cb{i}', f): r.choice(['DC 1', 'DC 2', 'DC 3'])
                              for i in range(n) for f in fields}
                expected_peak, expected_events, zones = {}, {}, []
                for e in range(r.randint(1, 5)):
                    rows, used, totals = [], set(), defaultdict(int)
                    for _ in range(r.randint(1, 20)):
                        cb, field, stage = f'cb{r.randrange(n)}', r.choice(fields), r.choice(stages)
                        if (cb, field, stage) in used:
                            continue
                        used.add((cb, field, stage))
                        units = r.choice([1, 3])
                        supply = supply_map[(cb, field)]
                        row = action(cb, field, stage, supply, units)
                        rows.append(row)
                        if r.random() < .4:
                            rows.append(copy.deepcopy(row))
                        totals[(stage, supply)] += by_id[cb]['properties'][field] * units
                    ident = f'event{e}'
                    zones.append(event(net, ident, rows))
                    expected_events[ident] = dict(totals)
                    for (_, supply), amps in totals.items():
                        expected_peak[supply] = max(expected_peak.get(supply, 0), amps)
                original = copy.deepcopy((net, zones))
                report = study.evaluate_zones(net, zones)
                self.assertTrue(report['complete'], report)
                self.assertEqual({k: v['peak_current_A'] for k, v in report['supply_peaks'].items()}, expected_peak)
                for row in report['zones']:
                    actual = {(s['stage'], supply): amps for s in row['scenarios']
                              for supply, amps in s['supplies'].items()}
                    self.assertEqual(actual, expected_events[row['id']])
                self.assertEqual(original, (net, zones))
                json.dumps(report, allow_nan=False)

    def test_review_withholds_peaks_for_32_assignment_and_topology_mutations(self):
        net = radial_netlist(2)
        z = event(net, 'bus event', [action(), action('cb1')])
        zone_changes = [
            lambda z: z.update(confirmed=False),
            lambda z: z.pop('topology_signature'),
            lambda z: z.pop('configuration_signature'),
            lambda z: z.update(configuration_signature=''),
            lambda z: z['actions'][0].update(units=3),
            lambda z: z['actions'][0].update(stage='Reclose'),
            lambda z: z['actions'][0].update(supply='Other DC'),
            lambda z: z['actions'][0].update(component='missing'),
            lambda z: z['actions'][0].update(load='close_coil_a'),
            lambda z: z.update(boundary_cts=[]),
            lambda z: z.update(seed='cb0'),
            lambda z: z.update(kind='Manual'),
        ]
        cases = []
        for modify in zone_changes:
            nn, zz = copy.deepcopy((net, z))
            modify(zz)
            cases.append((nn, zz))
        for status in ['Open', 'Maintenance', 'changed', '']:
            nn, zz = copy.deepcopy((net, z))
            next(c for c in nn['components'] if c['id'] == 'cb0')['properties']['status'] = status
            cases.append((nn, zz))
        for invalid in [None, False, True, -1, 0, float('nan'), float('inf'), float('-inf'),
                        '10', [], {}, 10**1000]:
            nn, zz = copy.deepcopy((net, z))
            next(c for c in nn['components'] if c['id'] == 'cb0')['properties']['trip_coil_1_a'] = invalid
            cases.append((nn, zz))
        for i in range(4):
            nn, zz = copy.deepcopy((net, z))
            nn['connections'].pop(i)
            cases.append((nn, zz))
        self.assertEqual(len(cases), 32)
        for i, (nn, zz) in enumerate(cases):
            with self.subTest(case=i):
                report = study.evaluate_zones(nn, [zz])
                self.assertFalse(report['complete'])
                self.assertEqual(report['supply_peaks'], {})

    def test_current_edits_recalculate_without_changing_region_or_review_identity(self):
        net = radial_netlist(2)
        zone = event(net, 'bus event', [action(), action('cb1')])
        catalog, sig = study.analyze_topology(net), study.topology_signature(net)
        before = study.evaluate_zones(net, [zone])
        next(c for c in net['components'] if c['id'] == 'cb0')['properties']['trip_coil_1_a'] = 19
        after = study.evaluate_zones(net, [zone])
        self.assertTrue(after['complete'])
        self.assertEqual(before['supply_peaks']['DC 1']['peak_current_A'], 7)
        self.assertEqual(after['supply_peaks']['DC 1']['peak_current_A'], 23)
        self.assertEqual(sig, study.topology_signature(net))
        self.assertEqual(catalog, study.analyze_topology(net))

    def test_inrush_run_separate_stages_and_overlap_rejection(self):
        net = radial_netlist(1)
        rows = [action(amps='motor_inrush_a', stage='Motor inrush'),
                action(amps='motor_run_a', stage='Motor run')]
        zone = event(net, 'motor', rows)
        report = study.evaluate_zones(net, [zone])
        self.assertTrue(report['complete'])
        self.assertEqual(report['supply_peaks']['DC 1']['peak_current_A'], 13)
        rows[1]['stage'] = 'Motor inrush'
        approve(net, zone)
        report = study.evaluate_zones(net, [zone])
        self.assertFalse(report['complete'])
        self.assertEqual(report['supply_peaks'], {})

    def test_equivalent_netlist_reordering_preserves_catalog_and_peaks(self):
        net = radial_netlist(6)
        zone = event(net, 'event', [action(f'cb{i}') for i in range(6)])
        before = study.evaluate_zones(net, [zone])
        after_net = copy.deepcopy(net)
        after_net['components'].reverse()
        after_net['connections'] = [[b, bp, a, ap] for a, ap, b, bp in reversed(after_net['connections'])]
        after_net['connections'] += copy.deepcopy(after_net['connections'][:4])
        after = study.evaluate_zones(after_net, [zone])
        self.assertTrue(after['complete'])
        self.assertEqual(before, after)

    def test_zero_configured_events_never_implicitly_calculates_catalog(self):
        report = study.evaluate_zones(radial_netlist(6), [])
        self.assertFalse(report['complete'])
        self.assertEqual(report['zones'], [])
        self.assertEqual(report['supply_peaks'], {})
        self.assertGreater(len(report['topology_regions']), 0)
        self.assertTrue(all(not r['calculated'] for r in report['topology_regions']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
