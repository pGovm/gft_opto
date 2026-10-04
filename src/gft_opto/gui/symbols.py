"""One-line equipment glyphs, junction dots, and the wires between them."""

import math
from collections import defaultdict

from PySide6.QtCore import Qt, QPointF, QRectF, QTimer
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QTransform,
)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPathItem, QGraphicsScene

from gft_opto.gui.routing import (
    MAX_SYMBOL_SCALE,
    MIN_BUS_LENGTH,
    MIN_SYMBOL_SCALE,
    PORT_DOT_RADIUS,
    PORT_HIT_RADIUS,
    RESIZE_HANDLE_HIT,
    RESIZE_HANDLE_SIZE,
    ROTATE_HANDLE_HIT,
    ROTATE_HANDLE_OFFSET,
    ROTATE_HANDLE_RADIUS,
    UI_ACCENT,
    _segment_hits_rect,
    default_snap_port,
    port_into_direction,
    position_for_snapped_port,
    route_wire_on_grid,
    simplify_route_points,
    wire_path_from_points,
)

_instance_counters: dict[str, int] = defaultdict(int)

def next_instance_id(equip_type: str) -> str:
    """Assign a unique id per placed symbol (e.g. mos_1, mos_2)."""
    _instance_counters[equip_type] += 1
    return f"{equip_type}_{_instance_counters[equip_type]}"


def _same_label_rect(a: QRectF | None, b: QRectF | None) -> bool:
    if a is None or b is None:
        return False
    return (
        abs(a.x() - b.x()) < 0.5
        and abs(a.y() - b.y()) < 0.5
        and abs(a.width() - b.width()) < 0.5
        and abs(a.height() - b.height()) < 0.5
    )



# ---------------------------------------------------------------------------
# Symbol graphics items — base class
# ---------------------------------------------------------------------------

class OneLineSymbolItem(QGraphicsItem):
    """Base class for draggable one-line equipment symbols with named ports."""

    snap_to_grid_enabled = True
    max_scale = MAX_SYMBOL_SCALE  # None means resize is not capped

    def __init__(self, equip_type: str, label: str, ports: dict[str, QPointF]):
        super().__init__()
        self.equip_type = equip_type
        self.equip_id = equip_type  # legacy alias used by the properties panel
        self.instance_id = next_instance_id(equip_type)
        self.label = label
        self.display_name = label
        self.show_name_label = True
        self._base_ports = dict(ports)
        self._connections: list["ConnectionItem"] = []
        self._snap_port_name: str | None = None
        self.scale_factor = 1.0
        self.rotation_deg = 0.0
        self._rect = QRectF(-40, -20, 80, 40)
        self._label_rect_cache: QRectF | None = None
        self._pending_label_rect: QRectF | None = None
        self._defer_label_geometry = False
        self._label_flush_scheduled = False
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
        scale = max(MIN_SYMBOL_SCALE, float(scale))
        if self.max_scale is not None:
            scale = min(self.max_scale, scale)
        if abs(scale - self.scale_factor) < 1e-6:
            return
        self.prepareGeometryChange()
        self.scale_factor = scale
        self._label_rect_cache = None
        self.update()
        for conn in self._connections:
            conn.update_path()
        self.refresh_label_placement()

    def set_rotation_deg(self, degrees: float):
        degrees = float(degrees) % 360.0
        if degrees < 0:
            degrees += 360.0
        if abs(degrees - self.rotation_deg) < 1e-6:
            return
        self.prepareGeometryChange()
        self.rotation_deg = degrees
        self._label_rect_cache = None
        self.update()
        for conn in self._connections:
            conn.update_path()
        self.refresh_label_placement()

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
        if self.show_name_label and self._caption():
            rect = rect.united(self._local_transform().mapRect(self._label_base_rect()))
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

    def _caption(self) -> str:
        """Name drawn above the symbol, same style as a bus."""
        return (self.display_name or "").strip()

    def _label_font(self) -> QFont:
        """Keep the name the same visual size when the symbol is scaled."""
        font = QFont("Sans-Serif", 11)
        font.setWeight(QFont.Weight.Bold)
        font.setPointSizeF(11.0 / max(self.scale_factor, 1e-6))
        return font

    def _label_base_rect(self) -> QRectF:
        """Name placed beside the symbol, shifted when a wire would cross it."""
        if self._label_rect_cache is not None:
            return self._label_rect_cache
        return self._choose_label_rect()

    def _candidate_label_rects(self) -> list[QRectF]:
        """Preferred name positions: above the symbol first, then nearby clear slots."""
        metrics = QFontMetricsF(self._label_font())
        width = metrics.horizontalAdvance(self._caption()) + 4
        height = metrics.height()
        gap = 8.0 / max(self.scale_factor, 1e-6)
        body = self._rect
        cx = body.center().x()
        cy = body.center().y()
        step = width / 2 + 8.0 / max(self.scale_factor, 1e-6)
        rects: list[QRectF] = []
        for slot in range(7):
            dx = 0.0 if slot == 0 else step * ((slot + 1) // 2) * (-1 if slot % 2 else 1)
            rects.append(QRectF(cx - width / 2 + dx, body.top() - gap - height, width, height))
        for slot in range(5):
            dx = 0.0 if slot == 0 else step * ((slot + 1) // 2) * (-1 if slot % 2 else 1)
            rects.append(QRectF(cx - width / 2 + dx, body.bottom() + gap, width, height))
        for ring in range(1, 4):
            extra = ring * (height + gap)
            rects.append(QRectF(cx - width / 2, body.top() - gap - height - extra, width, height))
            rects.append(QRectF(cx - width / 2, body.bottom() + gap + extra, width, height))
        for shift in (0, -1, 1, -2, 2):
            dy = shift * (height + 4)
            rects.append(QRectF(body.right() + gap, cy - height / 2 + dy, width, height))
            rects.append(QRectF(body.left() - gap - width, cy - height / 2 + dy, width, height))
        return rects

    def _label_scene_rect(self, base_rect: QRectF) -> QRectF:
        return self.mapRectToScene(self._local_transform().mapRect(base_rect))

    def _label_hits_wire(self, base_rect: QRectF, wires: list) -> bool:
        if base_rect.width() <= 0 or base_rect.height() <= 0:
            return False
        scene_rect = self._label_scene_rect(base_rect).adjusted(-4, -4, 4, 4)
        for wire in wires:
            points = wire.route_points()
            for index in range(len(points) - 1):
                if _segment_hits_rect(points[index], points[index + 1], scene_rect):
                    return True
        return False

    def _choose_label_rect(self, wires: list | None = None) -> QRectF:
        caption = self._caption()
        if not caption:
            return QRectF()
        if wires is None:
            scene = self.scene()
            wires = [
                it for it in scene.items() if isinstance(it, ConnectionItem)
            ] if scene is not None else []
        candidates = self._candidate_label_rects()
        if not candidates:
            return QRectF()
        for rect in candidates:
            if not self._label_hits_wire(rect, wires):
                return rect
        return candidates[0]

    def refresh_label_placement(self, wires: list | None = None):
        """Pick a name position that does not cross a wire."""
        if not self.show_name_label:
            return
        chosen = self._choose_label_rect(wires)
        if _same_label_rect(self._label_rect_cache, chosen):
            return
        if self._defer_label_geometry:
            self._pending_label_rect = chosen
            if not self._label_flush_scheduled:
                self._label_flush_scheduled = True
                QTimer.singleShot(0, self._flush_pending_label)
            return
        if self.scene() is not None:
            self.prepareGeometryChange()
        self._label_rect_cache = chosen
        self.update()

    def _flush_pending_label(self):
        self._label_flush_scheduled = False
        pending = self._pending_label_rect
        self._pending_label_rect = None
        if pending is None or _same_label_rect(self._label_rect_cache, pending):
            return
        if self.scene() is None:
            self._label_rect_cache = pending
            return
        self.prepareGeometryChange()
        self._label_rect_cache = pending
        self.update()

    def _draw_name_label(self, painter: QPainter):
        if not self.show_name_label:
            return
        caption = self._caption()
        if not caption:
            return
        text_pen = QPen(Qt.GlobalColor.black)
        text_pen.setWidthF(0)
        painter.setPen(text_pen)
        painter.setFont(self._label_font())
        painter.drawText(self._label_base_rect(), Qt.AlignmentFlag.AlignCenter, caption)

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
        self._draw_name_label(painter)
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
            self._defer_label_geometry = True
            try:
                for conn in self._connections:
                    conn.update_path()
            finally:
                self._defer_label_geometry = False
            self._flush_pending_label()
            if not self._connections:
                self.refresh_label_placement()
        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.prepareGeometryChange()
            self.update()
        return super().itemChange(change, value)


# ---------------------------------------------------------------------------
# AEP one-line equipment glyphs
# ---------------------------------------------------------------------------

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


class BusItem(OneLineSymbolItem):
    """Thick horizontal bus bar. Extra nodes are ports added along the bar."""

    HALF_LENGTH = 80.0
    max_scale = None

    def __init__(self):
        half = self.HALF_LENGTH
        super().__init__(
            "bus",
            "Bus",
            {
                "left": QPointF(-half, 0),
                "right": QPointF(half, 0),
            },
        )
        self._tap_seq = 0
        self.display_name = self.label
        self.properties = {}
        self._sync_bus_rect()

    def _sync_bus_rect(self):
        left = self._base_ports["left"].x()
        right = self._base_ports["right"].x()
        self._rect = QRectF(left, -12, right - left, 24)

    def paint(self, painter: QPainter, option, widget=None):
        self._begin_paint(painter)
        pen = painter.pen()
        pen.setWidthF(4.0 / max(self.scale_factor, 1e-6))
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        painter.drawLine(
            self._base_ports["left"],
            self._base_ports["right"],
        )
        self._end_paint(painter)

    def _caption(self) -> str:
        """Custom name plus voltage rating, drawn above the bar."""
        name = self.display_name.strip()
        props = getattr(self, "properties", None)
        rating = props.get("rating_kv") if isinstance(props, dict) else None
        if rating is None:
            return name
        voltage = f"{rating:g} kV"
        return f"{name}  {voltage}" if name else voltage

    def _bar_span(self) -> tuple[QPointF, QPointF, float]:
        """Scene start, end, and squared length of the bus axis."""
        start = self.port_scene_pos("left")
        end = self.port_scene_pos("right")
        span = end - start
        return start, span, span.x() ** 2 + span.y() ** 2

    def project_onto_axis(self, scene_pos: QPointF) -> QPointF:
        """Project onto the infinite line through the bus, past either end."""
        start, span, length_sq = self._bar_span()
        if length_sq < 1e-6:
            return QPointF(start)
        t = (
            (scene_pos.x() - start.x()) * span.x()
            + (scene_pos.y() - start.y()) * span.y()
        ) / length_sq
        return QPointF(start.x() + t * span.x(), start.y() + t * span.y())

    def project_onto_bar(self, scene_pos: QPointF) -> QPointF:
        """Closest point on the bus segment to a scene position."""
        start, span, length_sq = self._bar_span()
        if length_sq < 1e-6:
            return QPointF(start)
        t = (
            (scene_pos.x() - start.x()) * span.x()
            + (scene_pos.y() - start.y()) * span.y()
        ) / length_sq
        t = max(0.0, min(1.0, t))
        return QPointF(start.x() + t * span.x(), start.y() + t * span.y())

    def point_on_bar(self, scene_pos: QPointF) -> QPointF:
        return self.project_onto_bar(scene_pos)

    def distance_to_bar(self, scene_pos: QPointF) -> float:
        delta = self.project_onto_bar(scene_pos) - scene_pos
        return (delta.x() ** 2 + delta.y() ** 2) ** 0.5

    def _unclamped_base_x(self, scene_pos: QPointF) -> float:
        local = self.mapFromScene(scene_pos)
        inverse, ok = self._local_transform().inverted()
        if not ok:
            return 0.0
        return inverse.map(local).x()

    def _clamp_to_bar(self, base_x: float) -> float:
        left = self._base_ports["left"].x()
        right = self._base_ports["right"].x()
        return max(left, min(right, float(base_x)))

    def base_x_for_scene(self, scene_pos: QPointF) -> float:
        """Bar coordinate (before scale/rotation) for a scene point on the bus."""
        return self._clamp_to_bar(self._unclamped_base_x(scene_pos))

    def set_end_base_x(self, port: str, base_x: float):
        """Move one end along the bar. The other end and existing nodes stay."""
        if port not in ("left", "right"):
            return
        base_x = float(base_x)
        left = self._base_ports["left"].x()
        right = self._base_ports["right"].x()
        taps = [
            pt.x()
            for name, pt in self._base_ports.items()
            if name not in ("left", "right")
        ]
        if port == "left":
            limit = right - MIN_BUS_LENGTH
            if taps:
                limit = min(limit, min(taps))
            base_x = min(base_x, limit)
        else:
            limit = left + MIN_BUS_LENGTH
            if taps:
                limit = max(limit, max(taps))
            base_x = max(base_x, limit)
        if abs(base_x - self._base_ports[port].x()) < 1e-6:
            return
        self.prepareGeometryChange()
        self._base_ports[port] = QPointF(base_x, 0.0)
        self._sync_bus_rect()
        self.update()
        for conn in self._connections:
            conn.update_path()

    def add_tap(self, base_x: float, name: str | None = None) -> tuple[str, bool]:
        """
        Add a node on the bar. Reuse a port already within 2 units.
        Returns (port_name, created_new).
        """
        base_x = self._clamp_to_bar(base_x)
        for existing, pt in self._base_ports.items():
            if abs(pt.x() - base_x) <= 2.0:
                return existing, False
        if name is None or name in self._base_ports:
            self._tap_seq += 1
            name = f"node_{self._tap_seq}"
            while name in self._base_ports:
                self._tap_seq += 1
                name = f"node_{self._tap_seq}"
        self.prepareGeometryChange()
        self._base_ports[name] = QPointF(base_x, 0.0)
        self.update()
        for conn in self._connections:
            conn.update_path()
        return name, True

    def remove_tap(self, name: str) -> bool:
        """Remove a dropped node. Built-in left/right ports stay."""
        if name in ("left", "right") or name not in self._base_ports:
            return False
        self.prepareGeometryChange()
        del self._base_ports[name]
        self.update()
        return True

    def restore_tap(self, name: str, base_x: float):
        """Put a removed node back at the same place (used by undo)."""
        if name in ("left", "right") or name in self._base_ports:
            return
        self.prepareGeometryChange()
        self._base_ports[name] = QPointF(float(base_x), 0.0)
        self.update()

    def resize_handle_at(self, local_pos: QPointF, hit_radius: float = RESIZE_HANDLE_HIT):
        return None

    def _draw_resize_handles(self, painter: QPainter):
        return


# ---------------------------------------------------------------------------
# Auto tee node + fallback custom box
# ---------------------------------------------------------------------------

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
        self.show_name_label = False

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
        self.display_name = name
        self._rect = QRectF(-42, -22, 84, 44)

    def paint(self, painter: QPainter, option, widget=None):
        self._begin_paint(painter)
        painter.setBrush(QBrush(Qt.GlobalColor.white))
        painter.drawRect(self._rect)
        self._end_paint(painter)


# ---------------------------------------------------------------------------
# Wires (orthogonal connections between ports)
# ---------------------------------------------------------------------------

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
                block_leave=port_into_direction(self.from_item, self.from_port),
                block_arrive=port_into_direction(self.to_item, self.to_port),
            )
        )
        self._route_points = points
        self.setPath(wire_path_from_points(points))
        self.update()
        scene = self.scene()
        if scene is None and self.from_item is not None:
            scene = self.from_item.scene()
        refresh_symbol_labels(scene, extra_wires=[self])

    def paint(self, painter: QPainter, option, widget=None):
        super().paint(painter, option, widget)
        # Bend corners are path geometry only — real tee junctions use JunctionItem.

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



_LABEL_REFRESH_DEPTH = 0


def refresh_symbol_labels(
    scene: QGraphicsScene | None,
    extra_wires: list | None = None,
):
    """Move component names that a wire now crosses."""
    global _LABEL_REFRESH_DEPTH
    if scene is None or _LABEL_REFRESH_DEPTH:
        return
    _LABEL_REFRESH_DEPTH += 1
    try:
        symbols = [it for it in scene.items() if isinstance(it, OneLineSymbolItem)]
        wires = [it for it in scene.items() if isinstance(it, ConnectionItem)]
        if extra_wires:
            seen = {id(wire) for wire in wires}
            for wire in extra_wires:
                if wire is not None and id(wire) not in seen:
                    wires.append(wire)
                    seen.add(id(wire))
        for symbol in symbols:
            symbol.refresh_label_placement(wires)
    finally:
        _LABEL_REFRESH_DEPTH -= 1



# ---------------------------------------------------------------------------
# Equipment library registry
# ---------------------------------------------------------------------------

# Glyphs used by Create Component. Not listed in the Equipment Library until
# the user adds them.
BUILTIN_SYMBOLS = {
    "xfmr_2w": {"label": "Transformer (2-winding)", "factory": Transformer2WItem},
    "xfmr_3w": {"label": "Transformer (3-winding)", "factory": Transformer3WItem},
    "breaker": {"label": "Circuit Breaker", "factory": CircuitBreakerItem},
    "disconnect": {"label": "Disconnect Switch", "factory": DisconnectSwitchItem},
    "mos": {"label": "Motor Operated Switch (MOS)", "factory": MotorOperatedSwitchItem},
    "ct": {"label": "Current Transformer (CT/BCT)", "factory": CurrentTransformerItem},
    "pt": {"label": "Potential Transformer (PT/VT)", "factory": PotentialTransformerItem},
    "ground": {"label": "Ground", "factory": GroundItem},
    "bus": {"label": "Bus", "factory": BusItem},
}

# User-created entries only. This is what the left-panel library shows.
EQUIPMENT_DEFS: dict[str, dict] = {}


def make_user_equipment(
    symbol_key: str,
    name: str,
    properties: dict | None = None,
) -> OneLineSymbolItem:
    """
    Build a library/canvas item for a user-created component.
    Uses the real AEP glyph when symbol_key matches a built-in type.
    """
    props = dict(properties or {})
    if symbol_key == "custom" or symbol_key not in BUILTIN_SYMBOLS:
        return CustomComponentItem(name=name, properties=props)

    item = BUILTIN_SYMBOLS[symbol_key]["factory"]()
    item.display_name = name
    item.properties = props
    return item


