"""Shared canvas constants and orthogonal wire routing."""

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainterPath

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Drag-and-drop mime type for the equipment library
MIME_EQUIPMENT = "application/x-substation-equipment"

# Port / wire hit testing
PORT_HIT_RADIUS = 14.0       # px — cursor must be this close to grab a port
PORT_DOT_RADIUS = 4.0        # px — drawn port handle size
WIRE_DRAG_THRESHOLD = 6.0    # px — move this far before a wire drag starts
WIRE_HIT_RADIUS = 10.0       # px — how close to a wire to tee into it
BUS_DROP_RADIUS = 16.0       # px — how close to a bus bar to drop a new node
MIN_BUS_LENGTH = 40.0        # base units — shortest a bus can be dragged

# Default AEP/ANSI E landscape drawing sheet (48 in wide × 36 in high)
AEP_SHEET_WIDTH_IN = 48.0
AEP_SHEET_HEIGHT_IN = 36.0
SCENE_UNITS_PER_INCH = 50.0
DEFAULT_WORKSPACE_WIDTH = AEP_SHEET_WIDTH_IN * SCENE_UNITS_PER_INCH
DEFAULT_WORKSPACE_HEIGHT = AEP_SHEET_HEIGHT_IN * SCENE_UNITS_PER_INCH

# Alignment grid: half-inch divisions on the AEP sheet
GRID_SPACING = SCENE_UNITS_PER_INCH / 2.0
GRID_MAJOR_EVERY = 5         # every Nth line is drawn heavier
DEFAULT_GRID_OPACITY = 0.25

# Symbol resize / rotate handles
RESIZE_HANDLE_SIZE = 8.0
RESIZE_HANDLE_HIT = 12.0
MIN_SYMBOL_SCALE = 0.5
MAX_SYMBOL_SCALE = 3.0
SYMBOL_SCALE_STEP = 0.25     # snap resize to this increment
SYMBOL_ROTATION_STEP = 90.0  # degrees — R / Shift+R and Quick Actions
ROTATE_HANDLE_OFFSET = 28.0  # px above symbol AABB top edge
ROTATE_HANDLE_RADIUS = 8.0
ROTATE_HANDLE_HIT = 14.0
ROTATE_DRAG_SNAP = 15.0      # degrees — snap while dragging the rotate handle

# Branding / theme
UI_ACCENT = "#006A4E"        # bottle green — primary GUI accent
GFT_LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "gft_logo.png"
GFT_LOGO_HEIGHT = 34         # px — header wordmark height


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


def _axis_direction(a: QPointF, b: QPointF) -> tuple[int, int]:
    """Unit step from a to b along the dominant axis. (0, 0) if they coincide."""
    dx = b.x() - a.x()
    dy = b.y() - a.y()
    if abs(dx) < 0.01 and abs(dy) < 0.01:
        return (0, 0)
    if abs(dx) >= abs(dy):
        return (1 if dx >= 0 else -1, 0)
    return (0, 1 if dy >= 0 else -1)


def port_into_direction(item: "OneLineSymbolItem", port_name: str) -> tuple[int, int] | None:
    """
    Scene-space direction from this port into the symbol body.
    None when the port sits on the symbol center (a junction dot).
    """
    if port_name not in getattr(item, "_base_ports", {}):
        return None
    port = item.port_scene_pos(port_name)
    center = item.mapToScene(QPointF(0, 0))
    dx = center.x() - port.x()
    dy = center.y() - port.y()
    if abs(dx) < 1.0 and abs(dy) < 1.0:
        return None
    if abs(dx) >= abs(dy):
        return (1 if dx > 0 else -1, 0)
    return (0, 1 if dy > 0 else -1)


def _segment_clears_ports(
    points: list[QPointF],
    block_leave: tuple[int, int] | None,
    block_arrive: tuple[int, int] | None,
) -> bool:
    """True when the route leaves and enters without running into either symbol."""
    if len(points) < 2:
        return False
    first = _axis_direction(points[0], points[1])
    last = _axis_direction(points[-2], points[-1])
    if first == (0, 0) or last == (0, 0):
        return False
    if block_leave is not None and first == block_leave:
        return False
    if block_arrive is not None and last == (-block_arrive[0], -block_arrive[1]):
        return False
    return True


def _prefer_outward(
    points: list[QPointF],
    block_leave: tuple[int, int] | None,
) -> int:
    """Sort key: routes that leave along the port's outward side come first."""
    if block_leave is None:
        return 1
    outward = (-block_leave[0], -block_leave[1])
    return 0 if _axis_direction(points[0], points[1]) == outward else 1


def route_wire_on_grid(
    p1: QPointF,
    p2: QPointF,
    *,
    spacing: float = GRID_SPACING,
    snap_endpoints: bool = True,
    block_leave: tuple[int, int] | None = None,
    block_arrive: tuple[int, int] | None = None,
) -> list[QPointF]:
    """
    Orthogonal polyline between two ports.

    Uses a straight run or a single corner. A two-corner step is used only
    when that corner would leave a port into its own symbol.
    """
    if snap_endpoints:
        p1 = snap_point_to_grid(p1, spacing)
        p2 = snap_point_to_grid(p2, spacing)

    same_row = abs(p1.y() - p2.y()) < 0.01
    same_col = abs(p1.x() - p2.x()) < 0.01
    if same_row or same_col:
        return [p1, p2]

    elbows = (
        [p1, QPointF(p2.x(), p1.y()), p2],
        [p1, QPointF(p1.x(), p2.y()), p2],
    )
    clear = [
        route for route in elbows
        if _segment_clears_ports(route, block_leave, block_arrive)
    ]
    if clear:
        clear.sort(key=lambda route: _prefer_outward(route, block_leave))
        return clear[0]

    bend_x = snap_point_to_grid(QPointF((p1.x() + p2.x()) / 2, p1.y()), spacing).x()
    bend_y = snap_point_to_grid(QPointF(p1.x(), (p1.y() + p2.y()) / 2), spacing).y()
    steps = (
        [p1, QPointF(bend_x, p1.y()), QPointF(bend_x, p2.y()), p2],
        [p1, QPointF(p1.x(), bend_y), QPointF(p2.x(), bend_y), p2],
    )
    clear_steps = [
        route for route in steps
        if _segment_clears_ports(route, block_leave, block_arrive)
    ]
    choices = clear_steps or list(steps)
    choices.sort(key=lambda route: _prefer_outward(route, block_leave))
    return choices[0]


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
    """Drop duplicate vertices and points that do not change direction."""
    if not points:
        return []
    simplified: list[QPointF] = [QPointF(points[0])]
    for pt in points[1:]:
        if (pt - simplified[-1]).manhattanLength() > 0.01:
            simplified.append(QPointF(pt))
    changed = True
    while changed and len(simplified) >= 3:
        changed = False
        kept: list[QPointF] = [simplified[0]]
        for index in range(1, len(simplified) - 1):
            prev_pt = kept[-1]
            mid = simplified[index]
            nxt = simplified[index + 1]
            incoming = _axis_direction(prev_pt, mid)
            outgoing = _axis_direction(mid, nxt)
            if incoming != (0, 0) and incoming == outgoing:
                changed = True
                continue
            kept.append(mid)
        kept.append(simplified[-1])
        simplified = kept
    return simplified


def _segment_hits_rect(a: QPointF, b: QPointF, rect: QRectF) -> bool:
    """True when segment ab crosses or touches an axis-aligned rect."""
    if rect.width() <= 0 or rect.height() <= 0:
        return False
    dx = b.x() - a.x()
    dy = b.y() - a.y()
    t0, t1 = 0.0, 1.0
    for p, q in (
        (-dx, a.x() - rect.left()),
        (dx, rect.right() - a.x()),
        (-dy, a.y() - rect.top()),
        (dy, rect.bottom() - a.y()),
    ):
        if abs(p) < 1e-9:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            if t > t1:
                return False
            if t > t0:
                t0 = t
        else:
            if t < t0:
                return False
            if t < t1:
                t1 = t
    return True


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


