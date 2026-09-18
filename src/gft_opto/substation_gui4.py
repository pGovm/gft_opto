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
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QPointF, QRectF, QByteArray, QMimeData
from PySide6.QtGui import (
    QFont, QPixmap, QImage, QDrag, QPainter, QPen, QBrush, QColor,
    QKeySequence, QUndoStack, QUndoCommand, QPainterPath, QTransform,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QPushButton, QSlider, QTextEdit, QVBoxLayout, QWidget, QFileDialog,
    QGraphicsView, QGraphicsScene, QGraphicsItem, QGraphicsPixmapItem,
    QGraphicsPathItem, QDialog,
    QMessageBox,
)

import pymupdf
import functools

from gft_opto.customWidgetTool import ComponentDialog
from gft_opto.test_netlist import run_fake_evaluation


# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------

MIME_EQUIPMENT = "application/x-substation-equipment"
PORT_HIT_RADIUS = 14.0       # px — how close the cursor must be to snap to a port
PORT_DOT_RADIUS = 4.0        # px — visual size of port handles
WIRE_DRAG_THRESHOLD = 6.0    # px — min drag before rubber-band wire appears
WIRE_HIT_RADIUS = 10.0       # px — how close to a wire to tee / place a junction
DEFAULT_WORKSPACE_WIDTH = 2400.0
DEFAULT_WORKSPACE_HEIGHT = 1800.0
GRID_SPACING = 25.0          # scene units — matches PDF pixel coords after import
GRID_MAJOR_EVERY = 5         # every Nth line is drawn heavier
DEFAULT_GRID_OPACITY = 0.25
RESIZE_HANDLE_SIZE = 8.0     # px — corner handle size in item coords
RESIZE_HANDLE_HIT = 12.0     # px — how close the cursor must be to grab a handle
MIN_SYMBOL_SCALE = 0.5
MAX_SYMBOL_SCALE = 3.0
SYMBOL_SCALE_STEP = 0.25     # snap resize to this increment
SYMBOL_ROTATION_STEP = 90.0  # degrees — R / Shift+R and Quick Actions
ROTATE_HANDLE_OFFSET = 28.0  # px above symbol AABB top edge
ROTATE_HANDLE_RADIUS = 8.0
ROTATE_HANDLE_HIT = 14.0
ROTATE_DRAG_SNAP = 15.0      # degrees — snap while dragging the rotate handle
UI_ACCENT = "#006A4E"        # bottle green — primary GUI accent
GFT_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "gft_logo.png"
GFT_LOGO_HEIGHT = 34         # px — header wordmark height

_instance_counters: dict[str, int] = defaultdict(int)


def load_gft_logo_pixmap(height: int = GFT_LOGO_HEIGHT) -> QPixmap:
    """Load the GFT wordmark, clear near-white background, and size for the header."""
    image = QImage(str(GFT_LOGO_PATH))
    if image.isNull():
        return QPixmap()

    # Shrink first so chroma-keying stays cheap at startup.
    work_h = max(height * 3, 96)
    image = image.scaledToHeight(work_h, Qt.TransformationMode.SmoothTransformation)
    image = image.convertToFormat(QImage.Format.Format_ARGB32)

    width, height_px = image.width(), image.height()
    min_x, min_y = width, height_px
    max_x, max_y = -1, -1
    for y in range(height_px):
        for x in range(width):
            c = image.pixelColor(x, y)
            if c.red() > 220 and c.green() > 220 and c.blue() > 220:
                c.setAlpha(0)
                image.setPixelColor(x, y, c)
            elif c.alpha() > 0:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)

    if max_x >= min_x and max_y >= min_y:
        pad = 2
        image = image.copy(
            max(0, min_x - pad),
            max(0, min_y - pad),
            min(width, max_x + pad + 1) - max(0, min_x - pad),
            min(height_px, max_y + pad + 1) - max(0, min_y - pad),
        )

    return QPixmap.fromImage(image).scaledToHeight(
        height, Qt.TransformationMode.SmoothTransformation
    )


def next_instance_id(equip_type: str) -> str:
    """Assign a unique id per placed symbol (e.g. mos_1, mos_2)."""
    _instance_counters[equip_type] += 1
    return f"{equip_type}_{_instance_counters[equip_type]}"


def snap_point_to_grid(p: QPointF, spacing: float = GRID_SPACING) -> QPointF:
    """Round a scene point to the nearest grid intersection."""
    return QPointF(
        round(p.x() / spacing) * spacing,
        round(p.y() / spacing) * spacing,
    )


def position_for_snapped_port(
    proposed_pos: QPointF,
    port_local: QPointF,
    spacing: float = GRID_SPACING,
) -> QPointF:
    """Return item position that places port_local on the grid."""
    port_scene = proposed_pos + port_local
    snapped = snap_point_to_grid(port_scene, spacing)
    return snapped - port_local


def default_snap_port(item: "OneLineSymbolItem") -> str:
    """Pick a sensible anchor port when the user did not click on one."""
    ports = item.ports()
    for preferred in ("left", "H", "H1", "line", "node"):
        if preferred in ports:
            return preferred
    return next(iter(ports))


def route_wire_on_grid(
    p1: QPointF,
    p2: QPointF,
    *,
    spacing: float = GRID_SPACING,
    snap_endpoints: bool = True,
) -> list[QPointF]:
    """Return orthogonal polyline vertices that follow the alignment grid."""
    if snap_endpoints:
        p1 = snap_point_to_grid(p1, spacing)
        p2 = snap_point_to_grid(p2, spacing)

    if abs(p1.y() - p2.y()) < 0.01:
        return [p1, p2]
    if abs(p1.x() - p2.x()) < 0.01:
        return [p1, p2]

    bend_x = snap_point_to_grid(
        QPointF((p1.x() + p2.x()) / 2, p1.y()),
        spacing,
    ).x()
    return [p1, QPointF(bend_x, p1.y()), QPointF(bend_x, p2.y()), p2]


def wire_path_from_points(points: list[QPointF]) -> QPainterPath:
    """Build a painter path from a polyline, skipping duplicate vertices."""
    if not points:
        return QPainterPath()

    simplified: list[QPointF] = [points[0]]
    for pt in points[1:]:
        if (pt - simplified[-1]).manhattanLength() > 0.01:
            simplified.append(pt)

    path = QPainterPath(simplified[0])
    for pt in simplified[1:]:
        path.lineTo(pt)
    return path


def simplify_route_points(points: list[QPointF]) -> list[QPointF]:
    if not points:
        return []
    simplified: list[QPointF] = [QPointF(points[0])]
    for pt in points[1:]:
        if (pt - simplified[-1]).manhattanLength() > 0.01:
            simplified.append(QPointF(pt))
    return simplified


def point_to_segment_distance(p: QPointF, a: QPointF, b: QPointF) -> tuple[float, QPointF]:
    """Return (distance, closest point on segment ab to p)."""
    ab = b - a
    len_sq = ab.x() ** 2 + ab.y() ** 2
    if len_sq < 1e-9:
        return ((p - a).manhattanLength(), QPointF(a))
    t = ((p.x() - a.x()) * ab.x() + (p.y() - a.y()) * ab.y()) / len_sq
    t = max(0.0, min(1.0, t))
    closest = QPointF(a.x() + t * ab.x(), a.y() + t * ab.y())
    delta = p - closest
    return ((delta.x() ** 2 + delta.y() ** 2) ** 0.5, closest)


# ---------------------------------------------------------------------------
# Symbol graphics items
# ---------------------------------------------------------------------------

class OneLineSymbolItem(QGraphicsItem):
    """Base class for draggable one-line equipment symbols with named ports."""

    snap_to_grid_enabled = True

    def __init__(self, equip_type: str, label: str, ports: dict[str, QPointF]):
        super().__init__()
        self.equip_type = equip_type
        self.equip_id = equip_type  # legacy alias used by the properties panel
        self.instance_id = next_instance_id(equip_type)
        self.label = label
        self._base_ports = dict(ports)
        self._connections: list["ConnectionItem"] = []
        self._snap_port_name: str | None = None
        self.scale_factor = 1.0
        self.rotation_deg = 0.0
        self._rect = QRectF(-40, -20, 80, 40)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )

    # --- Transform (scale + rotation) ---------------------------------------

    def _local_transform(self) -> QTransform:
        """Map base symbol coords → item-local coords (scale, then rotate)."""
        t = QTransform()
        t.rotate(self.rotation_deg)
        t.scale(self.scale_factor, self.scale_factor)
        return t

    def _transformed_point(self, p: QPointF) -> QPointF:
        return self._local_transform().map(p)

    def _transformed_rect(self) -> QRectF:
        return self._local_transform().mapRect(self._rect)

    def set_scale_factor(self, scale: float):
        scale = max(MIN_SYMBOL_SCALE, min(MAX_SYMBOL_SCALE, float(scale)))
        if abs(scale - self.scale_factor) < 1e-6:
            return
        self.prepareGeometryChange()
        self.scale_factor = scale
        self.update()
        for conn in self._connections:
            conn.update_path()

    def set_rotation_deg(self, degrees: float):
        degrees = float(degrees) % 360.0
        if degrees < 0:
            degrees += 360.0
        if abs(degrees - self.rotation_deg) < 1e-6:
            return
        self.prepareGeometryChange()
        self.rotation_deg = degrees
        self.update()
        for conn in self._connections:
            conn.update_path()

    # --- Port helpers -------------------------------------------------------

    def ports(self) -> dict[str, QPointF]:
        return {name: self._transformed_point(pt) for name, pt in self._base_ports.items()}

    def port_scene_pos(self, port_name: str) -> QPointF:
        return self.mapToScene(self._transformed_point(self._base_ports[port_name]))

    def nearest_port(self, scene_pos: QPointF, max_dist: float = PORT_HIT_RADIUS):
        """Return the closest port name within max_dist, or None."""
        best_name = None
        best_dist = max_dist
        for name, local in self.ports().items():
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

    # --- Resize / rotate handles --------------------------------------------

    def resize_handle_points(self) -> dict[str, QPointF]:
        r = self._rect
        t = self._local_transform()
        return {
            "tl": t.map(r.topLeft()),
            "tr": t.map(r.topRight()),
            "bl": t.map(r.bottomLeft()),
            "br": t.map(r.bottomRight()),
        }

    def resize_handle_at(self, local_pos: QPointF, hit_radius: float = RESIZE_HANDLE_HIT):
        """Return handle name under local_pos, or None."""
        if not self.isSelected():
            return None
        best_name = None
        best_dist = hit_radius
        for name, pt in self.resize_handle_points().items():
            delta = pt - local_pos
            dist = (delta.x() ** 2 + delta.y() ** 2) ** 0.5
            if dist <= best_dist:
                best_dist = dist
                best_name = name
        return best_name

    def rotation_handle_pos(self) -> QPointF:
        """Local position of the drag-to-rotate knob above the symbol."""
        r = self._transformed_rect()
        return QPointF(r.center().x(), r.top() - ROTATE_HANDLE_OFFSET)

    def rotation_handle_at(self, local_pos: QPointF, hit_radius: float = ROTATE_HANDLE_HIT) -> bool:
        if not self.isSelected():
            return False
        pt = self.rotation_handle_pos()
        delta = pt - local_pos
        return (delta.x() ** 2 + delta.y() ** 2) ** 0.5 <= hit_radius

    # --- Drawing ------------------------------------------------------------

    def boundingRect(self) -> QRectF:
        body = self._transformed_rect()
        rect = QRectF(body)
        pad = 6.0
        if self.isSelected():
            pad = max(pad, RESIZE_HANDLE_SIZE / 2 + 2)
            handle = self.rotation_handle_pos()
            hr = ROTATE_HANDLE_RADIUS + 2
            handle_rect = QRectF(handle.x() - hr, handle.y() - hr, 2 * hr, 2 * hr)
            top_mid = QPointF(body.center().x(), body.top())
            stem_rect = QRectF(top_mid, handle).normalized()
            rect = rect.united(handle_rect).united(stem_rect)
        return rect.adjusted(-pad, -pad, pad, pad)

    def _pen(self):
        return QPen(Qt.GlobalColor.black, 2)

    def _begin_paint(self, painter: QPainter) -> float:
        """Apply scale+rotation for symbol geometry; keep stroke width constant."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.save()
        painter.setTransform(self._local_transform(), True)
        s = self.scale_factor
        pen = self._pen()
        if s > 0:
            pen.setWidthF(pen.widthF() / s)
        painter.setPen(pen)
        return s

    def _end_paint(self, painter: QPainter):
        painter.restore()
        self._draw_ports(painter)
        self._draw_resize_handles(painter)
        self._draw_rotation_handle(painter)

    def _draw_ports(self, painter: QPainter):
        """Draw port dots so drag-to-connect is easy to discover."""
        color = QColor("#000000") if self.isSelected() else QColor("#1f2933")
        painter.setPen(QPen(color, 1.5))
        painter.setBrush(QBrush(color))
        r = PORT_DOT_RADIUS
        for pt in self.ports().values():
            painter.drawEllipse(QRectF(pt.x() - r, pt.y() - r, 2 * r, 2 * r))

    def _draw_resize_handles(self, painter: QPainter):
        if not self.isSelected():
            return
        half = RESIZE_HANDLE_SIZE / 2
        painter.setPen(QPen(QColor("#1f2933"), 1.0))
        painter.setBrush(QBrush(QColor("#ffffff")))
        for pt in self.resize_handle_points().values():
            painter.drawRect(QRectF(pt.x() - half, pt.y() - half, RESIZE_HANDLE_SIZE, RESIZE_HANDLE_SIZE))

    def _draw_rotation_handle(self, painter: QPainter):
        """Draw stem + circular-arrow knob above the selection (sandbox-style)."""
        if not self.isSelected():
            return
        body = self._transformed_rect()
        top_mid = QPointF(body.center().x(), body.top())
        handle = self.rotation_handle_pos()
        accent = QColor(UI_ACCENT)

        painter.setPen(QPen(accent, 1.5))
        painter.drawLine(top_mid, handle)

        hr = ROTATE_HANDLE_RADIUS
        painter.setBrush(QBrush(QColor("#ffffff")))
        painter.setPen(QPen(accent, 1.5))
        painter.drawEllipse(handle, hr, hr)

        # Circular arrow inside the knob
        arc_rect = QRectF(handle.x() - 4.5, handle.y() - 4.5, 9.0, 9.0)
        painter.drawArc(arc_rect, 40 * 16, 240 * 16)
        # Arrowhead at the arc end (~40°)
        tip_angle = math.radians(40)
        tip = QPointF(
            handle.x() + 4.5 * math.cos(tip_angle),
            handle.y() - 4.5 * math.sin(tip_angle),
        )
        painter.setBrush(QBrush(accent))
        arrow = QPainterPath()
        arrow.moveTo(tip)
        arrow.lineTo(tip + QPointF(-3.5, -1.0))
        arrow.lineTo(tip + QPointF(-0.5, 3.5))
        arrow.closeSubpath()
        painter.drawPath(arrow)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            if self.snap_to_grid_enabled:
                ports = self.ports()
                port_name = self._snap_port_name
                if port_name is None or port_name not in ports:
                    port_name = default_snap_port(self) if ports else None
                if port_name is not None and port_name in ports:
                    value = position_for_snapped_port(value, ports[port_name])
            return super().itemChange(change, value)
        # Keep attached wires in sync when this symbol moves or is selected.
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for conn in self._connections:
                conn.update_path()
        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.prepareGeometryChange()
            self.update()
        return super().itemChange(change, value)


class Transformer2WItem(OneLineSymbolItem):
    """AEP-style two-winding power transformer (interlocking circles)."""

    def __init__(self):
        super().__init__(
            "xfmr_2w",
            "Transformer (2-winding)",
            {"H": QPointF(-40, 0), "X": QPointF(40, 0)},
        )
        self._rect = QRectF(-40, -18, 80, 36)

    def paint(self, painter: QPainter, option, widget=None):
        self._begin_paint(painter)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(-40, 0, -18, 0)
        painter.drawLine(18, 0, 40, 0)
        painter.drawEllipse(QRectF(-18, -14, 28, 28))
        painter.drawEllipse(QRectF(-10, -14, 28, 28))
        self._end_paint(painter)


class Transformer3WItem(OneLineSymbolItem):
    """AEP-style three-winding transformer (three interlocking circles)."""

    def __init__(self):
        super().__init__(
            "xfmr_3w",
            "Transformer (3-winding)",
            {"H1": QPointF(-40, -8), "H2": QPointF(40, -8), "X": QPointF(0, 40)},
        )
        self._rect = QRectF(-40, -28, 80, 68)

    def paint(self, painter: QPainter, option, widget=None):
        self._begin_paint(painter)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(-20, -22, 26, 26))
        painter.drawEllipse(QRectF(-6, -22, 26, 26))
        painter.drawEllipse(QRectF(-13, -4, 26, 26))
        painter.drawLine(-40, -8, -20, -8)
        painter.drawLine(20, -8, 40, -8)
        painter.drawLine(0, 22, 0, 40)
        self._end_paint(painter)


class DisconnectSwitchItem(OneLineSymbolItem):
    """AEP air-break / disconnect: open blade with hinge dots on the centerline."""

    def __init__(self):
        super().__init__(
            "disconnect",
            "Disconnect Switch",
            {"left": QPointF(-36, 0), "right": QPointF(36, 0)},
        )
        self._rect = QRectF(-36, -16, 72, 28)

    def paint(self, painter: QPainter, option, widget=None):
        self._begin_paint(painter)
        painter.drawLine(-36, 0, -12, 0)
        painter.drawLine(12, 0, 36, 0)
        # Open blade (angled up toward the far terminal)
        painter.drawLine(-12, 0, 12, -12)
        painter.setBrush(QBrush(Qt.GlobalColor.black))
        painter.drawEllipse(QRectF(-14, -2, 4, 4))
        painter.drawEllipse(QRectF(10, -2, 4, 4))
        self._end_paint(painter)


class MotorOperatedSwitchItem(OneLineSymbolItem):
    """AEP motor-operated disconnect: open blade + circled M."""

    def __init__(self):
        super().__init__(
            "mos",
            "Motor Operated Switch (MOS)",
            {"left": QPointF(-36, 0), "right": QPointF(36, 0)},
        )
        self._rect = QRectF(-36, -16, 72, 40)

    def paint(self, painter: QPainter, option, widget=None):
        self._begin_paint(painter)
        painter.drawLine(-36, 0, -12, 0)
        painter.drawLine(12, 0, 36, 0)
        painter.drawLine(-12, 0, 12, -12)
        painter.setBrush(QBrush(Qt.GlobalColor.black))
        painter.drawEllipse(QRectF(-14, -2, 4, 4))
        painter.drawEllipse(QRectF(10, -2, 4, 4))
        # Circled M (motor operator), AEP style
        painter.setBrush(QBrush(Qt.GlobalColor.white))
        painter.drawEllipse(QRectF(-8, 8, 16, 16))
        painter.setFont(QFont("Sans-Serif", 7, QFont.Weight.Bold))
        painter.drawText(QRectF(-8, 8, 16, 16), Qt.AlignmentFlag.AlignCenter, "M")
        self._end_paint(painter)


class CircuitBreakerItem(OneLineSymbolItem):
    """AEP circuit breaker: square on the centerline."""

    def __init__(self):
        super().__init__(
            "breaker",
            "Circuit Breaker",
            {"left": QPointF(-32, 0), "right": QPointF(32, 0)},
        )
        self._rect = QRectF(-32, -12, 64, 24)

    def paint(self, painter: QPainter, option, widget=None):
        self._begin_paint(painter)
        painter.drawLine(-32, 0, -10, 0)
        painter.drawLine(10, 0, 32, 0)
        painter.setBrush(QBrush(Qt.GlobalColor.white))
        painter.drawRect(QRectF(-10, -10, 20, 20))
        self._end_paint(painter)


class CurrentTransformerItem(OneLineSymbolItem):
    """AEP CT: conductor with two core loops."""

    def __init__(self):
        super().__init__(
            "ct",
            "Current Transformer (CT/BCT)",
            {"left": QPointF(-28, 0), "right": QPointF(28, 0)},
        )
        self._rect = QRectF(-28, -14, 56, 28)

    def paint(self, painter: QPainter, option, widget=None):
        self._begin_paint(painter)
        painter.drawLine(-28, 0, 28, 0)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # Classic AEP CT: two open loops sitting on the conductor
        painter.drawArc(QRectF(-16, -12, 16, 16), 0 * 16, 180 * 16)
        painter.drawArc(QRectF(0, -12, 16, 16), 0 * 16, 180 * 16)
        self._end_paint(painter)


class PotentialTransformerItem(OneLineSymbolItem):
    """AEP VT/PT tap: small two-winding symbol off the line."""

    def __init__(self):
        super().__init__(
            "pt",
            "Potential Transformer (PT/VT)",
            {"line": QPointF(0, -28), "ground": QPointF(0, 28)},
        )
        self._rect = QRectF(-16, -28, 32, 56)

    def paint(self, painter: QPainter, option, widget=None):
        self._begin_paint(painter)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(0, -28, 0, -12)
        painter.drawEllipse(QRectF(-10, -12, 20, 20))
        painter.drawEllipse(QRectF(-10, -2, 20, 20))
        painter.drawLine(0, 18, 0, 28)
        self._end_paint(painter)



class GroundItem(OneLineSymbolItem):
    """Earth ground: three horizontal bars."""

    def __init__(self):
        super().__init__(
            "ground",
            "Ground",
            {"node": QPointF(0, -16)},
        )
        self._rect = QRectF(-14, -16, 28, 36)

    def paint(self, painter: QPainter, option, widget=None):
        self._begin_paint(painter)
        painter.drawLine(0, -16, 0, 4)
        painter.drawLine(-12, 4, 12, 4)
        painter.drawLine(-8, 10, 8, 10)
        painter.drawLine(-4, 16, 4, 16)
        self._end_paint(painter)


class JunctionItem(OneLineSymbolItem):
    """
    Auto-placed tee node. Looks exactly like a component blue port so you can
    branch more wires from wire bends / mid-wire connection points.
    """

    def __init__(self):
        super().__init__(
            "junction",
            "Junction",
            {"node": QPointF(0, 0)},
        )
        self._rect = QRectF(-10, -10, 20, 20)

    def paint(self, painter: QPainter, option, widget=None):
        # Match component port styling exactly (no extra cross / body).
        color = QColor("#000000") if self.isSelected() else QColor("#1f2933")
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(color, 1.5))
        painter.setBrush(QBrush(color))
        r = PORT_DOT_RADIUS
        painter.drawEllipse(QRectF(-r, -r, 2 * r, 2 * r))
        self._draw_rotation_handle(painter)

    def resize_handle_at(self, local_pos: QPointF, hit_radius: float = RESIZE_HANDLE_HIT):
        return None

    def _draw_resize_handles(self, painter: QPainter):
        return


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

    def paint(self, painter: QPainter, option, widget=None):
        self._begin_paint(painter)
        painter.setBrush(QBrush(Qt.GlobalColor.white))
        painter.drawRect(self._rect)
        painter.setFont(QFont("Sans-Serif", 8))
        painter.drawText(self._rect, Qt.AlignmentFlag.AlignCenter, self.label)
        self._end_paint(painter)


class ConnectionItem(QGraphicsPathItem):
    """A wire between two symbol ports; routes orthogonally along the grid."""

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
        self._route_points: list[QPointF] = []
        self.setPen(QPen(QColor("#1f2933"), 2.5))
        self.setZValue(50)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.attach()
        self.update_path()

    def route_points(self) -> list[QPointF]:
        return [QPointF(p) for p in self._route_points]

    def bend_points(self) -> list[QPointF]:
        pts = self._route_points
        if len(pts) <= 2:
            return []
        return [QPointF(p) for p in pts[1:-1]]

    def update_path(self):
        if self.from_item is None or self.to_item is None:
            return
        p1 = self.from_item.port_scene_pos(self.from_port)
        p2 = self.to_item.port_scene_pos(self.to_port)
        points = simplify_route_points(
            route_wire_on_grid(
                p1,
                p2,
                snap_endpoints=OneLineSymbolItem.snap_to_grid_enabled,
            )
        )
        self._route_points = points
        self.setPath(wire_path_from_points(points))
        self.update()

    def paint(self, painter: QPainter, option, widget=None):
        super().paint(painter, option, widget)
        # Ports at orthogonal bend / tee corners (same look as component ports).
        color = QColor("#000000") if self.isSelected() else QColor("#1f2933")
        painter.setPen(QPen(color, 1.5))
        painter.setBrush(QBrush(color))
        r = PORT_DOT_RADIUS
        for pt in self.bend_points():
            local = self.mapFromScene(pt)
            painter.drawEllipse(QRectF(local.x() - r, local.y() - r, 2 * r, 2 * r))

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
    "breaker": {"label": "Circuit Breaker", "factory": CircuitBreakerItem},
    "disconnect": {"label": "Disconnect Switch", "factory": DisconnectSwitchItem},
    "mos": {"label": "Motor Operated Switch (MOS)", "factory": MotorOperatedSwitchItem},
    "ct": {"label": "Current Transformer (CT/BCT)", "factory": CurrentTransformerItem},
    "pt": {"label": "Potential Transformer (PT/VT)", "factory": PotentialTransformerItem},
    "ground": {"label": "Ground", "factory": GroundItem},
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
        # May include JunctionItems + one or more ConnectionItems (bent routes).
        self.created_items: list[QGraphicsItem] = []

    def redo(self):
        if self.created_items:
            for item in self.created_items:
                if item.scene() is not self.scene:
                    self.scene.addItem(item)
                if isinstance(item, ConnectionItem):
                    item.attach()
                    item.update_path()
            return

        p1 = self.from_item.port_scene_pos(self.from_port)
        p2 = self.to_item.port_scene_pos(self.to_port)
        points = simplify_route_points(
            route_wire_on_grid(
                p1,
                p2,
                snap_endpoints=OneLineSymbolItem.snap_to_grid_enabled,
            )
        )

        prev_item: OneLineSymbolItem = self.from_item
        prev_port = self.from_port
        for bend in points[1:-1]:
            junction = JunctionItem()
            pos = snap_point_to_grid(bend) if OneLineSymbolItem.snap_to_grid_enabled else QPointF(bend)
            junction.setPos(pos)
            junction.setZValue(100)
            self.scene.addItem(junction)
            self.created_items.append(junction)
            seg = ConnectionItem(prev_item, prev_port, junction, "node")
            self.scene.addItem(seg)
            self.created_items.append(seg)
            prev_item = junction
            prev_port = "node"

        seg = ConnectionItem(prev_item, prev_port, self.to_item, self.to_port)
        self.scene.addItem(seg)
        self.created_items.append(seg)

    def undo(self):
        for item in reversed(self.created_items):
            if isinstance(item, ConnectionItem):
                item.detach()
            if item.scene() is self.scene:
                self.scene.removeItem(item)


class SplitWireCommand(QUndoCommand):
    """
    Insert a blue-port junction on an existing wire at `point`, splitting it
    into two segments. Optionally also attach a new branch wire to the junction.
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
        self._temp_line: QGraphicsPathItem | None = None
        self._wire_start_pos: QPointF | None = None
        self._wiring = False
        self._saved_drag_mode = QGraphicsView.DragMode.ScrollHandDrag

        # Move tracking for undo (captured on press, committed on release)
        self._move_origins: dict[OneLineSymbolItem, QPointF] = {}
        self._snap_ports: dict[OneLineSymbolItem, str] = {}
        self._snap_to_grid = True

        # Live ghost while dragging equipment from the library
        self._drop_preview: OneLineSymbolItem | None = None
        self._drop_preview_equip_id: str | None = None

        # Resize tracking (corner-handle drag)
        self._resize_item: OneLineSymbolItem | None = None
        self._resize_origin_scale = 1.0
        self._resize_start_dist = 1.0
        self._resize_was_movable = True

        # Rotate tracking (circular-arrow handle drag)
        self._rotate_item: OneLineSymbolItem | None = None
        self._rotate_origin_deg = 0.0
        self._rotate_start_angle = 0.0
        self._rotate_was_movable = True

        self._pdf_visible = True

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

    def set_pdf_visible(self, visible: bool):
        self._pdf_visible = visible
        if self._background_item is not None:
            self._background_item.setVisible(visible)

    def has_pdf_background(self) -> bool:
        return self._background_item is not None

    def set_grid_opacity(self, percent: int):
        if self._grid_item is not None:
            self._grid_item.setOpacity(max(0.0, min(percent / 100.0, 1.0)))

    def set_snap_to_grid(self, enabled: bool):
        self._snap_to_grid = enabled
        OneLineSymbolItem.snap_to_grid_enabled = enabled
        scn = self.scene()
        if scn is not None:
            for item in scn.items():
                if isinstance(item, ConnectionItem):
                    item.update_path()

    def _assign_snap_ports(self, items: list[OneLineSymbolItem], scene_pos: QPointF):
        self._clear_snap_ports()
        if not self._snap_to_grid:
            return
        for item in items:
            port = item.nearest_port(scene_pos, max_dist=PORT_HIT_RADIUS)
            if port is None:
                port = item.nearest_port(scene_pos, max_dist=float("inf"))
            if port is None:
                port = default_snap_port(item)
            item._snap_port_name = port
            self._snap_ports[item] = port

    def _clear_snap_ports(self):
        for item in self._snap_ports:
            item._snap_port_name = None
        self._snap_ports = {}

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

    def _wire_preview_path(self, start: QPointF, end: QPointF) -> QPainterPath:
        hit = self._find_port_at(end)
        if hit is not None:
            end = hit[0].port_scene_pos(hit[1])
        else:
            wire_hit = self._find_wire_at(end)
            if wire_hit is not None:
                end = wire_hit[1]
            elif self._snap_to_grid:
                end = snap_point_to_grid(end)
        points = route_wire_on_grid(start, end, snap_endpoints=self._snap_to_grid)
        return wire_path_from_points(points)

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

    def _find_wire_at(self, scene_pos: QPointF, exclude: ConnectionItem | None = None):
        """Return (ConnectionItem, point_on_wire) if cursor is near a wire segment."""
        scn = self.scene()
        if scn is None:
            return None
        best = None
        best_dist = WIRE_HIT_RADIUS
        for item in scn.items():
            if not isinstance(item, ConnectionItem) or item is exclude:
                continue
            pts = item.route_points()
            if len(pts) < 2:
                continue
            for i in range(len(pts) - 1):
                dist, closest = point_to_segment_distance(scene_pos, pts[i], pts[i + 1])
                if dist <= best_dist:
                    best_dist = dist
                    point = (
                        snap_point_to_grid(closest)
                        if self._snap_to_grid
                        else QPointF(closest)
                    )
                    best = (item, point)
        return best

    def _find_resize_handle_at(self, scene_pos: QPointF):
        scn = self.scene()
        if scn is None:
            return None
        for item in scn.selectedItems():
            if not isinstance(item, OneLineSymbolItem):
                continue
            handle = item.resize_handle_at(item.mapFromScene(scene_pos))
            if handle is not None:
                return item, handle
        return None

    def _find_rotate_handle_at(self, scene_pos: QPointF):
        scn = self.scene()
        if scn is None:
            return None
        for item in scn.selectedItems():
            if not isinstance(item, OneLineSymbolItem):
                continue
            if item.rotation_handle_at(item.mapFromScene(scene_pos)):
                return item
        return None

    @staticmethod
    def _angle_about_item(item: OneLineSymbolItem, scene_pos: QPointF) -> float:
        center = item.mapToScene(QPointF(0.0, 0.0))
        delta = scene_pos - center
        return math.degrees(math.atan2(delta.y(), delta.x()))

    def _begin_rotate(self, item: OneLineSymbolItem, scene_pos: QPointF):
        self._move_origins = {}
        self._clear_snap_ports()
        self._rotate_item = item
        self._rotate_origin_deg = item.rotation_deg
        self._rotate_start_angle = self._angle_about_item(item, scene_pos)
        self._rotate_was_movable = bool(
            item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self._saved_drag_mode = self.dragMode()
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self._set_status(f"Rotate {item.instance_id} ({item.rotation_deg:g}°)")

    def _update_rotate(self, scene_pos: QPointF):
        item = self._rotate_item
        if item is None:
            return
        angle_now = self._angle_about_item(item, scene_pos)
        delta = angle_now - self._rotate_start_angle
        raw = (self._rotate_origin_deg + delta) % 360.0
        if raw < 0:
            raw += 360.0
        # Hold Shift for free rotation; otherwise snap to 15°
        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier:
            snapped = raw
        else:
            snapped = round(raw / ROTATE_DRAG_SNAP) * ROTATE_DRAG_SNAP % 360.0
            if snapped < 0:
                snapped += 360.0
        item.set_rotation_deg(snapped)
        self._set_status(f"Rotate {item.instance_id} → {snapped:g}°")

    def _commit_rotate_if_any(self):
        item = self._rotate_item
        if item is None:
            return
        old_rot = self._rotate_origin_deg
        new_rot = item.rotation_deg
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, self._rotate_was_movable)
        self._rotate_item = None
        self.setDragMode(self._saved_drag_mode)
        self.unsetCursor()
        if abs(new_rot - old_rot) > 1e-6:
            self._push(RotateEquipmentCommand([(item, old_rot, new_rot)]))
            self._set_status(f"Rotated {item.instance_id} to {new_rot:g}°")
        else:
            self._set_status("Ready")

    def _cancel_rotate(self):
        item = self._rotate_item
        if item is None:
            return
        item.set_rotation_deg(self._rotate_origin_deg)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, self._rotate_was_movable)
        self._rotate_item = None
        self.setDragMode(self._saved_drag_mode)
        self.unsetCursor()
        self._set_status("Rotate cancelled")

    def _begin_resize(self, item: OneLineSymbolItem, scene_pos: QPointF):
        self._move_origins = {}
        self._clear_snap_ports()
        self._resize_item = item
        self._resize_origin_scale = item.scale_factor
        local = item.mapFromScene(scene_pos)
        self._resize_start_dist = max(1.0, (local.x() ** 2 + local.y() ** 2) ** 0.5)
        self._resize_was_movable = bool(
            item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self._saved_drag_mode = self.dragMode()
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self._set_status(f"Resize {item.instance_id} (scale {item.scale_factor:g})")

    def _update_resize(self, scene_pos: QPointF):
        item = self._resize_item
        if item is None:
            return
        local = item.mapFromScene(scene_pos)
        dist = (local.x() ** 2 + local.y() ** 2) ** 0.5
        raw = self._resize_origin_scale * (dist / self._resize_start_dist)
        snapped = round(raw / SYMBOL_SCALE_STEP) * SYMBOL_SCALE_STEP
        snapped = max(MIN_SYMBOL_SCALE, min(MAX_SYMBOL_SCALE, snapped))
        item.set_scale_factor(snapped)
        self._set_status(f"Resize {item.instance_id} → {snapped:g}×")

    def _commit_resize_if_any(self):
        item = self._resize_item
        if item is None:
            return
        old_scale = self._resize_origin_scale
        new_scale = item.scale_factor
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, self._resize_was_movable)
        self._resize_item = None
        self.setDragMode(self._saved_drag_mode)
        self.unsetCursor()
        if abs(new_scale - old_scale) > 1e-6:
            # Undo stack expects redo() to apply the new value; item is already there,
            # so push a command that no-ops on first redo by setting again.
            self._push(ResizeEquipmentCommand(item, old_scale, new_scale))
            self._set_status(f"Resized {item.instance_id} to {new_scale:g}×")
        else:
            self._set_status("Ready")

    # --- Move tracking for undo ---------------------------------------------

    def _capture_move_origins(self, scene_pos: QPointF):
        scn = self.scene()
        if scn is None:
            return
        origins: dict[OneLineSymbolItem, QPointF] = {}
        items: list[OneLineSymbolItem] = []
        under = [
            it for it in scn.items(scene_pos) if isinstance(it, OneLineSymbolItem)
        ]
        selected = [it for it in scn.selectedItems() if isinstance(it, OneLineSymbolItem)]
        # Prefer the symbol under the cursor so snap applies to the item being
        # dragged (not a stale multi-selection). Keep multi-select when the
        # clicked item is already part of the selection.
        if under and under[0] in selected and len(selected) > 1:
            move_items = selected
        elif under:
            move_items = [under[0]]
        else:
            move_items = selected
        for item in move_items:
            origins[item] = QPointF(item.pos())
            items.append(item)
        self._move_origins = origins
        self._assign_snap_ports(items, scene_pos)

    def _commit_moves_if_any(self):
        moves: list[tuple[OneLineSymbolItem, QPointF, QPointF]] = []
        for item, old_pos in self._move_origins.items():
            new_pos = QPointF(item.pos())
            if (new_pos - old_pos).manhattanLength() > 0.5:
                moves.append((item, old_pos, new_pos))
        self._move_origins = {}
        self._clear_snap_ports()
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

    def rotate_selected(self, delta_deg: float = SYMBOL_ROTATION_STEP):
        """Rotate selected symbols by delta_deg (positive = clockwise)."""
        scn = self.scene()
        if scn is None:
            return
        selected = [it for it in scn.selectedItems() if isinstance(it, OneLineSymbolItem)]
        if not selected:
            self._set_status("Select a component to rotate")
            return
        rotations: list[tuple[OneLineSymbolItem, float, float]] = []
        for item in selected:
            old = item.rotation_deg
            new = (old + delta_deg) % 360.0
            if new < 0:
                new += 360.0
            if abs(new - old) > 1e-6:
                rotations.append((item, old, new))
        if not rotations:
            return
        self._push(RotateEquipmentCommand(rotations))
        if len(rotations) == 1:
            self._set_status(
                f"Rotated {rotations[0][0].instance_id} to {rotations[0][2]:g}°"
            )
        else:
            self._set_status(f"Rotated {len(rotations)} items")

    def set_background_pixmap(self, pixmap: QPixmap):
        scn = self.scene()
        if self._background_item is not None and scn is not None:
            scn.removeItem(self._background_item)
            self._background_item = None

        self._background_item = QGraphicsPixmapItem(pixmap)
        self._background_item.setZValue(-10_000)
        self._background_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self._background_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self._background_item.setVisible(self._pdf_visible)
        scn = self.scene()
        if scn is not None:
            scn.addItem(self._background_item)
            scn.setSceneRect(self._background_item.boundingRect())
            self._sync_grid_to_scene()
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    # --- Keyboard & mouse input ---------------------------------------------

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self._rotate_item is not None:
            self._cancel_rotate()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self._resize_item is not None:
            item = self._resize_item
            old_scale = self._resize_origin_scale
            item.set_scale_factor(old_scale)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, self._resize_was_movable)
            self._resize_item = None
            self.setDragMode(self._saved_drag_mode)
            self.unsetCursor()
            self._set_status("Resize cancelled")
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self._pending is not None:
            self._cancel_pending()
            self._set_status("Connection cancelled")
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
            event.accept()
            return
        if event.key() == Qt.Key.Key_R:
            delta = (
                -SYMBOL_ROTATION_STEP
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                else SYMBOL_ROTATION_STEP
            )
            self.rotate_selected(delta)
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

            rotate_item = self._find_rotate_handle_at(scene_pos)
            if rotate_item is not None:
                self._begin_rotate(rotate_item, scene_pos)
                event.accept()
                return

            handle_hit = self._find_resize_handle_at(scene_pos)
            if handle_hit is not None:
                item, _handle = handle_hit
                self._begin_resize(item, scene_pos)
                event.accept()
                return

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
                self._temp_line = QGraphicsPathItem()
                self._temp_line.setPen(QPen(QColor(UI_ACCENT), 2, Qt.PenStyle.DashLine))
                self._temp_line.setZValue(200)
                start = item.port_scene_pos(port)
                self._temp_line.setPath(wire_path_from_points([start, start]))
                self._temp_line.setVisible(False)
                self.scene().addItem(self._temp_line)
                self._set_status(f"Drag to a port from {item.instance_id}:{port}")
                event.accept()
                return

            # Click on an existing wire / bend → insert blue junction and start a branch.
            wire_hit = self._find_wire_at(scene_pos)
            if wire_hit is not None:
                wire, point = wire_hit
                cmd = SplitWireCommand(self.scene(), wire, point)
                self._push(cmd)
                junction = cmd.junction
                if junction is None:
                    event.accept()
                    return
                self._move_origins = {}
                self._saved_drag_mode = self.dragMode()
                self.setDragMode(QGraphicsView.DragMode.NoDrag)
                self.setCursor(Qt.CursorShape.CrossCursor)
                self._pending = (junction, "node")
                self._wire_start_pos = scene_pos
                self._wiring = False
                self._temp_line = QGraphicsPathItem()
                self._temp_line.setPen(QPen(QColor(UI_ACCENT), 2, Qt.PenStyle.DashLine))
                self._temp_line.setZValue(200)
                start = junction.port_scene_pos("node")
                self._temp_line.setPath(wire_path_from_points([start, start]))
                self._temp_line.setVisible(False)
                self.scene().addItem(self._temp_line)
                self._set_status(f"Drag to a port from {junction.instance_id}")
                event.accept()
                return

            # Not on a port — track positions for a possible move undo.
            self._capture_move_origins(scene_pos)

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._rotate_item is not None:
            self._update_rotate(self.mapToScene(event.position().toPoint()))
            event.accept()
            return
        if self._resize_item is not None:
            self._update_resize(self.mapToScene(event.position().toPoint()))
            event.accept()
            return
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
                self._temp_line.setPath(self._wire_preview_path(start, end))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._rotate_item is not None:
            self._commit_rotate_if_any()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self._resize_item is not None:
            self._commit_resize_if_any()
            event.accept()
            return

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

            # Drop onto an existing wire → tee with an auto blue-port junction.
            if self._wiring:
                wire_hit = self._find_wire_at(scene_pos)
                if wire_hit is not None:
                    wire, point = wire_hit
                    self._push(
                        SplitWireCommand(
                            self.scene(),
                            wire,
                            point,
                            branch_from=from_item,
                            branch_port=from_port,
                        )
                    )
                    self._cancel_pending()
                    self._set_status(
                        f"Teed {from_item.instance_id}:{from_port} into wire junction"
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

    def _clear_drop_preview(self):
        preview = self._drop_preview
        self._drop_preview = None
        self._drop_preview_equip_id = None
        if preview is None:
            return
        scn = self.scene()
        if scn is not None and preview.scene() is scn:
            scn.removeItem(preview)

    def _snapped_place_pos(self, scene_pos: QPointF, symbol: OneLineSymbolItem) -> QPointF:
        if not self._snap_to_grid:
            return QPointF(scene_pos)
        port = default_snap_port(symbol)
        return position_for_snapped_port(scene_pos, symbol.ports()[port])

    def _ensure_drop_preview(self, equip_id: str) -> OneLineSymbolItem | None:
        meta = EQUIPMENT_DEFS.get(equip_id)
        if not meta:
            return None
        if (
            self._drop_preview is not None
            and self._drop_preview_equip_id == equip_id
            and self._drop_preview.scene() is self.scene()
        ):
            return self._drop_preview

        self._clear_drop_preview()
        # Avoid burning a permanent instance id on the transient ghost.
        prior_count = _instance_counters[equip_id]
        symbol = meta["factory"]()
        _instance_counters[equip_id] = prior_count

        symbol.setOpacity(0.55)
        symbol.setZValue(300)
        symbol.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        symbol.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        if self._snap_to_grid:
            symbol._snap_port_name = default_snap_port(symbol)
        scn = self.scene()
        if scn is not None:
            scn.addItem(symbol)
        self._drop_preview = symbol
        self._drop_preview_equip_id = equip_id
        return symbol

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(MIME_EQUIPMENT):
            equip_id = bytes(event.mimeData().data(MIME_EQUIPMENT)).decode("utf-8")
            preview = self._ensure_drop_preview(equip_id)
            if preview is not None:
                pos = self.mapToScene(event.position().toPoint())
                preview.setPos(self._snapped_place_pos(pos, preview))
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(MIME_EQUIPMENT):
            equip_id = bytes(event.mimeData().data(MIME_EQUIPMENT)).decode("utf-8")
            preview = self._ensure_drop_preview(equip_id)
            if preview is not None:
                pos = self.mapToScene(event.position().toPoint())
                preview.setPos(self._snapped_place_pos(pos, preview))
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self._clear_drop_preview()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(MIME_EQUIPMENT):
            super().dropEvent(event)
            return

        equip_id = bytes(event.mimeData().data(MIME_EQUIPMENT)).decode("utf-8")
        meta = EQUIPMENT_DEFS.get(equip_id)
        if not meta:
            self._clear_drop_preview()
            event.ignore()
            return

        # Prefer the live ghost position so drop matches what the user saw.
        if (
            self._drop_preview is not None
            and self._drop_preview_equip_id == equip_id
        ):
            pos = QPointF(self._drop_preview.pos())
        else:
            prior_count = _instance_counters[equip_id]
            temp = meta["factory"]()
            _instance_counters[equip_id] = prior_count
            pos = self._snapped_place_pos(
                self.mapToScene(event.position().toPoint()),
                temp,
            )

        self._clear_drop_preview()
        symbol = meta["factory"]()
        # Keep the exact preview/grid position (avoid a second snap pass).
        was_snap = OneLineSymbolItem.snap_to_grid_enabled
        OneLineSymbolItem.snap_to_grid_enabled = False
        symbol.setPos(pos)
        OneLineSymbolItem.snap_to_grid_enabled = was_snap
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

    QCheckBox {
        color: #1f2933;
        spacing: 8px;
        font-size: 11pt;
        font-weight: normal;
    }

    QCheckBox::indicator {
        width: 18px;
        height: 18px;
        border: 1px solid #cbd2d9;
        border-radius: 4px;
        background: white;
    }

    QCheckBox::indicator:checked {
        background: #006A4E;
        border-color: #006A4E;
    }

    QSlider::groove:horizontal {
        height: 6px;
        background: #d9e2ec;
        border-radius: 3px;
    }

    QSlider::handle:horizontal {
        width: 14px;
        margin: -5px 0;
        border-radius: 7px;
        background: #006A4E;
    }

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
        background-color: #006A4E; color: white; border: none;
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

        self.pdf_show_cb = QCheckBox("Show PDF")
        self.pdf_show_cb.setChecked(True)
        self.pdf_show_cb.setEnabled(False)
        self.pdf_show_cb.setToolTip("Import a PDF first to enable this option")
        self.pdf_show_cb.toggled.connect(self.workspace_view.set_pdf_visible)

        self.grid_snap_cb = QCheckBox("Snap to Grid")
        self.grid_snap_cb.setChecked(True)
        self.grid_snap_cb.toggled.connect(self.workspace_view.set_snap_to_grid)

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
        self.controls_layout.addWidget(self.pdf_show_cb)
        self.controls_layout.addWidget(self.grid_snap_cb)
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
        layout.setSpacing(12)

        logo = QLabel()
        logo_pix = load_gft_logo_pixmap()
        if not logo_pix.isNull():
            logo.setPixmap(logo_pix)
            logo.setFixedSize(logo_pix.size())
        logo.setToolTip("GFT Inc")
        logo.setStyleSheet("background: transparent; border: none;")

        title = QLabel("AI-Assisted Substation Protection & Control Design")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))

        project_box = QComboBox()
        project_box.addItems(["Demo Project - One Line A", "Breaker-and-a-Half Yard", "Ring Bus Example"])
        project_box.setMinimumWidth(320)
        self.project_box = project_box

        save_btn = QPushButton("Save Layout")
        self.load_btn = QPushButton("Import PDF")
        self.load_btn.clicked.connect(self.on_import_pdf_clicked)
        self.run_btn = QPushButton("Run Evaluation")
        self.run_btn.clicked.connect(self._on_run_evaluation_clicked)

        layout.addWidget(logo)
        layout.addWidget(title)
        layout.addStretch()
        layout.addWidget(QLabel("Project:"))
        layout.addWidget(project_box)
        layout.addWidget(self.load_btn)
        layout.addWidget(save_btn)
        layout.addWidget(self.run_btn)
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

        rotate_btn = QPushButton("Rotate 90°")
        rotate_btn.clicked.connect(
            lambda: self.workspace_view.rotate_selected() if hasattr(self, "workspace_view") else None
        )
        self.controls_layout.addWidget(rotate_btn)

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
        properties_layout.addWidget(self.prop_close_coil, 5, 1)

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

        layout.addWidget(properties_group, 1)
        layout.addWidget(analysis_group, 4)

        if hasattr(self, "workspace_scene"):
            self.workspace_scene.selectionChanged.connect(self._on_selection_changed)

        return container

    # --- Event handlers -----------------------------------------------------

    def _on_run_evaluation_clicked(self):
        """
        Run momentary evaluation on the fake test netlist (test_netlist.py)
        """
        system_name = "Test Bay"
        if hasattr(self, "project_box"):
            system_name = self.project_box.currentText() or system_name

        started = time.perf_counter()
        try:
            netlist, result = run_fake_evaluation(system_name=system_name)
        except Exception as e:
            if hasattr(self, "output_box"):
                self.output_box.setPlainText(f"Evaluation failed:\n{e}")
            if hasattr(self, "footer_status_label"):
                self.footer_status_label.setText(f"Evaluation failed: {e}")
            return

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if hasattr(self, "response_time_label"):
            self.response_time_label.setText(f"Response time: {elapsed_ms:.0f} ms")

        lines: list[str] = []
        lines.append("(Using fake test netlist from test_netlist.py)")
        lines.append(f"System: {result.get('scenario_name', system_name)}")
        lines.append(f"Voltage: {result.get('voltage_V', 125):g} V")
        lines.append(f"Components: {len(netlist.get('components', []))}")
        lines.append(f"Connections: {len(netlist.get('connections', []))}")
        lines.append("")
        lines.append(f"Peak current: {result.get('peak_current_A', 0):g} A")
        lines.append("")
        loads = result.get("loads") or []
        if loads:
            lines.append("Loads:")
            for ld in loads:
                note = f"  ({ld['note']})" if ld.get("note") else ""
                lines.append(
                    f"  • {ld.get('name', '?')}: {ld.get('total_amps', 0):g} A{note}"
                )
        else:
            lines.append("Loads: (none)")

        warnings = result.get("warnings") or []
        if warnings:
            lines.append("")
            lines.append("Warnings:")
            for w in warnings:
                lines.append(f"  ⚠ {w}")

        if hasattr(self, "output_box"):
            self.output_box.setPlainText("\n".join(lines))
        if hasattr(self, "footer_status_label"):
            self.footer_status_label.setText(
                f"Evaluation complete — peak {result.get('peak_current_A', 0):g} A"
            )

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
        if hasattr(self, "workspace_view"):
            self.workspace_view.set_background_pixmap(pm)
        if hasattr(self, "pdf_show_cb"):
            self.pdf_show_cb.setEnabled(True)
            self.pdf_show_cb.setChecked(True)
            self.pdf_show_cb.setToolTip("Show or hide the imported PDF background")
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