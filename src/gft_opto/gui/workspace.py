"""Workspace grid and the canvas view that edits the one-line."""

import math
from typing import Callable

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import (
    QBrush, QColor, QPainter, QPainterPath, QPen, QPixmap,
    QTransform, QUndoCommand, QUndoStack,
)
from PySide6.QtWidgets import (
    QApplication, QGraphicsItem, QGraphicsPathItem, QGraphicsPixmapItem,
    QGraphicsScene, QGraphicsView,
)

from gft_opto.gui.commands import (
    AddConnectionCommand,
    AddEquipmentCommand,
    ConnectToBusCommand,
    DeleteSelectionCommand,
    MoveEquipmentCommand,
    ResizeBusCommand,
    ResizeEquipmentCommand,
    RotateEquipmentCommand,
    SplitWireCommand,
)
from gft_opto.gui.routing import (
    BUS_DROP_RADIUS,
    DEFAULT_GRID_OPACITY,
    DEFAULT_WORKSPACE_HEIGHT,
    DEFAULT_WORKSPACE_WIDTH,
    GRID_MAJOR_EVERY,
    GRID_SPACING,
    MIME_EQUIPMENT,
    MIN_SYMBOL_SCALE,
    PORT_HIT_RADIUS,
    RESIZE_HANDLE_HIT,
    ROTATE_DRAG_SNAP,
    ROTATE_HANDLE_HIT,
    SYMBOL_ROTATION_STEP,
    SYMBOL_SCALE_STEP,
    UI_ACCENT,
    WIRE_DRAG_THRESHOLD,
    WIRE_HIT_RADIUS,
    default_snap_port,
    point_to_segment_distance,
    port_into_direction,
    position_for_snapped_port,
    route_wire_on_grid,
    snap_point_to_grid,
    wire_path_from_points,
)
from gft_opto.gui.symbols import (
    EQUIPMENT_DEFS,
    BusItem,
    ConnectionItem,
    JunctionItem,
    OneLineSymbolItem,
    _instance_counters,
    make_user_equipment,
)

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

        # Sheet edge makes the 36 × 48 drawing boundary explicit.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#52606d"), 2.0))
        painter.drawRect(self._rect)


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

        # Bus end-port drag (changes length, keeps the other end fixed)
        self._bus_resize_item: BusItem | None = None
        self._bus_resize_port: str | None = None
        self._bus_resize_origin_x = 0.0
        self._bus_resize_was_movable = True

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

    # --- Grid / PDF / snap --------------------------------------------------

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

    def _wire_preview_path(
        self,
        start: QPointF,
        end: QPointF,
        exclude: OneLineSymbolItem | None = None,
        from_item: OneLineSymbolItem | None = None,
        from_port: str | None = None,
    ) -> QPainterPath:
        block_leave = (
            port_into_direction(from_item, from_port)
            if from_item is not None and from_port is not None
            else None
        )
        block_arrive = None
        hit = self._find_port_at(end)
        if hit is not None and hit[0] is not exclude:
            end = hit[0].port_scene_pos(hit[1])
            block_arrive = port_into_direction(hit[0], hit[1])
        else:
            bus = self._find_bus_at(end, exclude=exclude)
            if bus is not None:
                end = ConnectToBusCommand._drop_point(bus, end)
            else:
                wire_hit = self._find_wire_at(end, exclude_endpoint=exclude)
                if wire_hit is not None:
                    end = wire_hit[1]
                elif self._snap_to_grid:
                    end = snap_point_to_grid(end)
        points = route_wire_on_grid(
            start,
            end,
            snap_endpoints=self._snap_to_grid,
            block_leave=block_leave,
            block_arrive=block_arrive,
        )
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
        # Only consider symbols near the cursor — never scan the whole scene
        # (that made distant ports steal clicks meant for move/pan).
        hit_rect = QRectF(
            scene_pos.x() - PORT_HIT_RADIUS,
            scene_pos.y() - PORT_HIT_RADIUS,
            2 * PORT_HIT_RADIUS,
            2 * PORT_HIT_RADIUS,
        )
        candidates = [
            it for it in scn.items(hit_rect)
            if isinstance(it, OneLineSymbolItem)
        ]
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

    def _find_bus_at(
        self,
        scene_pos: QPointF,
        exclude: OneLineSymbolItem | None = None,
    ) -> BusItem | None:
        """Return a bus whose bar is within BUS_DROP_RADIUS of scene_pos."""
        scn = self.scene()
        if scn is None:
            return None
        best: BusItem | None = None
        best_dist = BUS_DROP_RADIUS
        for item in scn.items():
            if not isinstance(item, BusItem) or item is exclude:
                continue
            dist = item.distance_to_bar(scene_pos)
            if dist <= best_dist:
                best_dist = dist
                best = item
        return best

    def _find_wire_at(
        self,
        scene_pos: QPointF,
        exclude: ConnectionItem | None = None,
        exclude_endpoint: OneLineSymbolItem | None = None,
    ):
        """Return (ConnectionItem, point_on_wire) if cursor is near a wire segment.

        exclude_endpoint skips wires that already terminate on that symbol, so
        dragging one of its ports onto its own connection does not add a node.
        """
        scn = self.scene()
        if scn is None:
            return None
        best = None
        best_dist = WIRE_HIT_RADIUS
        for item in scn.items():
            if not isinstance(item, ConnectionItem) or item is exclude:
                continue
            if exclude_endpoint is not None and (
                item.from_item is exclude_endpoint or item.to_item is exclude_endpoint
            ):
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

    # --- Hit testing (resize / rotate handles) ------------------------------

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

    # --- Rotate / resize gestures -------------------------------------------

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
        snapped = max(MIN_SYMBOL_SCALE, snapped)
        if item.max_scale is not None:
            snapped = min(item.max_scale, snapped)
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

    def _begin_bus_resize(self, bus: BusItem, port: str):
        self._move_origins = {}
        self._clear_snap_ports()
        self._bus_resize_item = bus
        self._bus_resize_port = port
        self._bus_resize_origin_x = bus._base_ports[port].x()
        self._bus_resize_was_movable = bool(
            bus.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        bus.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self._saved_drag_mode = self.dragMode()
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        deg = bus.rotation_deg % 180.0
        cursor = (
            Qt.CursorShape.SizeVerCursor
            if 45.0 <= deg < 135.0
            else Qt.CursorShape.SizeHorCursor
        )
        self.setCursor(cursor)
        self._set_status(f"Drag {bus.instance_id} {port} end to change length")

    def _update_bus_resize(self, scene_pos: QPointF):
        bus = self._bus_resize_item
        port = self._bus_resize_port
        if bus is None or port is None:
            return
        point = bus.project_onto_axis(scene_pos)
        if self._snap_to_grid:
            point = bus.project_onto_axis(snap_point_to_grid(point))
        bus.set_end_base_x(port, bus._unclamped_base_x(point))
        self._set_status(f"Resize {bus.instance_id}")

    def _commit_bus_resize_if_any(self):
        bus = self._bus_resize_item
        port = self._bus_resize_port
        if bus is None or port is None:
            return
        old_x = self._bus_resize_origin_x
        new_x = bus._base_ports[port].x()
        bus.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, self._bus_resize_was_movable)
        self._bus_resize_item = None
        self._bus_resize_port = None
        self.setDragMode(self._saved_drag_mode)
        self.unsetCursor()
        if abs(new_x - old_x) > 1e-6:
            self._push(ResizeBusCommand(bus, port, old_x, new_x))
            self._set_status(f"Resized {bus.instance_id}")
        else:
            self._set_status("Ready")

    def _cancel_bus_resize(self):
        bus = self._bus_resize_item
        port = self._bus_resize_port
        if bus is None or port is None:
            return
        bus.set_end_base_x(port, self._bus_resize_origin_x)
        bus.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, self._bus_resize_was_movable)
        self._bus_resize_item = None
        self._bus_resize_port = None
        self.setDragMode(self._saved_drag_mode)
        self.unsetCursor()
        self._set_status("Resize cancelled")

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

    def set_background_pixmap(
        self,
        pixmap: QPixmap,
        scene_width: float | None = None,
        scene_height: float | None = None,
    ):
        """Place a PDF image at its physical drawing-sheet size."""
        scn = self.scene()
        if self._background_item is not None and scn is not None:
            scn.removeItem(self._background_item)
            self._background_item = None

        self._background_item = QGraphicsPixmapItem(pixmap)
        if (
            scene_width is not None
            and scene_height is not None
            and pixmap.width() > 0
            and pixmap.height() > 0
        ):
            self._background_item.setTransform(
                QTransform.fromScale(
                    scene_width / pixmap.width(),
                    scene_height / pixmap.height(),
                )
            )
        self._background_item.setZValue(-10_000)
        self._background_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self._background_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self._background_item.setVisible(self._pdf_visible)
        scn = self.scene()
        if scn is not None:
            scn.addItem(self._background_item)
            if scene_width is not None and scene_height is not None:
                scn.setSceneRect(QRectF(0, 0, scene_width, scene_height))
            else:
                scn.setSceneRect(self._background_item.sceneBoundingRect())
            self._sync_grid_to_scene()
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    # --- Keyboard & mouse input ---------------------------------------------

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self._rotate_item is not None:
            self._cancel_rotate()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self._bus_resize_item is not None:
            self._cancel_bus_resize()
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

            hit = self._find_port_at(scene_pos)
            if (
                hit is not None
                and isinstance(hit[0], BusItem)
                and hit[1] in ("left", "right")
            ):
                self._begin_bus_resize(hit[0], hit[1])
                event.accept()
                return

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

            # Not on a port — track positions for a possible move undo.
            # (Tee junctions are created only when a wire drag is dropped onto
            # an existing wire — never on a plain click.)
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
        if self._bus_resize_item is not None:
            self._update_bus_resize(self.mapToScene(event.position().toPoint()))
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
                self._temp_line.setPath(
                    self._wire_preview_path(
                        start,
                        end,
                        exclude=from_item,
                        from_item=from_item,
                        from_port=from_port,
                    )
                )
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

        if event.button() == Qt.MouseButton.LeftButton and self._bus_resize_item is not None:
            self._commit_bus_resize_if_any()
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

            # Drop onto a bus bar → add a node where the wire lands.
            if self._wiring:
                bus = self._find_bus_at(scene_pos, exclude=from_item)
                if bus is not None:
                    self._push(
                        ConnectToBusCommand(
                            self.scene(), from_item, from_port, bus, scene_pos
                        )
                    )
                    self._cancel_pending()
                    self._set_status(
                        f"Connected {from_item.instance_id}:{from_port} → "
                        f"{bus.instance_id}"
                    )
                    event.accept()
                    return

            # Drop onto an existing wire → tee with an auto junction.
            # A wire that already ends on this component is not a valid tee.
            if self._wiring:
                wire_hit = self._find_wire_at(scene_pos, exclude_endpoint=from_item)
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


