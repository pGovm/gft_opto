"""
Layout:
  - Constants & ID helpers
  - Symbol graphics items (equipment + connections)
  - Undo/redo commands
  - Equipment library drag source
  - Workspace canvas (drop, move, wire, zoom)
  - Main window (panels, PDF import, properties)
"""

import sys
from collections import defaultdict
from typing import Callable

from PySide6.QtCore import Qt, QPointF, QRectF, QByteArray, QMimeData
from PySide6.QtGui import (
    QFont, QPixmap, QDrag, QPainter, QPen, QBrush, QColor,
    QKeySequence, QUndoStack, QUndoCommand,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QPushButton, QSlider, QTextEdit, QVBoxLayout, QWidget, QFileDialog,
    QGraphicsView, QGraphicsScene, QGraphicsItem, QGraphicsPixmapItem,
    QGraphicsLineItem, QDialog,
    QMessageBox,
)

import pymupdf
import functools

from gft_opto.customWidgetTool import ComponentDialog


# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------

MIME_EQUIPMENT = "application/x-substation-equipment"
PORT_HIT_RADIUS = 14.0       # px — how close the cursor must be to snap to a port
PORT_DOT_RADIUS = 4.0        # px — visual size of port handles
WIRE_DRAG_THRESHOLD = 6.0    # px — min drag before rubber-band wire appears
DEFAULT_WORKSPACE_WIDTH = 2400.0
DEFAULT_WORKSPACE_HEIGHT = 1800.0
GRID_SPACING = 25.0          # scene units — matches PDF pixel coords after import
GRID_MAJOR_EVERY = 5         # every Nth line is drawn heavier
DEFAULT_GRID_OPACITY = 0.25

_instance_counters: dict[str, int] = defaultdict(int)


def next_instance_id(equip_type: str) -> str:
    """Assign a unique id per placed symbol (e.g. mos_1, mos_2)."""
    _instance_counters[equip_type] += 1
    return f"{equip_type}_{_instance_counters[equip_type]}"


# ---------------------------------------------------------------------------
# Symbol graphics items
# ---------------------------------------------------------------------------

class OneLineSymbolItem(QGraphicsItem):
    """Base class for draggable one-line equipment symbols with named ports."""

    def __init__(self, equip_type: str, label: str, ports: dict[str, QPointF]):
        super().__init__()
        self.equip_type = equip_type
        self.equip_id = equip_type  # legacy alias used by the properties panel
        self.instance_id = next_instance_id(equip_type)
        self.label = label
        self._ports = dict(ports)
        self._connections: list["ConnectionItem"] = []
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )

    # --- Port helpers -------------------------------------------------------

    def ports(self) -> dict[str, QPointF]:
        return self._ports

    def port_scene_pos(self, port_name: str) -> QPointF:
        return self.mapToScene(self._ports[port_name])

    def nearest_port(self, scene_pos: QPointF, max_dist: float = PORT_HIT_RADIUS):
        """Return the closest port name within max_dist, or None."""
        best_name = None
        best_dist = max_dist
        for name, local in self._ports.items():
            delta = self.mapToScene(local) - scene_pos
            dist = (delta.x() ** 2 + delta.y() ** 2) ** 0.5
            if dist <= best_dist:
                best_dist = dist
                best_name = name
        return best_name

    def add_connection(self, conn: "ConnectionItem"):
        if conn not in self._connections:
            self._connections.append(conn)

    def remove_connection(self, conn: "ConnectionItem"):
        if conn in self._connections:
            self._connections.remove(conn)

    def connections(self) -> list["ConnectionItem"]:
        return list(self._connections)

    # --- Drawing ------------------------------------------------------------

    def _pen(self):
        return QPen(Qt.GlobalColor.black, 2)

    def _draw_ports(self, painter: QPainter):
        """Draw blue port dots so drag-to-connect is easy to discover."""
        color = QColor("#1d4ed8") if self.isSelected() else QColor("#2f6fed")
        painter.setPen(QPen(color, 1.5))
        painter.setBrush(QBrush(color))
        r = PORT_DOT_RADIUS
        for pt in self._ports.values():
            painter.drawEllipse(QRectF(pt.x() - r, pt.y() - r, 2 * r, 2 * r))

    def itemChange(self, change, value):
        # Keep attached wires in sync when this symbol moves or is selected.
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for conn in self._connections:
                conn.update_path()
        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.update()
        return super().itemChange(change, value)


class Transformer2WItem(OneLineSymbolItem):
    def __init__(self):
        super().__init__(
            "xfmr_2w",
            "Transformer (2-winding)",
            {"H": QPointF(-34, 0), "X": QPointF(34, 0)},
        )
        self._rect = QRectF(-34, -20, 68, 40)

    def boundingRect(self):
        return self._rect.adjusted(-6, -6, 6, 6)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(self._pen())
        painter.drawLine(-34, 0, -18, 0)
        painter.drawLine(18, 0, 34, 0)
        painter.drawEllipse(-18, -12, 24, 24)
        painter.drawEllipse(-6, -12, 24, 24)
        self._draw_ports(painter)


class Transformer3WItem(OneLineSymbolItem):
    def __init__(self):
        super().__init__(
            "xfmr_3w",
            "Transformer (3-winding)",
            {"H1": QPointF(-38, -1), "H2": QPointF(38, -1), "X": QPointF(3, 38)},
        )
        self._rect = QRectF(-38, -28, 76, 60)

    def boundingRect(self):
        return self._rect.adjusted(-6, -6, 6, 6)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(self._pen())
        painter.drawEllipse(-18, -12, 22, 22)
        painter.drawEllipse(2, -12, 22, 22)
        painter.drawEllipse(-8, 8, 22, 22)
        painter.drawLine(-38, -1, -18, -1)
        painter.drawLine(24, -1, 38, -1)
        painter.drawLine(3, 30, 3, 38)
        self._draw_ports(painter)


class MotorOperatedSwitchItem(OneLineSymbolItem):
    def __init__(self):
        super().__init__(
            "mos",
            "Motor Operated Switch (MOS)",
            {"left": QPointF(-38, 0), "right": QPointF(38, 0)},
        )
        self._rect = QRectF(-38, -18, 76, 36)

    def boundingRect(self):
        return self._rect.adjusted(-6, -6, 6, 6)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(self._pen())
        painter.drawLine(-38, 0, -12, 0)
        painter.drawLine(12, 0, 38, 0)
        painter.drawLine(-12, 0, 12, -10)
        painter.setBrush(QBrush(Qt.GlobalColor.white))
        painter.drawEllipse(QRectF(-14, -2, 4, 4))
        painter.drawEllipse(QRectF(10, -2, 4, 4))
        painter.drawRect(QRectF(-6, 6, 12, 8))
        self._draw_ports(painter)


class CurrentTransformerItem(OneLineSymbolItem):
    def __init__(self):
        super().__init__(
            "ct",
            "Current Transformer (CT/BCT)",
            {"left": QPointF(-16, 0), "right": QPointF(16, 0)},
        )
        self._rect = QRectF(-22, -22, 44, 44)

    def boundingRect(self):
        return self._rect.adjusted(-6, -6, 6, 6)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(self._pen())
        painter.drawEllipse(QRectF(-16, -16, 32, 32))
        painter.drawEllipse(QRectF(-6, -6, 12, 12))
        self._draw_ports(painter)


class CustomComponentItem(OneLineSymbolItem):
    """A user-defined component created via the 'Create Component' dialog."""

    def __init__(self, name: str, properties: dict | None = None):
        slug = "".join(ch if ch.isalnum() else "_" for ch in name.strip().lower())
        equip_type = f"custom_{slug}" if slug else "custom_component"
        super().__init__(
            equip_type,
            name,
            {"left": QPointF(-40, 0), "right": QPointF(40, 0)},
        )
        self.properties = dict(properties or {})
        self._rect = QRectF(-42, -22, 84, 44)

    def boundingRect(self):
        return self._rect.adjusted(-6, -6, 6, 6)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(self._pen())
        painter.setBrush(QBrush(Qt.GlobalColor.white))
        painter.drawRect(self._rect)
        painter.setFont(QFont("Sans-Serif", 8))
        painter.drawText(self._rect, Qt.AlignmentFlag.AlignCenter, self.label)
        self._draw_ports(painter)


class ConnectionItem(QGraphicsLineItem):
    """A wire between two symbol ports; updates when either endpoint moves."""

    def __init__(
        self,
        from_item: OneLineSymbolItem,
        from_port: str,
        to_item: OneLineSymbolItem,
        to_port: str,
    ):
        super().__init__()
        self.from_item = from_item
        self.from_port = from_port
        self.to_item = to_item
        self.to_port = to_port
        self.setPen(QPen(QColor("#1f2933"), 2.5))
        self.setZValue(50)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.attach()
        self.update_path()

    def update_path(self):
        if self.from_item is None or self.to_item is None:
            return
        p1 = self.from_item.port_scene_pos(self.from_port)
        p2 = self.to_item.port_scene_pos(self.to_port)
        self.setLine(p1.x(), p1.y(), p2.x(), p2.y())

    def attach(self):
        if self.from_item is not None:
            self.from_item.add_connection(self)
        if self.to_item is not None:
            self.to_item.add_connection(self)

    def detach(self):
        # Keep endpoint refs so undo can reattach the same connection object.
        if self.from_item is not None:
            self.from_item.remove_connection(self)
        if self.to_item is not None:
            self.to_item.remove_connection(self)


# Maps equipment type keys to display labels and factory callables.
EQUIPMENT_DEFS = {
    "xfmr_2w": {"label": "Transformer (2-winding)", "factory": Transformer2WItem},
    "xfmr_3w": {"label": "Transformer (3-winding)", "factory": Transformer3WItem},
    "mos": {"label": "Motor Operated Switch (MOS)", "factory": MotorOperatedSwitchItem},
    "ct": {"label": "Current Transformer (CT/BCT)", "factory": CurrentTransformerItem},
}


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
        else:
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
        count = len(self.symbols) + len(self.connections)
        self.setText(f"Delete {count} item(s)")

    def redo(self):
        for conn in self.connections:
            conn.detach()
            if conn.scene() is self.scene:
                self.scene.removeItem(conn)
        for symbol in self.symbols:
            if symbol.scene() is self.scene:
                self.scene.removeItem(symbol)

    def undo(self):
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


# ---------------------------------------------------------------------------
# Equipment library (left panel drag source)
# ---------------------------------------------------------------------------

class EquipmentList(QListWidget):
    """List of equipment types; dragging an entry drops a new symbol on the canvas."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)

    def startDrag(self, supportedActions):
        item = self.currentItem()
        if not item:
            return
        equip_id = item.data(Qt.ItemDataRole.UserRole)
        if not equip_id:
            return

        mime = QMimeData()
        mime.setData(MIME_EQUIPMENT, QByteArray(str(equip_id).encode("utf-8")))

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


# ---------------------------------------------------------------------------
# Workspace canvas
# ---------------------------------------------------------------------------

class WorkspaceGridItem(QGraphicsItem):
    """Alignment grid drawn in scene coordinates so it pans/zooms with the PDF."""

    def __init__(self, rect: QRectF):
        super().__init__()
        self._rect = QRectF(rect)
        self.setZValue(-9_000)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)

    def set_grid_rect(self, rect: QRectF):
        self.prepareGeometryChange()
        self._rect = QRectF(rect)
        self.update()

    def boundingRect(self) -> QRectF:
        return self._rect

    def paint(self, painter: QPainter, option, widget=None):
        left = int(self._rect.left())
        right = int(self._rect.right())
        top = int(self._rect.top())
        bottom = int(self._rect.bottom())
        spacing = int(GRID_SPACING)

        for x in range(left, right + 1, spacing):
            is_major = ((x - left) // spacing) % GRID_MAJOR_EVERY == 0
            color = QColor("#9fb3c8" if is_major else "#d9e2ec")
            width = 1.5 if is_major else 1.0
            painter.setPen(QPen(color, width))
            painter.drawLine(x, top, x, bottom)

        for y in range(top, bottom + 1, spacing):
            is_major = ((y - top) // spacing) % GRID_MAJOR_EVERY == 0
            color = QColor("#9fb3c8" if is_major else "#d9e2ec")
            width = 1.5 if is_major else 1.0
            painter.setPen(QPen(color, width))
            painter.drawLine(left, y, right, y)


class WorkspaceView(QGraphicsView):
    """
    Central diagram view: pan/zoom, drop equipment, move symbols,
    drag port-to-port connections, and delete selection.
    """

    def __init__(self, scene: QGraphicsScene, parent=None):
        super().__init__(scene, parent)
        self.setAcceptDrops(True)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

        # PDF background (optional) and alignment grid (above PDF, below symbols)
        self._background_item: QGraphicsPixmapItem | None = None
        self._grid_item: WorkspaceGridItem | None = None
        self._init_grid()

        # In-progress port-to-port wire drag
        self._pending: tuple[OneLineSymbolItem, str] | None = None
        self._temp_line: QGraphicsLineItem | None = None
        self._wire_start_pos: QPointF | None = None
        self._wiring = False
        self._saved_drag_mode = QGraphicsView.DragMode.ScrollHandDrag

        # Move tracking for undo (captured on press, committed on release)
        self._move_origins: dict[OneLineSymbolItem, QPointF] = {}

        self.undo_stack: QUndoStack | None = None
        self.status_callback: Callable[[str], None] | None = None

    def _init_grid(self):
        scn = self.scene()
        if scn is None:
            return
        self._grid_item = WorkspaceGridItem(scn.sceneRect())
        self._grid_item.setOpacity(DEFAULT_GRID_OPACITY)
        scn.addItem(self._grid_item)

    def _sync_grid_to_scene(self):
        scn = self.scene()
        if scn is None or self._grid_item is None:
            return
        self._grid_item.set_grid_rect(scn.sceneRect())

    def set_grid_visible(self, visible: bool):
        if self._grid_item is not None:
            self._grid_item.setVisible(visible)

    def set_grid_opacity(self, percent: int):
        if self._grid_item is not None:
            self._grid_item.setOpacity(max(0.0, min(percent / 100.0, 1.0)))

    # --- Status & undo helpers ----------------------------------------------

    def _set_status(self, text: str):
        if callable(self.status_callback):
            self.status_callback(text)

    def _push(self, command: QUndoCommand):
        if self.undo_stack is not None:
            self.undo_stack.push(command)
        else:
            command.redo()

    # --- Wiring helpers -----------------------------------------------------

    def _cancel_pending(self):
        self._pending = None
        self._wire_start_pos = None
        self._wiring = False
        if self._temp_line is not None:
            scn = self.scene()
            if scn is not None:
                scn.removeItem(self._temp_line)
            self._temp_line = None
        self.setDragMode(self._saved_drag_mode)
        self.unsetCursor()

    def _find_port_at(self, scene_pos: QPointF):
        scn = self.scene()
        if scn is None:
            return None
        candidates = [
            it for it in scn.items(scene_pos)
            if isinstance(it, OneLineSymbolItem)
        ]
        if not candidates:
            candidates = [it for it in scn.items() if isinstance(it, OneLineSymbolItem)]
        best = None
        best_dist = PORT_HIT_RADIUS
        for item in candidates:
            port = item.nearest_port(scene_pos, PORT_HIT_RADIUS)
            if port is None:
                continue
            delta = item.port_scene_pos(port) - scene_pos
            dist = (delta.x() ** 2 + delta.y() ** 2) ** 0.5
            if dist <= best_dist:
                best_dist = dist
                best = (item, port)
        return best

    # --- Move tracking for undo ---------------------------------------------

    def _capture_move_origins(self, scene_pos: QPointF):
        scn = self.scene()
        if scn is None:
            return
        origins: dict[OneLineSymbolItem, QPointF] = {}
        selected = [it for it in scn.selectedItems() if isinstance(it, OneLineSymbolItem)]
        if selected:
            for item in selected:
                origins[item] = QPointF(item.pos())
        else:
            for it in scn.items(scene_pos):
                if isinstance(it, OneLineSymbolItem):
                    origins[it] = QPointF(it.pos())
                    break
        self._move_origins = origins

    def _commit_moves_if_any(self):
        moves: list[tuple[OneLineSymbolItem, QPointF, QPointF]] = []
        for item, old_pos in self._move_origins.items():
            new_pos = QPointF(item.pos())
            if (new_pos - old_pos).manhattanLength() > 0.5:
                moves.append((item, old_pos, new_pos))
        self._move_origins = {}
        if moves:
            self._push(MoveEquipmentCommand(moves))
            if len(moves) == 1:
                self._set_status(f"Moved {moves[0][0].instance_id}")
            else:
                self._set_status(f"Moved {len(moves)} items")

    # --- Scene edits --------------------------------------------------------

    def delete_selected(self):
        scn = self.scene()
        if scn is None:
            return
        selected: list[QGraphicsItem] = []
        for item in scn.selectedItems():
            if item is self._background_item:
                continue
            if isinstance(item, (OneLineSymbolItem, ConnectionItem)):
                selected.append(item)
        if not selected:
            return
        self._push(DeleteSelectionCommand(scn, selected))
        self._set_status("Deleted selection")

    def set_background_pixmap(self, pixmap: QPixmap):
        scn = self.scene()
        if self._background_item is not None and scn is not None:
            scn.removeItem(self._background_item)
            self._background_item = None

        self._background_item = QGraphicsPixmapItem(pixmap)
        self._background_item.setZValue(-10_000)
        self._background_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self._background_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        scn = self.scene()
        if scn is not None:
            scn.addItem(self._background_item)
            scn.setSceneRect(self._background_item.boundingRect())
            self._sync_grid_to_scene()
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    # --- Keyboard & mouse input ---------------------------------------------

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self._pending is not None:
            self._cancel_pending()
            self._set_status("Connection cancelled")
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton and self._pending is not None:
            self._cancel_pending()
            self._set_status("Connection cancelled")
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(event.position().toPoint())
            hit = self._find_port_at(scene_pos)
            if hit is not None:
                # Start a port-to-port wire drag.
                item, port = hit
                self._move_origins = {}
                self._saved_drag_mode = self.dragMode()
                self.setDragMode(QGraphicsView.DragMode.NoDrag)
                self.setCursor(Qt.CursorShape.CrossCursor)
                self._pending = (item, port)
                self._wire_start_pos = scene_pos
                self._wiring = False
                self._temp_line = QGraphicsLineItem()
                self._temp_line.setPen(QPen(QColor("#2f6fed"), 2, Qt.PenStyle.DashLine))
                self._temp_line.setZValue(200)
                start = item.port_scene_pos(port)
                self._temp_line.setLine(start.x(), start.y(), start.x(), start.y())
                self._temp_line.setVisible(False)
                self.scene().addItem(self._temp_line)
                self._set_status(f"Drag to a port from {item.instance_id}:{port}")
                event.accept()
                return

            # Not on a port — track positions for a possible move undo.
            self._capture_move_origins(scene_pos)

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pending is not None and self._temp_line is not None:
            from_item, from_port = self._pending
            start = from_item.port_scene_pos(from_port)
            end = self.mapToScene(event.position().toPoint())
            if self._wire_start_pos is not None and not self._wiring:
                delta = end - self._wire_start_pos
                dist = (delta.x() ** 2 + delta.y() ** 2) ** 0.5
                if dist >= WIRE_DRAG_THRESHOLD:
                    self._wiring = True
                    self._temp_line.setVisible(True)
            if self._wiring:
                self._temp_line.setLine(start.x(), start.y(), end.x(), end.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._pending is not None:
            from_item, from_port = self._pending
            scene_pos = self.mapToScene(event.position().toPoint())
            hit = self._find_port_at(scene_pos)

            if self._wiring and hit is not None:
                to_item, to_port = hit
                if not (from_item is to_item and from_port == to_port):
                    self._push(
                        AddConnectionCommand(
                            self.scene(), from_item, from_port, to_item, to_port
                        )
                    )
                    self._cancel_pending()
                    self._set_status(
                        f"Connected {from_item.instance_id}:{from_port} → "
                        f"{to_item.instance_id}:{to_port}"
                    )
                    event.accept()
                    return

            was_wiring = self._wiring
            self._cancel_pending()
            self._set_status("Connection cancelled" if was_wiring else "Ready")
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self._move_origins:
            super().mouseReleaseEvent(event)
            self._commit_moves_if_any()
            return

        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            steps = event.angleDelta().y() / 120.0
            if steps == 0:
                return
            factor = 1.15 ** steps
            self.scale(factor, factor)
            event.accept()
            return
        super().wheelEvent(event)

    # --- Drag-and-drop from equipment library -------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(MIME_EQUIPMENT):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(MIME_EQUIPMENT):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(MIME_EQUIPMENT):
            super().dropEvent(event)
            return

        equip_id = bytes(event.mimeData().data(MIME_EQUIPMENT)).decode("utf-8")
        meta = EQUIPMENT_DEFS.get(equip_id)
        if not meta:
            event.ignore()
            return

        pos = self.mapToScene(event.position().toPoint())
        symbol = meta["factory"]()
        symbol.setPos(pos)
        symbol.setZValue(100)
        self._push(AddEquipmentCommand(self.scene(), symbol))
        self._set_status(f"Placed {symbol.instance_id}")
        event.acceptProposedAction()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class SubstationGuiMockup(QMainWindow):
    """Top-level window assembling header, panels, canvas, and footer."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI-Assisted Substation Design Tool")
        self.setMinimumSize(1400, 800)
        self._apply_stylesheet()
        self._build_ui()
        self._setup_undo_redo()

    def _apply_stylesheet(self):
        self.setStyleSheet("""
    QMainWindow { background-color: #f4f6f8; }

    QLabel, QGroupBox { color: #1f2933; }

    QGroupBox {
        background: white;
        border: 1px solid #d9e2ec;
        border-radius: 12px;
        margin-top: 20px;
        font-weight: bold;
        font-size: 12pt;
        padding-top: 25px;
    }

    QGroupBox::title {
        subcontrol-origin: margin;
        left: 16px;
        padding: 0 8px;
        color: #1f2933;
    }

    QPushButton {
        background-color: #2f6fed; color: white; border: none;
        border-radius: 8px; padding: 8px 16px; font-weight: 600;
    }

    QPushButton:disabled {
        background-color: #9fb3c8;
        color: #e4e7eb;
    }

    QLineEdit, QTextEdit, QComboBox, QListWidget {
        background: white; border: 1px solid #cbd2d9;
        border-radius: 8px; padding: 8px; color: #1f2933;
    }
    QFrame#canvasFrame { background: white; border: 2px dashed #9fb3c8; border-radius: 14px; }
    QFrame#toolbarFrame { background: white; border: 1px solid #d9e2ec; border-radius: 12px; }
""")

    # --- Undo / redo wiring -------------------------------------------------

    def _setup_undo_redo(self):
        self.undo_stack = QUndoStack(self)
        if hasattr(self, "workspace_view"):
            self.workspace_view.undo_stack = self.undo_stack

        self.undo_action = self.undo_stack.createUndoAction(self, "Undo")
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.redo_action = self.undo_stack.createRedoAction(self, "Redo")
        self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        self.addAction(self.undo_action)
        self.addAction(self.redo_action)

        if hasattr(self, "undo_btn"):
            self.undo_btn.clicked.connect(self.undo_stack.undo)
            self.redo_btn.clicked.connect(self.undo_stack.redo)
            self.undo_stack.canUndoChanged.connect(self.undo_btn.setEnabled)
            self.undo_stack.canRedoChanged.connect(self.redo_btn.setEnabled)
            self.undo_btn.setEnabled(self.undo_stack.canUndo())
            self.redo_btn.setEnabled(self.undo_stack.canRedo())

        self.undo_stack.indexChanged.connect(self._on_undo_index_changed)

    def _on_undo_index_changed(self, _index: int):
        if not hasattr(self, "footer_status_label"):
            return
        text = self.undo_stack.undoText() if self.undo_stack.canUndo() else ""
        self.footer_status_label.setText(
            f"Ready — last action: {text}" if text else "Ready"
        )

    # --- Layout assembly ----------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(22, 22, 22, 22)
        main_layout.setSpacing(18)

        main_layout.addWidget(self._build_header())
        main_layout.addLayout(self._build_content())
        main_layout.addWidget(self._build_footer())

        if hasattr(self, "workspace_view"):
            self.workspace_view.status_callback = self.footer_status_label.setText
        self._setup_grid_controls()

    def _setup_grid_controls(self):
        self.grid_show_cb = QCheckBox("Show Grid")
        self.grid_show_cb.setChecked(True)
        self.grid_show_cb.toggled.connect(self.workspace_view.set_grid_visible)

        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("Grid Opacity:"))
        self.grid_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.grid_opacity_slider.setRange(0, 100)
        self.grid_opacity_slider.setValue(int(DEFAULT_GRID_OPACITY * 100))
        self.grid_opacity_label = QLabel(f"{self.grid_opacity_slider.value()}%")
        opacity_row.addWidget(self.grid_opacity_slider, 1)
        opacity_row.addWidget(self.grid_opacity_label)

        def _on_opacity_changed(value: int):
            self.grid_opacity_label.setText(f"{value}%")
            self.workspace_view.set_grid_opacity(value)

        self.grid_opacity_slider.valueChanged.connect(_on_opacity_changed)

        self.controls_layout.addWidget(self.grid_show_cb)
        self.controls_layout.addLayout(opacity_row)

    def _build_footer(self):
        footer = QFrame()
        footer.setStyleSheet(
            "QFrame { background: white; border: 1px solid #d9e2ec; border-radius: 10px; }"
            "QLabel { color: #52606d; }"
        )
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(12, 6, 12, 6)

        self.response_time_label = QLabel("Response time: -- ms")
        self.footer_status_label = QLabel("Ready")

        footer_layout.addWidget(self.response_time_label)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.footer_status_label)
        return footer

    def _build_header(self):
        frame = QFrame()
        frame.setObjectName("toolbarFrame")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(20, 14, 20, 14)

        title = QLabel("AI-Assisted Substation Protection & Control Design")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))

        project_box = QComboBox()
        project_box.addItems(["Demo Project - One Line A", "Breaker-and-a-Half Yard", "Ring Bus Example"])
        project_box.setMinimumWidth(320)

        save_btn = QPushButton("Save Layout")
        self.load_btn = QPushButton("Import PDF")
        self.load_btn.clicked.connect(self.on_import_pdf_clicked)
        run_btn = QPushButton("Run Evaluation")

        layout.addWidget(title)
        layout.addStretch()
        layout.addWidget(QLabel("Project:"))
        layout.addWidget(project_box)
        layout.addWidget(self.load_btn)
        layout.addWidget(save_btn)
        layout.addWidget(run_btn)
        return frame

    def _build_content(self):
        layout = QHBoxLayout()
        layout.setSpacing(18)
        layout.addWidget(self._build_left_panel(), 2)
        layout.addWidget(self._build_center_panel(), 5)
        layout.addWidget(self._build_right_panel(), 3)
        return layout

    def _build_left_panel(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(16)
        layout.setContentsMargins(0, 0, 0, 0)

        # Create Component button (sits above the Equipment Library)
        self.create_component_btn = QPushButton("Create Component")
        self.create_component_btn.clicked.connect(self._on_create_component_clicked)
        layout.addWidget(self.create_component_btn)

        # Equipment palette
        palette_group = QGroupBox("Equipment Library")
        palette_layout = QVBoxLayout(palette_group)

        search = QLineEdit()
        search.setPlaceholderText("Search equipment...")
        palette_layout.addWidget(search)

        self.equipment_list = EquipmentList()
        for equip_id, meta in EQUIPMENT_DEFS.items():
            item = QListWidgetItem(meta["label"])
            item.setData(Qt.ItemDataRole.UserRole, equip_id)
            self.equipment_list.addItem(item)
        palette_layout.addWidget(self.equipment_list)

        def _filter_equipment(text: str):
            t = (text or "").strip().lower()
            for i in range(self.equipment_list.count()):
                it = self.equipment_list.item(i)
                it.setHidden(t not in (it.text() or "").lower())

        search.textChanged.connect(_filter_equipment)

        # Quick actions
        self.controls_group = QGroupBox("Quick Actions")
        self.controls_layout = QVBoxLayout(self.controls_group)

        self.undo_btn = QPushButton("Undo")
        self.redo_btn = QPushButton("Redo")
        self.controls_layout.addWidget(self.undo_btn)
        self.controls_layout.addWidget(self.redo_btn)

        delete_btn = QPushButton("Delete Selected")
        delete_btn.clicked.connect(
            lambda: self.workspace_view.delete_selected() if hasattr(self, "workspace_view") else None
        )
        self.controls_layout.addWidget(delete_btn)

        layout.addWidget(palette_group, 3)
        layout.addWidget(self.controls_group, 1)
        return container

    def _build_center_panel(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(16)
        layout.setContentsMargins(0, 0, 0, 0)

        canvas = QFrame()
        canvas.setObjectName("canvasFrame")
        canvas_layout = QVBoxLayout(canvas)

        self.workspace_scene = QGraphicsScene(self)
        self.workspace_scene.setSceneRect(
            QRectF(0, 0, DEFAULT_WORKSPACE_WIDTH, DEFAULT_WORKSPACE_HEIGHT)
        )
        self.workspace_view = WorkspaceView(self.workspace_scene, self)
        self.workspace_view.setStyleSheet("background: white; border: none;")

        self._workspace_original = None
        canvas_layout.addWidget(self.workspace_view, 1)

        self.workspace_hint = QLabel(
            "Drag blue ports to connect.\nCtrl+Z / Ctrl+Y undo-redo. Ctrl+scroll zooms."
        )
        self.workspace_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.workspace_hint.setStyleSheet("color: #52606d; font-size: 13pt; margin-top: 10px;")
        canvas_layout.addWidget(self.workspace_hint)

        layout.addWidget(canvas, 1)
        return container

    def _build_right_panel(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(16)
        layout.setContentsMargins(0, 0, 0, 0)

        # Properties for the selected symbol or connection
        properties_group = QGroupBox("Selected Element Properties")
        properties_layout = QGridLayout(properties_group)
        properties_layout.setHorizontalSpacing(16)
        properties_layout.setVerticalSpacing(12)

        properties_layout.addWidget(QLabel("Type:"), 0, 0)
        self.prop_type = QLineEdit("—")
        self.prop_type.setReadOnly(True)
        properties_layout.addWidget(self.prop_type, 0, 1)
        properties_layout.addWidget(QLabel("Name:"), 1, 0)
        self.prop_name = QLineEdit("—")
        properties_layout.addWidget(self.prop_name, 1, 1)

        properties_layout.addWidget(QLabel("Status:"), 2, 0)
        status_cb = QComboBox()
        status_cb.addItems(["Closed", "Open", "Maintenance"])
        properties_layout.addWidget(status_cb, 2, 1)

        properties_layout.addWidget(QLabel("Trip Coil 1 (A):"), 3, 0)
        self.prop_trip_coil_1 = QLineEdit("—")
        self.prop_trip_coil_1.setReadOnly(True)
        properties_layout.addWidget(self.prop_trip_coil_1, 3, 1)

        properties_layout.addWidget(QLabel("Trip Coil 2 (A):"), 4, 0)
        self.prop_trip_coil_2 = QLineEdit("—")
        self.prop_trip_coil_2.setReadOnly(True)
        properties_layout.addWidget(self.prop_trip_coil_2, 4, 1)

        properties_layout.addWidget(QLabel("Close Coil (A):"), 5, 0)
        self.prop_close_coil = QLineEdit("—")
        self.prop_close_coil.setReadOnly(True)
        properties_layout.addWidget(self.prop_close_coil, 6, 1)

        properties_layout.addWidget(QLabel("Motor Inrush Current (A):"), 6, 0)
        self.prop_motor_inrush = QLineEdit("—")
        self.prop_motor_inrush.setReadOnly(True)
        properties_layout.addWidget(self.prop_motor_inrush, 6, 1)

        properties_layout.addWidget(QLabel("Motor Run Current (A):"), 7, 0)
        self.prop_motor_run = QLineEdit("—")
        self.prop_motor_run.setReadOnly(True)
        properties_layout.addWidget(self.prop_motor_run, 7, 1)

        analysis_group = QGroupBox("Analysis Output")
        analysis_layout = QVBoxLayout(analysis_group)
        self.output_box = QTextEdit()
        self.output_box.setPlaceholderText("Analysis results will appear here...")
        self.output_box.setReadOnly(True)
        analysis_layout.addWidget(self.output_box)

        notes_group = QGroupBox("Engineer Notes")
        notes_layout = QVBoxLayout(notes_group)
        notes = QTextEdit()
        notes.setPlaceholderText("Add design notes here...")
        notes_layout.addWidget(notes)

        layout.addWidget(properties_group, 1)
        layout.addWidget(analysis_group, 2)
        layout.addWidget(notes_group, 2)

        if hasattr(self, "workspace_scene"):
            self.workspace_scene.selectionChanged.connect(self._on_selection_changed)

        return container

    # --- Event handlers -----------------------------------------------------

    def _on_create_component_clicked(self):
        """
        Open the Create Component popup (from customWidgetTool.py). On
        accept, register the new component as an EQUIPMENT_DEFS entry (so
        it appears in the Equipment Library) and add a matching list entry
        so it can be dragged into the sandbox like any built-in symbol.
        """
        dialog = ComponentDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        name = dialog.component_name()
        if not name:
            QMessageBox.warning(self, "Create Component", "Please enter a component name.")
            return

        data = dialog.component_data()
        properties = {
            "rating_kv": data["Rating"],
            "trip_coil_1_a": data["TripCoil1"],
            "trip_coil_2_a": data["TripCoil2"],
            "close_coil_a": data["CloseCoil"],
            "motor_inrush_a": data["MotorInrushCurrent"],
            "motor_run_a": data["MotorRunCurrent"],
        }

        slug = "".join(ch if ch.isalnum() else "_" for ch in name.strip().lower())
        equip_id = f"custom_{slug}" if slug else f"custom_{len(EQUIPMENT_DEFS)}"
        base_id = equip_id
        suffix = 1
        while equip_id in EQUIPMENT_DEFS:
            suffix += 1
            equip_id = f"{base_id}_{suffix}"

        EQUIPMENT_DEFS[equip_id] = {
            "label": name,
            "factory": functools.partial(
                CustomComponentItem, name=name, properties=properties
            ),
        }

        item = QListWidgetItem(name)
        item.setData(Qt.ItemDataRole.UserRole, equip_id)
        self.equipment_list.addItem(item)

        if hasattr(self, "footer_status_label"):
            self.footer_status_label.setText(f"Added '{name}' to the Equipment Library")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "workspace_view") and getattr(self, "_workspace_original", None):
            self.workspace_view.fitInView(self.workspace_scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def on_import_pdf_clicked(self):
        """Render the first page of a PDF as the canvas background."""
        pdf_path, _ = QFileDialog.getOpenFileName(self, "Select PDF", "", "PDF Files (*.pdf)")
        if not pdf_path:
            return

        try:
            doc = pymupdf.open(pdf_path)
            try:
                page = doc.load_page(0)
                zoom = 2.0
                mat = pymupdf.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                png_bytes = pix.tobytes("png")
            finally:
                doc.close()
        except Exception as e:
            if hasattr(self, "footer_status_label"):
                self.footer_status_label.setText(f"Import failed: {e}")
            return

        pm = QPixmap()
        if not pm.loadFromData(png_bytes):
            if hasattr(self, "footer_status_label"):
                self.footer_status_label.setText("Import failed: could not decode rendered PNG")
            return

        self._workspace_original = pm
        if hasattr(self, "workspace_hint"):
            self.workspace_hint.hide()
        if hasattr(self, "workspace_view"):
            self.workspace_view.set_background_pixmap(pm)
        if hasattr(self, "footer_status_label"):
            self.footer_status_label.setText(f"Imported: {pdf_path}")

    def _on_selection_changed(self):
        """Update the properties panel when the user selects a symbol or wire."""
        if not hasattr(self, "workspace_scene"):
            return
        items = self.workspace_scene.selectedItems()
        if not items:
            self.prop_type.setText("—")
            self.prop_name.setText("—")
            self._clear_custom_component_properties()
            return

        item = items[0]
        if isinstance(item, OneLineSymbolItem):
            self.prop_type.setText(item.label)
            self.prop_name.setText(item.instance_id)
            if isinstance(item, CustomComponentItem):
                self._set_custom_component_properties(item.properties)
            else:
                self._clear_custom_component_properties()
        elif isinstance(item, ConnectionItem):
            self.prop_type.setText("Connection")
            self.prop_name.setText(
                f"{item.from_item.instance_id}:{item.from_port} → "
                f"{item.to_item.instance_id}:{item.to_port}"
            )
            self._clear_custom_component_properties()
        else:
            self.prop_type.setText(type(item).__name__)
            self._clear_custom_component_properties()

    def _set_custom_component_properties(self, properties: dict):
        """Fill the Trip Coil / Motor Current rows from a CustomComponentItem."""
        self.prop_trip_coil_1.setText(f"{properties.get('trip_coil_1_a', 0):g}")
        self.prop_trip_coil_2.setText(f"{properties.get('trip_coil_2_a', 0):g}")
        self.prop_close_coil.setText(f"{properties.get('close_coil_a', 0):g}")
        self.prop_motor_inrush.setText(f"{properties.get('motor_inrush_a', 0):g}")
        self.prop_motor_run.setText(f"{properties.get('motor_run_a', 0):g}")

    def _clear_custom_component_properties(self):
        """Reset the Trip Coil / Motor Current rows for non-custom selections."""
        self.prop_trip_coil_1.setText("—")
        self.prop_trip_coil_2.setText("—")
        self.prop_close_coil.setText("—")
        self.prop_motor_inrush.setText("—")
        self.prop_motor_run.setText("—")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 11))

    window = SubstationGuiMockup()
    window.showMaximized()
    sys.exit(app.exec())