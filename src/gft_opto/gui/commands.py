"""Undo and redo commands for the one-line canvas."""

from PySide6.QtCore import QPointF
from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene

from gft_opto.gui.routing import snap_point_to_grid
from gft_opto.gui.symbols import (
    BusItem,
    ConnectionItem,
    JunctionItem,
    OneLineSymbolItem,
    refresh_symbol_labels,
)

# ---------------------------------------------------------------------------
# Undo / redo commands
# ---------------------------------------------------------------------------

class AddEquipmentCommand(QUndoCommand):
    def __init__(self, scene: QGraphicsScene, item: OneLineSymbolItem):
        super().__init__(f"Place {item.instance_id}")
        self.scene = scene
        self.item = item

    def redo(self):
        if self.item.scene() is not self.scene:
            self.scene.addItem(self.item)

    def undo(self):
        if self.item.scene() is self.scene:
            self.scene.removeItem(self.item)


class AddConnectionCommand(QUndoCommand):
    def __init__(
        self,
        scene: QGraphicsScene,
        from_item: OneLineSymbolItem,
        from_port: str,
        to_item: OneLineSymbolItem,
        to_port: str,
    ):
        super().__init__(
            f"Connect {from_item.instance_id}:{from_port} → {to_item.instance_id}:{to_port}"
        )
        self.scene = scene
        self.from_item = from_item
        self.from_port = from_port
        self.to_item = to_item
        self.to_port = to_port
        self.conn: ConnectionItem | None = None

    def redo(self):
        if self.conn is None:
            self.conn = ConnectionItem(
                self.from_item, self.from_port, self.to_item, self.to_port
            )
            self.scene.addItem(self.conn)
            return

        if self.conn.scene() is not self.scene:
            self.scene.addItem(self.conn)
        self.conn.attach()
        self.conn.update_path()

    def undo(self):
        if self.conn is None:
            return
        self.conn.detach()
        if self.conn.scene() is self.scene:
            self.scene.removeItem(self.conn)
        refresh_symbol_labels(self.scene)


class ConnectToBusCommand(QUndoCommand):
    """Drop a wire on a bus bar and create a node at that spot."""

    def __init__(
        self,
        scene: QGraphicsScene,
        from_item: OneLineSymbolItem,
        from_port: str,
        bus: BusItem,
        scene_pos: QPointF,
    ):
        super().__init__(
            f"Connect {from_item.instance_id}:{from_port} → {bus.instance_id}"
        )
        self.scene = scene
        self.from_item = from_item
        self.from_port = from_port
        self.bus = bus
        drop = self._drop_point(bus, scene_pos)
        self.base_x = bus.base_x_for_scene(drop)
        self.port_name: str | None = None
        self._created_port = False
        self.conn: ConnectionItem | None = None

    @staticmethod
    def _drop_point(bus: BusItem, scene_pos: QPointF) -> QPointF:
        projected = bus.project_onto_bar(scene_pos)
        if OneLineSymbolItem.snap_to_grid_enabled:
            projected = snap_point_to_grid(projected)
            return bus.point_on_bar(projected)
        return projected

    def redo(self):
        name, created = self.bus.add_tap(self.base_x, self.port_name)
        if self.port_name is None:
            self.port_name = name
            self._created_port = created
        elif name != self.port_name:
            self.port_name = name
            self._created_port = False
        self.setText(
            f"Connect {self.from_item.instance_id}:{self.from_port} → "
            f"{self.bus.instance_id}:{self.port_name}"
        )
        if self.conn is None:
            self.conn = ConnectionItem(
                self.from_item, self.from_port, self.bus, self.port_name
            )
            self.scene.addItem(self.conn)
            return
        self.conn.to_port = self.port_name
        if self.conn.scene() is not self.scene:
            self.scene.addItem(self.conn)
        self.conn.attach()
        self.conn.update_path()

    def undo(self):
        if self.conn is not None:
            self.conn.detach()
            if self.conn.scene() is self.scene:
                self.scene.removeItem(self.conn)
        if self._created_port and self.port_name is not None:
            self.bus.remove_tap(self.port_name)
        refresh_symbol_labels(self.scene)


class SplitWireCommand(QUndoCommand):
    """
    Insert a junction on an existing wire at `point`, splitting it into two
    segments. Optionally also attach a new branch wire to the junction.
    """

    def __init__(
        self,
        scene: QGraphicsScene,
        wire: ConnectionItem,
        point: QPointF,
        branch_from: OneLineSymbolItem | None = None,
        branch_port: str | None = None,
    ):
        super().__init__("Add wire junction")
        self.scene = scene
        self.wire = wire
        self.point = QPointF(point)
        self.branch_from = branch_from
        self.branch_port = branch_port
        self.junction: JunctionItem | None = None
        self.seg_a: ConnectionItem | None = None
        self.seg_b: ConnectionItem | None = None
        self.branch: ConnectionItem | None = None
        self._did_remove_wire = False

    def redo(self):
        if self.junction is None:
            pos = (
                snap_point_to_grid(self.point)
                if OneLineSymbolItem.snap_to_grid_enabled
                else QPointF(self.point)
            )
            self.junction = JunctionItem()
            self.junction.setPos(pos)
            self.junction.setZValue(100)

            a, ap = self.wire.from_item, self.wire.from_port
            b, bp = self.wire.to_item, self.wire.to_port
            self.wire.detach()
            if self.wire.scene() is self.scene:
                self.scene.removeItem(self.wire)
            self._did_remove_wire = True

            self.scene.addItem(self.junction)
            self.seg_a = ConnectionItem(a, ap, self.junction, "node")
            self.seg_b = ConnectionItem(self.junction, "node", b, bp)
            self.scene.addItem(self.seg_a)
            self.scene.addItem(self.seg_b)
            if self.branch_from is not None and self.branch_port is not None:
                self.branch = ConnectionItem(
                    self.branch_from, self.branch_port, self.junction, "node"
                )
                self.scene.addItem(self.branch)
            refresh_symbol_labels(self.scene)
            return

        if self._did_remove_wire and self.wire.scene() is self.scene:
            self.wire.detach()
            self.scene.removeItem(self.wire)
        if self.junction.scene() is not self.scene:
            self.scene.addItem(self.junction)
        for seg in (self.seg_a, self.seg_b, self.branch):
            if seg is None:
                continue
            if seg.scene() is not self.scene:
                self.scene.addItem(seg)
            seg.attach()
            seg.update_path()
        refresh_symbol_labels(self.scene)

    def undo(self):
        for seg in (self.branch, self.seg_b, self.seg_a):
            if seg is None:
                continue
            seg.detach()
            if seg.scene() is self.scene:
                self.scene.removeItem(seg)
        if self.junction is not None and self.junction.scene() is self.scene:
            self.scene.removeItem(self.junction)
        if self.wire.scene() is not self.scene:
            self.scene.addItem(self.wire)
        self.wire.attach()
        self.wire.update_path()
        refresh_symbol_labels(self.scene)


class MoveEquipmentCommand(QUndoCommand):
    def __init__(self, moves: list[tuple[OneLineSymbolItem, QPointF, QPointF]]):
        label = "Move equipment" if len(moves) != 1 else f"Move {moves[0][0].instance_id}"
        super().__init__(label)
        self.moves = [(item, QPointF(old), QPointF(new)) for item, old, new in moves]

    def redo(self):
        for item, _old, new in self.moves:
            item.setPos(new)

    def undo(self):
        for item, old, _new in self.moves:
            item.setPos(old)


class ResizeEquipmentCommand(QUndoCommand):
    def __init__(self, item: OneLineSymbolItem, old_scale: float, new_scale: float):
        super().__init__(f"Resize {item.instance_id}")
        self.item = item
        self.old_scale = old_scale
        self.new_scale = new_scale

    def redo(self):
        self.item.set_scale_factor(self.new_scale)

    def undo(self):
        self.item.set_scale_factor(self.old_scale)


class ResizeBusCommand(QUndoCommand):
    def __init__(self, bus: BusItem, port: str, old_x: float, new_x: float):
        super().__init__(f"Resize {bus.instance_id}")
        self.bus = bus
        self.port = port
        self.old_x = old_x
        self.new_x = new_x

    def redo(self):
        self.bus.set_end_base_x(self.port, self.new_x)

    def undo(self):
        self.bus.set_end_base_x(self.port, self.old_x)


class RotateEquipmentCommand(QUndoCommand):
    def __init__(self, rotations: list[tuple[OneLineSymbolItem, float, float]]):
        label = (
            "Rotate equipment"
            if len(rotations) != 1
            else f"Rotate {rotations[0][0].instance_id}"
        )
        super().__init__(label)
        self.rotations = [
            (item, float(old), float(new)) for item, old, new in rotations
        ]

    def redo(self):
        for item, _old, new in self.rotations:
            item.set_rotation_deg(new)

    def undo(self):
        for item, old, _new in self.rotations:
            item.set_rotation_deg(old)


class DeleteSelectionCommand(QUndoCommand):
    def __init__(self, scene: QGraphicsScene, selected_items: list[QGraphicsItem]):
        super().__init__("Delete selection")
        self.scene = scene
        symbols: list[OneLineSymbolItem] = []
        connections: list[ConnectionItem] = []

        for item in selected_items:
            if isinstance(item, OneLineSymbolItem):
                symbols.append(item)
                for conn in item.connections():
                    if conn not in connections:
                        connections.append(conn)
            elif isinstance(item, ConnectionItem):
                if item not in connections:
                    connections.append(item)

        self.symbols = symbols
        self.connections = connections
        self.removed_taps: list[tuple[BusItem, str, QPointF]] = []
        self._taps_recorded = False
        count = len(self.symbols) + len(self.connections)
        self.setText(f"Delete {count} item(s)")

    def _orphan_bus_taps(self) -> list[tuple[BusItem, str, QPointF]]:
        """Dropped bus nodes whose wires are all part of this delete."""
        deleted = set(self.symbols)
        found: list[tuple[BusItem, str, QPointF]] = []
        seen: set[tuple[int, str]] = set()
        for conn in self.connections:
            for item, port in (
                (conn.from_item, conn.from_port),
                (conn.to_item, conn.to_port),
            ):
                if not isinstance(item, BusItem) or item in deleted:
                    continue
                if port in ("left", "right") or port not in item._base_ports:
                    continue
                key = (id(item), port)
                if key in seen:
                    continue
                still_used = any(
                    (other.from_item is item and other.from_port == port)
                    or (other.to_item is item and other.to_port == port)
                    for other in item.connections()
                    if other not in self.connections
                )
                if still_used:
                    continue
                seen.add(key)
                found.append((item, port, QPointF(item._base_ports[port])))
        return found

    def redo(self):
        for conn in self.connections:
            conn.detach()
            if conn.scene() is self.scene:
                self.scene.removeItem(conn)
        if not self._taps_recorded:
            self.removed_taps = self._orphan_bus_taps()
            self._taps_recorded = True
        for bus, name, _pt in self.removed_taps:
            bus.remove_tap(name)
        for symbol in self.symbols:
            if symbol.scene() is self.scene:
                self.scene.removeItem(symbol)
        refresh_symbol_labels(self.scene)

    def undo(self):
        for bus, name, pt in self.removed_taps:
            bus.restore_tap(name, pt.x())
        for symbol in self.symbols:
            if symbol.scene() is not self.scene:
                self.scene.addItem(symbol)
        for conn in self.connections:
            if conn.from_item is None or conn.to_item is None:
                continue
            if conn.from_item.scene() is not self.scene or conn.to_item.scene() is not self.scene:
                continue
            if conn.scene() is not self.scene:
                self.scene.addItem(conn)
            conn.attach()
            conn.update_path()
        refresh_symbol_labels(self.scene)


