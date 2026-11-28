"""Validate (circuit check): open terminals, dangling wires and short circuits."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest

from PySide6.QtWidgets import QApplication, QGraphicsScene

from gft_opto.gui import messages
from gft_opto.gui.circuit_check import check_circuit
from gft_opto.gui.symbols import (
    BusItem, CircuitBreakerItem, ConnectionItem, CurrentTransformerItem,
    GroundItem, JunctionItem, LineTrapItem, Transformer2WItem,
)


class CircuitCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.scene = QGraphicsScene()

    def add(self, item, name):
        item.display_id = name
        self.scene.addItem(item)
        return item

    def wire(self, a, ap, b, bp):
        conn = ConnectionItem(a, ap, b, bp)
        self.scene.addItem(conn)
        return conn

    def tap(self, bus, x):
        name, created = bus.add_tap(x)
        self.assertTrue(created)
        return name

    def test_empty_canvas_is_reported_and_not_ok(self):
        result = check_circuit(self.scene)
        self.assertFalse(result["ok"])
        self.assertEqual(result["open_ends"], [messages.CIRCUIT_CHECK_NO_COMPONENTS_ON_THE_CANVAS])
        self.assertEqual(result["shorts"], [])

    def test_unconnected_breaker_reports_both_terminals(self):
        self.add(CircuitBreakerItem(), "CB1")
        result = check_circuit(self.scene)
        self.assertFalse(result["ok"])
        self.assertCountEqual(result["open_ends"], [
            messages.CIRCUIT_CHECK_IS_NOT_CONNECTED.format("CB1", "left"),
            messages.CIRCUIT_CHECK_IS_NOT_CONNECTED.format("CB1", "right"),
        ])
        self.assertEqual(result["shorts"], [])

    def test_breaker_between_two_buses_passes(self):
        bus1, bus2 = self.add(BusItem(), "B1"), self.add(BusItem(), "B2")
        breaker = self.add(CircuitBreakerItem(), "CB1")
        self.wire(bus1, "right", breaker, "left")
        self.wire(breaker, "right", bus2, "left")
        self.assertEqual(check_circuit(self.scene),
                         {"ok": True, "open_ends": [], "shorts": [], "duplicate_ids": []})

    def test_breaker_with_both_terminals_on_one_bus_is_a_short(self):
        bus = self.add(BusItem(), "B1")
        breaker = self.add(CircuitBreakerItem(), "CB1")
        self.wire(bus, self.tap(bus, -40), breaker, "left")
        self.wire(breaker, "right", bus, self.tap(bus, 40))
        result = check_circuit(self.scene)
        self.assertFalse(result["ok"])
        self.assertEqual(result["open_ends"], [])
        self.assertEqual(result["shorts"],
                         [messages.CIRCUIT_CHECK_AND_ARE_ON_THE_SAME_NODE.format("CB1", "left", "right")])

    def test_transformer_windings_on_one_node_are_a_short(self):
        bus = self.add(BusItem(), "B1")
        xfmr = self.add(Transformer2WItem(), "T1")
        self.wire(bus, "left", xfmr, "H")
        self.wire(xfmr, "X", bus, "right")
        self.assertEqual(check_circuit(self.scene)["shorts"],
                         [messages.CIRCUIT_CHECK_AND_ARE_ON_THE_SAME_NODE.format("T1", "H", "X")])

    def test_two_terminal_device_without_a_listed_pair_is_still_checked(self):
        # Line traps are not in the explicit port-pair table.
        bus = self.add(BusItem(), "B1")
        trap = self.add(LineTrapItem(), "LT1")
        self.wire(bus, "left", trap, "left")
        self.wire(trap, "right", bus, "right")
        self.assertEqual(check_circuit(self.scene)["shorts"],
                         [messages.CIRCUIT_CHECK_AND_ARE_ON_THE_SAME_NODE.format("LT1", "left", "right")])

    def test_one_open_terminal_is_not_a_short(self):
        bus = self.add(BusItem(), "B1")
        breaker = self.add(CircuitBreakerItem(), "CB1")
        self.wire(bus, "right", breaker, "left")
        result = check_circuit(self.scene)
        self.assertEqual(result["open_ends"], [messages.CIRCUIT_CHECK_IS_NOT_CONNECTED.format("CB1", "right")])
        self.assertEqual(result["shorts"], [])

    def test_junction_with_zero_one_and_two_wires(self):
        bus1, bus2 = self.add(BusItem(), "B1"), self.add(BusItem(), "B2")
        self.add(JunctionItem(), "J0")
        dangling = self.add(JunctionItem(), "J1")
        through = self.add(JunctionItem(), "J2")
        self.wire(bus1, "left", dangling, "node")
        self.wire(bus1, "right", through, "node")
        self.wire(through, "node", bus2, "left")
        open_ends = check_circuit(self.scene)["open_ends"]
        self.assertCountEqual(open_ends, [
            messages.CIRCUIT_CHECK_JUNCTION_IS_NOT_CONNECTED.format("J0"),
            messages.CIRCUIT_CHECK_DANGLING_WIRE_ONLY_ONE_CONNECTION.format("J1"),
        ])

    def test_unconnected_bus_is_reported(self):
        self.add(BusItem(), "B1")
        self.assertEqual(check_circuit(self.scene)["open_ends"],
                         [messages.CIRCUIT_CHECK_BUS_IS_NOT_CONNECTED.format("B1")])

    def test_ground_wired_straight_to_a_bus_is_a_short(self):
        bus = self.add(BusItem(), "B1")
        ground = self.add(GroundItem(), "G1")
        self.wire(bus, "left", ground, "node")
        result = check_circuit(self.scene)
        self.assertEqual(result["shorts"], [messages.CIRCUIT_CHECK_IS_TIED_DIRECTLY_TO.format("B1", "G1")])

    def test_ground_behind_a_device_is_not_a_bus_short(self):
        bus = self.add(BusItem(), "B1")
        ct = self.add(CurrentTransformerItem(), "CT1")
        ground = self.add(GroundItem(), "G1")
        self.wire(bus, "left", ct, "left")
        self.wire(ct, "right", ground, "node")
        self.assertEqual(check_circuit(self.scene)["shorts"], [])

    def test_long_junction_chain_detects_grounded_bus_without_recursion(self):
        bus = self.add(BusItem(), "B1")
        ground = self.add(GroundItem(), "G1")
        items = [bus] + [self.add(JunctionItem(), f"J{i}") for i in range(1200)] + [ground]
        for index, (a, b) in enumerate(zip(items, items[1:])):
            wire = self.wire(a, "right" if index == 0 else "node", b, "node")
            # Process from the bus end first, the old validator's worst order.
            wire.setZValue(-index - 1)
        result = check_circuit(self.scene)
        self.assertEqual(result["open_ends"], [])
        self.assertEqual(result["shorts"], [messages.CIRCUIT_CHECK_IS_TIED_DIRECTLY_TO.format("B1", "G1")])

    def test_wire_to_equipment_outside_the_drawing_is_ignored(self):
        breaker = self.add(CircuitBreakerItem(), "CB1")
        bus = self.add(BusItem(), "B1")
        outside = CurrentTransformerItem()  # never added to the scene
        self.wire(bus, "right", breaker, "left")
        self.wire(breaker, "right", outside, "left")
        self.assertEqual(check_circuit(self.scene)["open_ends"],
                         [messages.CIRCUIT_CHECK_IS_NOT_CONNECTED.format("CB1", "right")])


if __name__ == "__main__":
    unittest.main()
