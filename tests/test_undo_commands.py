"""Each editing command must apply, undo back to the exact previous drawing, and redo."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QUndoStack
from PySide6.QtWidgets import QApplication, QGraphicsScene

from gft_opto.gui.commands import (
    AddConnectionCommand, ConnectToBusCommand, DeleteSelectionCommand, EditPropertyCommand,
    MoveEquipmentCommand, PasteComponentsCommand, ReplaceZonesCommand, ResizeBusCommand,
    ResizeEquipmentCommand, RotateEquipmentCommand, SplitWireCommand,
)
from gft_opto.gui.symbols import (
    BusItem, CircuitBreakerItem, ConnectionItem, CurrentTransformerItem, JunctionItem,
    OneLineSymbolItem,
)


class UndoCommands(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._snap = OneLineSymbolItem.snap_to_grid_enabled
        OneLineSymbolItem.snap_to_grid_enabled = False
        self.scene = QGraphicsScene()
        self.stack = QUndoStack()

    def tearDown(self):
        OneLineSymbolItem.snap_to_grid_enabled = self._snap
        self.stack.clear()

    def place(self, item, x=0.0, y=0.0):
        self.scene.addItem(item)
        item.setPos(x, y)
        return item

    def wire(self, a, ap, b, bp):
        conn = ConnectionItem(a, ap, b, bp)
        self.scene.addItem(conn)
        return conn

    def assert_point(self, actual, expected):
        self.assertAlmostEqual(actual.x(), expected.x(), places=6)
        self.assertAlmostEqual(actual.y(), expected.y(), places=6)

    # --- wiring -------------------------------------------------------------

    def test_add_connection_undo_and_redo_reuse_the_same_wire(self):
        bus, breaker = self.place(BusItem()), self.place(CircuitBreakerItem(), 200, 0)
        command = AddConnectionCommand(self.scene, bus, "right", breaker, "left")
        self.stack.push(command)
        wire = command.conn
        self.assertIs(wire.scene(), self.scene)
        self.assertIn(wire, breaker.connections())
        self.stack.undo()
        self.assertIsNone(wire.scene())
        self.assertEqual(breaker.connections(), [])
        self.assertEqual(bus.connections(), [])
        self.stack.redo()
        self.assertIs(command.conn, wire)
        self.assertIs(wire.scene(), self.scene)
        self.assertIn(wire, bus.connections())

    def test_connect_to_bus_creates_a_node_and_undo_removes_it(self):
        bus, breaker = self.place(BusItem()), self.place(CircuitBreakerItem(), 30, 150)
        command = ConnectToBusCommand(self.scene, breaker, "left", bus, QPointF(30, 5))
        self.stack.push(command)
        node = command.port_name
        self.assertNotIn(node, ("left", "right"))
        self.assertAlmostEqual(bus.ports()[node].x(), 30)
        self.assertEqual(command.conn.to_port, node)
        self.stack.undo()
        self.assertNotIn(node, bus.ports())
        self.assertIsNone(command.conn.scene())
        self.stack.redo()
        self.assertIn(node, bus.ports())
        self.assertEqual(command.conn.to_port, node)
        self.assertIn(command.conn, bus.connections())

    def test_connect_to_bus_reuses_a_nearby_node_and_undo_keeps_it(self):
        bus, breaker = self.place(BusItem()), self.place(CircuitBreakerItem(), 30, 150)
        existing, created = bus.add_tap(30)
        self.assertTrue(created)
        command = ConnectToBusCommand(self.scene, breaker, "left", bus, QPointF(31, 0))
        self.stack.push(command)
        self.assertEqual(command.port_name, existing)
        self.stack.undo()
        self.assertIn(existing, bus.ports())

    def test_split_wire_with_branch_round_trips(self):
        bus, breaker = self.place(BusItem()), self.place(CircuitBreakerItem(), 300, 0)
        ct = self.place(CurrentTransformerItem(), 200, 200)
        original = self.wire(bus, "right", breaker, "left")
        command = SplitWireCommand(self.scene, original, QPointF(200, 0), ct, "left")
        self.stack.push(command)
        junction = command.junction
        self.assertIsNone(original.scene())
        self.assertIsInstance(junction, JunctionItem)
        self.assertEqual(len(junction.connections()), 3)
        self.assertEqual(len(ct.connections()), 1)
        self.stack.undo()
        self.assertIsNone(junction.scene())
        self.assertIs(original.scene(), self.scene)
        self.assertEqual(bus.connections(), [original])
        self.assertEqual(breaker.connections(), [original])
        self.assertEqual(ct.connections(), [])
        self.stack.redo()
        self.assertIs(command.junction, junction)
        self.assertIs(junction.scene(), self.scene)
        self.assertIsNone(original.scene())
        self.assertEqual(len(junction.connections()), 3)

    # --- geometry -----------------------------------------------------------

    def test_move_applies_exact_positions_even_with_snap_on(self):
        a, b = self.place(CircuitBreakerItem()), self.place(CircuitBreakerItem(), 100, 0)
        OneLineSymbolItem.snap_to_grid_enabled = True
        self.stack.push(MoveEquipmentCommand([
            (a, QPointF(0, 0), QPointF(13.5, 7.25)),
            (b, QPointF(100, 0), QPointF(140.5, 60.75)),
        ]))
        self.assert_point(a.pos(), QPointF(13.5, 7.25))
        self.assert_point(b.pos(), QPointF(140.5, 60.75))
        self.assertTrue(OneLineSymbolItem.snap_to_grid_enabled)
        self.stack.undo()
        self.assert_point(a.pos(), QPointF(0, 0))
        self.assert_point(b.pos(), QPointF(100, 0))
        self.assertTrue(OneLineSymbolItem.snap_to_grid_enabled)

    def test_resize_moves_the_attached_wire_end(self):
        bus, breaker = self.place(BusItem()), self.place(CircuitBreakerItem(), 200, 0)
        conn = self.wire(bus, "right", breaker, "left")
        self.stack.push(ResizeEquipmentCommand(breaker, 1.0, 1.5))
        self.assertEqual(breaker.scale_factor, 1.5)
        self.assert_point(conn.route_points()[-1], breaker.port_scene_pos("left"))
        self.stack.undo()
        self.assertEqual(breaker.scale_factor, 1.0)
        self.assert_point(conn.route_points()[-1], breaker.port_scene_pos("left"))

    def test_resize_bus_end_round_trips(self):
        bus = self.place(BusItem())
        self.stack.push(ResizeBusCommand(bus, "right", 80.0, 150.0))
        self.assertAlmostEqual(bus.ports()["right"].x(), 150)
        self.assertAlmostEqual(bus.ports()["left"].x(), -80)
        self.stack.undo()
        self.assertAlmostEqual(bus.ports()["right"].x(), 80)

    def test_rotate_several_items_round_trips(self):
        a, b = self.place(CircuitBreakerItem()), self.place(CurrentTransformerItem(), 100, 0)
        self.stack.push(RotateEquipmentCommand([(a, 0, 90), (b, 0, 270)]))
        self.assertEqual((a.rotation_deg, b.rotation_deg), (90.0, 270.0))
        self.stack.undo()
        self.assertEqual((a.rotation_deg, b.rotation_deg), (0.0, 0.0))
        self.stack.redo()
        self.assertEqual((a.rotation_deg, b.rotation_deg), (90.0, 270.0))

    # --- delete ---------------------------------------------------------------

    def test_delete_removes_wires_and_orphan_bus_node_then_undo_restores(self):
        bus = self.place(BusItem())
        breaker = self.place(CircuitBreakerItem(), 30, 150)
        ct = self.place(CurrentTransformerItem(), 30, 300)
        node, _ = bus.add_tap(30)
        to_bus = self.wire(bus, node, breaker, "left")
        to_ct = self.wire(breaker, "right", ct, "left")
        self.stack.push(DeleteSelectionCommand(self.scene, [breaker]))
        self.assertIsNone(breaker.scene())
        self.assertIsNone(to_bus.scene())
        self.assertIsNone(to_ct.scene())
        self.assertNotIn(node, bus.ports())
        self.assertEqual(ct.connections(), [])
        self.stack.undo()
        self.assertIs(breaker.scene(), self.scene)
        self.assertAlmostEqual(bus.ports()[node].x(), 30)
        self.assertEqual(bus.connections(), [to_bus])
        self.assertEqual(ct.connections(), [to_ct])
        self.assertCountEqual(breaker.connections(), [to_bus, to_ct])

    def test_delete_keeps_a_bus_node_another_wire_still_uses(self):
        bus = self.place(BusItem())
        breaker = self.place(CircuitBreakerItem(), 30, 150)
        other = self.place(CircuitBreakerItem(), 30, -150)
        node, _ = bus.add_tap(30)
        self.wire(bus, node, breaker, "left")
        kept = self.wire(other, "right", bus, node)
        self.stack.push(DeleteSelectionCommand(self.scene, [breaker]))
        self.assertIn(node, bus.ports())
        self.assertEqual(bus.connections(), [kept])

    def test_deleting_only_a_wire_keeps_the_equipment(self):
        bus, breaker = self.place(BusItem()), self.place(CircuitBreakerItem(), 200, 0)
        conn = self.wire(bus, "right", breaker, "left")
        self.stack.push(DeleteSelectionCommand(self.scene, [conn]))
        self.assertIs(bus.scene(), self.scene)
        self.assertIs(breaker.scene(), self.scene)
        self.assertIsNone(conn.scene())
        self.assertEqual(breaker.connections(), [])
        self.stack.undo()
        self.assertEqual(breaker.connections(), [conn])

    # --- paste, properties, zones ----------------------------------------------

    def test_paste_adds_a_selected_connected_group_and_grows_the_canvas(self):
        self.scene.setSceneRect(QRectF(0, 0, 100, 100))
        syncs = []
        a, b = CircuitBreakerItem(), CurrentTransformerItem()
        a.setPos(500, 500)
        b.setPos(600, 500)
        conn = ConnectionItem(a, "right", b, "left")
        command = PasteComponentsCommand(self.scene, [a, b], [conn], lambda: syncs.append(1))
        self.stack.push(command)
        self.assertIs(a.scene(), self.scene)
        self.assertIs(conn.scene(), self.scene)
        self.assertTrue(a.isSelected() and b.isSelected())
        self.assertTrue(self.scene.sceneRect().contains(a.sceneBoundingRect()))
        self.assertEqual(len(syncs), 1)
        self.stack.undo()
        self.assertIsNone(a.scene())
        self.assertIsNone(conn.scene())
        self.assertEqual(a.connections(), [])
        self.assertEqual(self.scene.sceneRect(), QRectF(0, 0, 100, 100))
        self.assertEqual(len(syncs), 2)
        self.stack.redo()
        self.assertEqual(a.connections(), [conn])

    def test_edit_property_restores_old_value_or_removes_new_key(self):
        breaker = self.place(CircuitBreakerItem())
        breaker.properties = {"trip_coil_1_a": 5}
        refreshes = []
        refresh = lambda: refreshes.append(1)
        self.stack.push(EditPropertyCommand(breaker, "trip_coil_1_a", 7.5, refresh))
        self.stack.push(EditPropertyCommand(breaker, "close_coil_a", 12, refresh))
        self.assertEqual(breaker.properties, {"trip_coil_1_a": 7.5, "close_coil_a": 12})
        self.stack.undo()
        self.assertNotIn("close_coil_a", breaker.properties)
        self.stack.undo()
        self.assertEqual(breaker.properties, {"trip_coil_1_a": 5})
        self.assertEqual(len(refreshes), 4)

    def test_edit_property_none_removes_the_key(self):
        breaker = self.place(CircuitBreakerItem())
        breaker.properties = {"motor_run_a": 3}
        self.stack.push(EditPropertyCommand(breaker, "motor_run_a", None, lambda: None))
        self.assertNotIn("motor_run_a", breaker.properties)
        self.stack.undo()
        self.assertEqual(breaker.properties, {"motor_run_a": 3})

    def test_edit_display_id_round_trips(self):
        breaker = self.place(CircuitBreakerItem())
        breaker.display_id = "CB1"
        self.stack.push(EditPropertyCommand(breaker, "display_id", "CB-NEW", lambda: None))
        self.assertEqual(breaker.visible_id(), "CB-NEW")
        self.stack.undo()
        self.assertEqual(breaker.visible_id(), "CB1")

    def test_replace_zones_applies_independent_copies(self):
        applied = []
        old = [{"id": "z1", "actions": []}]
        new = [{"id": "z1", "actions": [{"component": "cb1"}]}]
        self.stack.push(ReplaceZonesCommand(old, new, applied.append))
        self.assertEqual(applied[-1], new)
        self.assertIsNot(applied[-1], new)
        new[0]["actions"].clear()  # later edits to the caller's list must not leak in
        self.stack.undo()
        self.assertEqual(applied[-1], old)
        self.stack.redo()
        self.assertEqual(applied[-1], [{"id": "z1", "actions": [{"component": "cb1"}]}])


if __name__ == "__main__":
    unittest.main()
