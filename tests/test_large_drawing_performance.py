# Checks that the fast paths used on large drawings give exactly the same
# results as the original whole-network methods, and that they stay fast.

"""Large-drawing speed: label placement and protection-study region lookup."""
import copy
import json
import math
import os
import random
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtWidgets import QApplication, QMessageBox

from gft_opto.gui.routing import _segment_hits_rect
from gft_opto.gui.symbols import ConnectionItem, OneLineSymbolItem, _WireSegmentIndex
from gft_opto.logic import protection_study
from gft_opto.logic.protection_study import (
    analyze_topology, evaluate_zones, propose_zones, zone_members,
)

LAYOUTS = Path(__file__).parent / "layouts"
APP = QApplication.instance() or QApplication(sys.argv)


# ---------------------------------------------------------------------------
# Random networks
# ---------------------------------------------------------------------------

_TYPES = ("bus", "breaker", "ct", "ct", "ct", "disconnect", "mos", "junction",
          "xfmr_2w", "incoming_line", "pt", "ground")
_PORTS = ("left", "right", "top", "bottom", None)


def random_netlist(rng: random.Random, size: int) -> dict:
    components = []
    for number in range(size):
        typ = rng.choice(_TYPES)
        props = {}
        if typ in {"breaker", "disconnect", "mos"} and rng.random() < 0.2:
            props["status"] = rng.choice(["open", "Normally Open", "closed"])
        if typ == "breaker":
            props["trip_coil_1_a"] = rng.choice([5, 10, 12.5])
        if typ == "bus" and rng.random() < 0.1:
            props["bus_role"] = "transfer"
        components.append({"id": f"{typ}:{number}", "type": typ,
                           "label": f"{typ.upper()}{number}", "properties": props})
    connections = []
    ids = [c["id"] for c in components]
    for _ in range(int(size * rng.uniform(1.0, 1.6))):
        a, b = rng.choice(ids), rng.choice(ids)
        record = [a, rng.choice(_PORTS), b, rng.choice(_PORTS)]
        connections.append(record if rng.random() < 0.5 else
                           dict(zip(("from_id", "from_port", "to_id", "to_port"), record)))
        if rng.random() < 0.05:  # duplicate and reversed conductors
            connections.append([record[2], record[3], record[0], record[1]])
    return {"components": components, "connections": connections}


def random_zones(rng: random.Random, netlist: dict) -> list[dict]:
    ids = [c["id"] for c in netlist["components"]]
    cts = [i for i in ids if i.startswith("ct:")]
    zones = []
    for number in range(12):
        boundary = rng.sample(cts, k=min(len(cts), rng.randint(0, 4)))
        if rng.random() < 0.2:
            boundary.append(rng.choice(ids))  # a non-CT or the seed itself
        if rng.random() < 0.1:
            boundary.append("ct:missing")
        zones.append({
            "id": f"z{number}", "name": f"Zone {number}",
            "kind": rng.choice(["Bus", "Transformer", "Line", "Manual"]),
            "seed": rng.choice(ids + ["", "missing"]),
            "boundary_cts": sorted(set(boundary)),
            "actions": [{"component": rng.choice(ids), "load": "trip_coil_1_a",
                         "stage": "Primary trip", "supply": "DC 1", "units": 1}],
            "confirmed": rng.random() < 0.3,
        })
    for number, region in enumerate(analyze_topology(netlist)[:6]):
        zones.append({
            "id": f"d{number}", "name": f"Detected {number}", "kind": "Manual",
            "seed": (region["core"] or [""])[0], "boundary_cts": region["boundaries"],
            "actions": [], "confirmed": False, "topology_region": region["signature"],
        })
    return zones


def _both_ways(function, *args):
    fast = function(*copy.deepcopy(args))
    with patch.object(protection_study, "_LOCAL_REGIONS", False):
        full = function(*copy.deepcopy(args))
    return fast, full


def _safe(function, *args):
    try:
        return function(*args)
    except ValueError as error:
        return ("ValueError", str(error))


def _canonical(value):
    return json.dumps(value, sort_keys=True, default=str)


class RegionLookupMatchesWholeNetworkTests(unittest.TestCase):
    """The reachable-subset region lookup must never change a study result."""

    def assert_same_study(self, netlist, zones):
        fast, full = _both_ways(lambda n: _safe(propose_zones, n), netlist)
        self.assertEqual(_canonical(fast), _canonical(full), "propose_zones differs")
        fast, full = _both_ways(lambda n, z: _safe(evaluate_zones, n, z), netlist, zones)
        self.assertEqual(_canonical(fast), _canonical(full), "evaluate_zones differs")
        for zone in zones:
            fast, full = _both_ways(lambda n, z: _safe(zone_members, n, z), netlist, zone)
            self.assertEqual(_canonical(fast), _canonical(full), f"zone_members differs: {zone['id']}")

    def test_random_networks(self):
        rng = random.Random(20261127)
        for trial in range(120):
            netlist = random_netlist(rng, rng.randint(4, 40))
            zones = random_zones(rng, netlist)
            proposals = _safe(propose_zones, netlist)
            if isinstance(proposals, list):
                zones += [dict(zone, id=f"p-{zone['id']}") for zone in proposals[:6]]
            with self.subTest(trial=trial):
                self.assert_same_study(netlist, zones)

    def test_supplied_layouts(self):
        from gft_opto.gui.substation_gui import SubstationGuiMockup

        layouts = sorted(LAYOUTS.glob("*.json"))
        self.assertGreaterEqual(len(layouts), 13)
        with patch.object(QMessageBox, "warning"), patch.object(QMessageBox, "critical"), \
                patch.object(QMessageBox, "information"):
            for path in layouts:
                window = SubstationGuiMockup()
                try:
                    self.assertTrue(window.open_layout(str(path)), path.name)
                    netlist = window._protection_netlist()
                    zones = list(window.protection_zones or [])
                    zones += [dict(zone, id=f"p-{zone['id']}") for zone in propose_zones(netlist)]
                    zones += [{
                        "id": f"d{number}", "name": region["name"], "kind": "Manual",
                        "seed": (region["core"] or [""])[0],
                        "boundary_cts": region["boundaries"], "actions": [],
                        "confirmed": False, "topology_region": region["signature"],
                    } for number, region in enumerate(analyze_topology(netlist))]
                    with self.subTest(layout=path.name):
                        self.assert_same_study(netlist, zones)
                finally:
                    window._saved_snapshot = window._layout_data()
                    window.close()
                    window.deleteLater()
                    APP.processEvents()


# ---------------------------------------------------------------------------
# Wire index used for label placement
# ---------------------------------------------------------------------------

class _Wire:
    def __init__(self, points):
        self._points = [QPointF(x, y) for x, y in points]

    def route_points(self):
        return [QPointF(p) for p in self._points]


def _brute_force_hits(wires, rect):
    for wire in wires:
        points = wire.route_points()
        for a, b in zip(points, points[1:]):
            if _segment_hits_rect(a, b, rect):
                return True
    return False


class WireIndexMatchesBruteForceTests(unittest.TestCase):
    def test_random_segments_and_rectangles(self):
        rng = random.Random(7)
        for trial in range(60):
            wires = []
            for _ in range(rng.randint(0, 25)):
                x, y = rng.uniform(-2000, 2000), rng.uniform(-2000, 2000)
                points = [(x, y)]
                for _ in range(rng.randint(1, 4)):
                    if rng.random() < 0.15:  # diagonal or zero-length piece
                        x, y = x + rng.choice([0, rng.uniform(-400, 400)]), y + rng.uniform(-400, 400)
                    elif rng.random() < 0.5:
                        x += rng.uniform(-900, 900)
                    else:
                        y += rng.uniform(-900, 900)
                    # Land some ends exactly on index cell edges.
                    if rng.random() < 0.2:
                        x = round(x / _WireSegmentIndex.CELL) * _WireSegmentIndex.CELL
                    points.append((x, y))
                wires.append(_Wire(points))
            index = _WireSegmentIndex(wires)
            for _ in range(80):
                if wires and rng.random() < 0.4:
                    # Rectangles that just touch a segment end are the edge case.
                    p = rng.choice(rng.choice(wires).route_points())
                    w, h = rng.uniform(0, 60), rng.uniform(0, 30)
                    rect = QRectF(p.x() - rng.choice([0, w]), p.y() - rng.choice([0, h]), w, h)
                else:
                    rect = QRectF(rng.uniform(-2500, 2500), rng.uniform(-2500, 2500),
                                  rng.uniform(0, 300), rng.uniform(0, 80))
                with self.subTest(trial=trial, rect=rect):
                    self.assertEqual(index.hits(rect), _brute_force_hits(wires, rect))

    def test_huge_and_non_finite_coordinates_are_still_checked(self):
        wires = [_Wire([(-1e9, 5), (1e9, 5)]), _Wire([(0, -1e12), (0, 1e12)]),
                 _Wire([(math.inf, 0), (10, 0)])]
        index = _WireSegmentIndex(wires)
        for rect in (QRectF(100, 0, 10, 10), QRectF(-5, 400, 10, 10),
                     QRectF(2e8, 2, 1, 6), QRectF(-1e10, -1e10, 2e10, 2e10)):
            self.assertEqual(index.hits(rect), _brute_force_hits(wires, rect), rect)


def _brute_force_label(symbol, wires):
    """Label choice as the original code made it: every wire, every candidate."""
    candidates = symbol._candidate_label_rects()
    for rect in candidates:
        scene_rect = symbol._label_scene_rect(rect).adjusted(-4, -4, 4, 4)
        if not _brute_force_hits(wires, scene_rect):
            return rect
    return candidates[0]


class LabelPlacementMatchesBruteForceTests(unittest.TestCase):
    def test_every_label_in_the_supplied_layouts(self):
        from gft_opto.gui.substation_gui import SubstationGuiMockup

        checked = 0
        with patch.object(QMessageBox, "warning"), patch.object(QMessageBox, "critical"), \
                patch.object(QMessageBox, "information"):
            for path in sorted(LAYOUTS.glob("*.json")):
                window = SubstationGuiMockup()
                try:
                    self.assertTrue(window.open_layout(str(path)), path.name)
                    APP.processEvents()
                    scene = window.workspace_scene
                    wires = [i for i in scene.items() if isinstance(i, ConnectionItem)]
                    for item in scene.items():
                        if not isinstance(item, OneLineSymbolItem) or not item.show_name_label:
                            continue
                        if not item._caption():
                            continue
                        expected = _brute_force_label(item, wires)
                        self.assertEqual(item._label_base_rect(), expected,
                                         f"{path.name}: {item.visible_id()}")
                        checked += 1
                finally:
                    window._saved_snapshot = window._layout_data()
                    window.close()
                    window.deleteLater()
                    APP.processEvents()
        self.assertGreater(checked, 200)

    def assert_labels_match(self, scene, step):
        wires = [i for i in scene.items() if isinstance(i, ConnectionItem)]
        checked = 0
        for item in scene.items():
            if isinstance(item, OneLineSymbolItem) and item.show_name_label and item._caption():
                self.assertEqual(item._label_base_rect(), _brute_force_label(item, wires),
                                 f"after {step}: {item.visible_id()}")
                checked += 1
        self.assertGreater(checked, 20, step)

    def test_labels_after_rotate_paste_and_undo(self):
        """These edits place labels once at the end of a batch instead of per symbol."""
        from gft_opto.gui.substation_gui import SubstationGuiMockup

        with patch.object(QMessageBox, "warning"), patch.object(QMessageBox, "critical"), \
                patch.object(QMessageBox, "information"), patch.object(QMessageBox, "question"):
            window = SubstationGuiMockup()
            try:
                self.assertTrue(window.open_layout(str(LAYOUTS / "1U-1L01-Model_1.json")))
                scene, view = window.workspace_scene, window.workspace_view
                for item in scene.items():
                    if isinstance(item, OneLineSymbolItem):
                        item.setSelected(True)
                view.rotate_selected(90)
                APP.processEvents()
                self.assert_labels_match(scene, "rotate")
                window.undo_stack.undo()
                APP.processEvents()
                self.assert_labels_match(scene, "undo rotate")
                self.assertTrue(view.copy_selected())
                self.assertTrue(view.paste_components())
                APP.processEvents()
                self.assert_labels_match(scene, "paste")
                window.undo_stack.undo()
                APP.processEvents()
                self.assert_labels_match(scene, "undo paste")
            finally:
                window._saved_snapshot = window._layout_data()
                window.close()
                window.deleteLater()
                APP.processEvents()
                APP.clipboard().clear()


# ---------------------------------------------------------------------------
# Timing on a generated 1,000-component station
# ---------------------------------------------------------------------------

def build_station(window, buses, feeders):
    """Buses with feeders (bus tap - CT - breaker - CT - line) and tie breakers."""
    from gft_opto.gui.symbols import (
        BusItem, CircuitBreakerItem, CurrentTransformerItem, IncomingLineItem,
    )

    scene = window.workspace_scene

    def add(item, x, y, name, props=None):
        scene.addItem(item)
        item.setPos(x, y)
        item.display_id = name
        if not isinstance(getattr(item, "properties", None), dict):
            item.properties = {}
        item.properties.update(props or {})
        return item

    def wire(a, ap, b, bp):
        scene.addItem(ConnectionItem(a, ap, b, bp))

    span = 160 * feeders + 200
    previous = None
    for b in range(buses):
        x0 = b * (span + 400)
        bus = add(BusItem(), x0 + span / 2, 400, f"BUS-{b + 1}")
        bus.set_end_base_x("left", -span / 2)
        bus.set_end_base_x("right", span / 2)
        for f in range(feeders):
            x = x0 + 100 + f * 160
            tap, _ = bus.add_tap(x - (x0 + span / 2))
            ct1 = add(CurrentTransformerItem(), x, 520, f"CT-{b + 1}-{f + 1}A")
            ct1.set_rotation_deg(90)
            cb = add(CircuitBreakerItem(), x, 640, f"CB-{b + 1}-{f + 1}",
                     {"trip_coil_1_a": 5 + f % 7, "close_coil_a": 9})
            cb.set_rotation_deg(90)
            ct2 = add(CurrentTransformerItem(), x, 760, f"CT-{b + 1}-{f + 1}B")
            ct2.set_rotation_deg(90)
            line = add(IncomingLineItem(), x, 820, f"LINE-{b + 1}-{f + 1}")
            wire(bus, tap, ct1, "left")
            wire(ct1, "right", cb, "left")
            wire(cb, "right", ct2, "left")
            wire(ct2, "right", line, "line")
        if previous is not None:
            ta = add(CurrentTransformerItem(), x0 - 300, 400, f"CT-TIE-{b}A")
            tcb = add(CircuitBreakerItem(), x0 - 200, 400, f"CB-TIE-{b}", {"trip_coil_1_a": 6})
            tb = add(CurrentTransformerItem(), x0 - 100, 400, f"CT-TIE-{b}B")
            wire(previous, "right", ta, "left")
            wire(ta, "right", tcb, "left")
            wire(tcb, "right", tb, "left")
            wire(tb, "right", bus, "left")
        previous = bus


@unittest.skipUnless(os.environ.get("GFT_RUN_TIMING", "1") == "1", "timing checks disabled")
class LargeDrawingTimingTests(unittest.TestCase):
    """Generous limits; before the fix these took 5 s to 50 s each."""

    @classmethod
    def setUpClass(cls):
        from gft_opto.gui.substation_gui import SubstationGuiMockup

        cls.patches = [patch.object(QMessageBox, name)
                       for name in ("warning", "critical", "information", "question")]
        for item in cls.patches:
            item.start()
        cls.window = SubstationGuiMockup()
        build_station(cls.window, 4, 60)
        APP.processEvents()
        cls.count = sum(isinstance(i, OneLineSymbolItem) for i in cls.window.workspace_scene.items())

    @classmethod
    def tearDownClass(cls):
        cls.window._saved_snapshot = cls.window._layout_data()
        cls.window.close()
        cls.window.deleteLater()
        APP.processEvents()
        # PySide6 on the offscreen platform crashes at exit if the clipboard
        # still holds data, as the GUI workflow tests also note.
        APP.clipboard().clear()
        for item in cls.patches:
            item.stop()

    def timed(self, action):
        start = time.perf_counter()
        action()
        APP.processEvents()
        return time.perf_counter() - start

    def test_station_is_large(self):
        self.assertGreater(self.count, 900)

    def test_rotate_one_and_all_symbols(self):
        view = self.window.workspace_view
        scene = self.window.workspace_scene
        symbols_in_scene = [i for i in scene.items() if isinstance(i, OneLineSymbolItem)]
        scene.clearSelection()
        symbols_in_scene[0].setSelected(True)
        self.assertLess(self.timed(lambda: view.rotate_selected(90)), 1.5)
        for item in symbols_in_scene:
            item.setSelected(True)
        self.assertLess(self.timed(lambda: view.rotate_selected(90)), 6.0)
        self.assertLess(self.timed(self.window.undo_stack.undo), 6.0)
        scene.clearSelection()

    def test_copy_paste_and_undo_delete(self):
        view = self.window.workspace_view
        scene = self.window.workspace_scene
        for item in scene.items():
            if isinstance(item, OneLineSymbolItem):
                item.setSelected(True)
        view.copy_selected()
        self.assertLess(self.timed(view.paste_components), 8.0)
        self.assertLess(self.timed(self.window.undo_stack.undo), 8.0)
        scene.clearSelection()

    def test_evaluation(self):
        netlist = self.window._protection_netlist()
        start = time.perf_counter()
        zones = propose_zones(netlist)
        evaluate_zones(netlist, zones)
        self.assertGreater(len(zones), 200)
        self.assertLess(time.perf_counter() - start, 8.0)


if __name__ == "__main__":
    unittest.main()
