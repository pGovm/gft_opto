# Builds the main application window and connects editing, layout files, zone studies, and report export.

"""
AI-Assisted Substation Design Tool — main window.

The canvas, symbols, wires, and undo commands live in sibling modules.
This file builds the panels around the workspace and starts the application.
"""

import functools
import json
import sys
import time
import math
import os
import tempfile
import copy
from html import escape

import pymupdf
from PySide6.QtCore import Qt, QByteArray, QMimeData, QRectF
from PySide6.QtGui import (
    QAction, QColor, QPen, QBrush, QPalette,
    QDrag, QFont, QImage, QKeySequence, QPixmap, QTextDocument, QUndoStack,
)
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGraphicsScene,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSlider, QTextEdit,
    QVBoxLayout, QWidget, QGraphicsRectItem, QGraphicsPathItem, QSpinBox, QScrollArea,
)

from gft_opto.gui.circuit_check import check_circuit
from gft_opto.gui.custom_widget_tool import ComponentDialog, SYMBOL_TYPE_CHOICES
from gft_opto.gui.routing import (
    DEFAULT_GRID_OPACITY,
    DEFAULT_WORKSPACE_HEIGHT,
    DEFAULT_WORKSPACE_WIDTH,
    GFT_LOGO_HEIGHT,
    GFT_LOGO_PATH,
    MIME_EQUIPMENT,
    SCENE_UNITS_PER_INCH,
    SYMBOL_ROTATION_STEP,
    UI_ACCENT,
)
from gft_opto.gui.symbols import (
    BUILTIN_SYMBOLS,
    EQUIPMENT_DEFS,
    BusItem,
    ConnectionItem,
    CurrentTransformerItem,
    CustomComponentItem,
    OneLineSymbolItem,
    make_user_equipment,
)
from gft_opto.gui.workspace import WorkspaceView
from gft_opto.gui.commands import EditPropertyCommand, ReplaceZonesCommand
from gft_opto.gui.layout_io import scene_data, build_scene, reserve_ids, write_layout, library_from_layout
from gft_opto.logic.momentary_calc2 import netlist_from_scene
from gft_opto.logic.protection_zones import connection_key, identify_zones
from gft_opto.logic.protection_study import propose_zones, evaluate_zones, topology_records
from gft_opto.gui.zone_dialog import ZoneDialog
from gft_opto.gui.theme import SCROLLBAR_STYLE
from gft_opto.logic.breaker_settings import breaker_settings, SETTING_KEYS
from pathlib import Path
from collections import defaultdict


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
def _resource_root() -> Path:
    """Package directory in a checkout, or the frozen bundle root in an .exe."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parent.parent


GFT_LOGO_PATH = _resource_root() / "assets" / "gft_logo.png"
GFT_LOGO_HEIGHT = 34         # px — header wordmark height

_instance_counters: dict[str, int] = defaultdict(int)


# ---------------------------------------------------------------------------
# Helpers — logo, IDs, grid snap, wire geometry
# ---------------------------------------------------------------------------

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
# Main window
# ---------------------------------------------------------------------------

class SubstationGuiMockup(QMainWindow):
    """Top-level window assembling header, panels, canvas, and footer."""

    def __init__(self):
        super().__init__()
        self.layout_path = None
        self.pdf_path = None
        self._loading = True
        self._saved_snapshot = None
        self.last_netlist: dict | None = None
        self.last_result: dict | None = None
        self.protection_zones = []
        self._zone_overlays = []
        self._zone_report = None
        self.setWindowTitle("AI-Assisted Substation Design Tool")
        self.setMinimumSize(1000, 700)
        self._apply_stylesheet()
        self._build_ui()
        self._setup_undo_redo()
        self._setup_file_shortcuts()
        self._loading = False
        self._saved_snapshot = self._layout_data()
        self.project_box.editingFinished.connect(self._mark_modified)
        for control in (self.grid_show_cb, self.grid_snap_cb, self.pdf_show_cb):
            control.toggled.connect(self._mark_modified)
        self.grid_opacity_slider.valueChanged.connect(self._mark_modified)
        self._on_selection_changed()

    def _apply_stylesheet(self):
        self.setStyleSheet("""
    QMainWindow { background-color: #f4f6f8; }

    QMenuBar, QMenu {
        background-color: #ffffff;
        color: #1f2933;
        font-family: "Segoe UI";
        font-size: 11pt;
    }
    QMenuBar::item { background: transparent; padding: 6px 12px; }
    QMenuBar::item:selected, QMenu::item:selected {
        background-color: #006A4E;
        color: #ffffff;
    }
    QMenu::item { padding: 7px 24px; }
    QMenu::item:disabled { color: #64748b; }

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

    QLineEdit, QTextEdit, QComboBox, QListWidget, QSpinBox {
        background: white; border: 1px solid #cbd2d9;
        border-radius: 8px; padding: 8px; color: #1f2933;
    }
    QComboBox QAbstractItemView {
        background-color: #ffffff; color: #1f2933;
        selection-background-color: #006a4e; selection-color: #ffffff;
        font-family: "Segoe UI"; font-size: 10pt; font-weight: normal;
    }
    QGroupBox#selectedProperties QWidget {
        color: #1f2933; font-family: "Segoe UI";
        font-size: 10pt; font-weight: normal;
    }
    QGroupBox#selectedProperties QLabel { background-color: transparent; }
    QGroupBox#selectedProperties QLineEdit,
    QGroupBox#selectedProperties QComboBox,
    QGroupBox#selectedProperties QSpinBox {
        background-color: #ffffff; color: #1f2933;
        selection-background-color: #006a4e; selection-color: #ffffff;
        padding: 6px; min-height: 20px;
    }
    QGroupBox#selectedProperties QComboBox:disabled {
        background-color: #f4f6f8; color: #52606d;
    }
    QScrollArea#propertiesScroll { border: none; background-color: #f4f6f8; }
    QGroupBox#quickActions { padding-top: 16px; }
    QGroupBox#quickActions QPushButton { padding: 5px 12px; }
    QFrame#canvasFrame { background: white; border: 2px dashed #9fb3c8; border-radius: 14px; }
    QFrame#toolbarFrame { background: white; border: 1px solid #d9e2ec; border-radius: 12px; }

    QMessageBox {
        background-color: #ffffff;
    }
    QMessageBox QLabel {
        color: #1f2933;
        background-color: transparent;
        font-size: 11pt;
    }
""" + SCROLLBAR_STYLE)

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
        self._mark_modified()
        if not hasattr(self, "footer_status_label"):
            return
        text = self.undo_stack.undoText() if self.undo_stack.canUndo() else ""
        self.footer_status_label.setText(
            f"Ready — last action: {text}" if text else "Ready"
        )

    def _setup_file_shortcuts(self):
        for title, shortcut, callback in (
            ("New Layout", "Ctrl+N", self.new_layout),
            ("Open Layout…", "Ctrl+O", self.open_layout),
            ("Save Layout", "Ctrl+S", lambda: self.save_layout()),
            ("Save Layout As…", "Ctrl+Shift+S", lambda: self.save_layout(save_as=True)),
        ):
            action = QAction(title, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            self.addAction(action)

    def _layout_data(self):
        rect = self.workspace_scene.sceneRect()
        return {
            "version": 1, "project": self.project_box.text().strip() or "Untitled Project",
            "scene_rect": [rect.x(), rect.y(), rect.width(), rect.height()],
            "pdf": self.pdf_path,
            "zones": copy.deepcopy(self.protection_zones),
            "library": {key: meta["definition"] for key, meta in EQUIPMENT_DEFS.items()},
            "settings": {"grid_visible": self.grid_show_cb.isChecked(),
                         "grid_opacity": self.grid_opacity_slider.value(),
                         "snap": self.grid_snap_cb.isChecked(),
                         "pdf_visible": self.pdf_show_cb.isChecked()},
            **scene_data(self.workspace_scene),
        }

    def _invalidate_results(self):
        self._clear_zone_highlights()
        self._zone_report = None
        self.zone_view.blockSignals(True)
        self.zone_view.clear()
        self.zone_view.addItem("Run Evaluation to display zones")
        self.zone_view.blockSignals(False)
        if self.last_result is not None:
            self.output_box.append("\nResults are outdated. Run Evaluation again before exporting.")
        self.last_result = self.last_netlist = None
        self.save_results_btn.setEnabled(False)

    def _mark_modified(self, *_args):
        if self._loading:
            return
        data = self._layout_data()
        self.setWindowTitle(f"{data['project']}{' *' if data != self._saved_snapshot else ''} — Substation Design Tool")
        if self.last_result is not None and data != self._evaluated_snapshot:
            self._invalidate_results()

    def _confirm_discard(self):
        if self._layout_data() == self._saved_snapshot:
            return True
        answer = QMessageBox.question(
            self, "Unsaved Layout", "Save changes to this layout?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save_layout()
        return answer == QMessageBox.StandardButton.Discard

    def save_layout(self, path=None, save_as=False):
        path = path or (None if save_as else self.layout_path)
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Save Layout", "layout.json", "Layout Files (*.json)")
        if not path:
            return False
        if not str(path).lower().endswith(".json"):
            path = str(path) + ".json"
        try:
            data = self._layout_data()
            # Validate the same representation that Open Layout will accept.
            checked = build_scene(data)
            checked.clear()
            write_layout(path, data)
        except Exception as error:
            QMessageBox.critical(self, "Save Failed", f"Could not save the layout:\n{error}")
            return False
        self.layout_path = str(Path(path).resolve())
        self._saved_snapshot = data
        self._mark_modified()
        self.footer_status_label.setText(f"Layout saved: {self.layout_path}")
        return True

    def new_layout(self):
        if not self._confirm_discard():
            return
        data = {"version": 1, "project": "Untitled Project", "pdf": None,
                "scene_rect": [0, 0, DEFAULT_WORKSPACE_WIDTH, DEFAULT_WORKSPACE_HEIGHT],
                "components": [], "connections": [], "library": {},
                "settings": {"grid_visible": True, "grid_opacity": round(DEFAULT_GRID_OPACITY * 100),
                             "snap": True, "pdf_visible": True}}
        self._install_layout(data, build_scene(data), None, None)

    def open_layout(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open Layout", "", "Layout Files (*.json)")
        if not path:
            return False
        # Build the entire replacement before discarding any current work.
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            scene = build_scene(data)
            pdf = data.get("pdf")
            normalized_pdf = str((Path(path).resolve().parent / pdf).resolve()) if pdf else pdf
            pdf = normalized_pdf
            pixmap = None
            if pdf:
                try:
                    pixmap = self._read_pdf_pixmap(pdf)
                except Exception:
                    replacement, _ = QFileDialog.getOpenFileName(
                        self, "Locate Missing or Unreadable Background PDF (Cancel to open without it)", "", "PDF Files (*.pdf)"
                    )
                    if replacement:
                        pdf = str(Path(replacement).resolve())
                        pixmap = self._read_pdf_pixmap(pdf)
            if not self._confirm_discard():
                return False
            # Normalize the original reference before taking the saved snapshot.
            # Selecting a different replacement PDF remains an unsaved edit.
            self._install_layout(dict(data, pdf=normalized_pdf), scene, str(Path(path).resolve()), pixmap)
            self.pdf_path = pdf
            self._mark_modified()
            self.footer_status_label.setText(
                "Layout opened; background PDF unavailable" if pdf and pixmap is None else f"Layout opened: {path}"
            )
            return True
        except Exception as error:
            QMessageBox.critical(self, "Open Failed", f"Could not open the layout:\n{error}")
            return False

    @staticmethod
    def _read_pdf_pixmap(path):
        with pymupdf.open(path) as document:
            pix = document.load_page(0).get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
            image = QPixmap()
            if not image.loadFromData(pix.tobytes("png")):
                raise ValueError("Could not decode PDF background.")
            return image

    def _install_layout(self, data, staged_scene, path, pixmap):
        self._loading = True
        self._clear_zone_highlights()
        self._zone_report = None
        self.protection_zones = copy.deepcopy(data.get("zones", []))
        view = self.workspace_view
        view._cancel_pending()
        view._clear_drop_preview()
        view._clear_snap_ports()
        view._move_origins = {}
        view._resize_item = None
        view._rotate_item = None
        view._bus_resize_item = None
        self.undo_stack.clear()
        old_scene = self.workspace_scene
        old_scene.selectionChanged.disconnect(self._on_selection_changed)
        staged_scene.setParent(self)
        self.workspace_scene = staged_scene
        view.setScene(staged_scene)
        view._background_item = None
        view._grid_item = None
        view._init_grid()
        staged_scene.selectionChanged.connect(self._on_selection_changed)
        old_scene.deleteLater()
        self._workspace_original = pixmap
        if pixmap is not None:
            view.set_background_pixmap(pixmap, scene_width=data["scene_rect"][2], scene_height=data["scene_rect"][3])
            staged_scene.setSceneRect(QRectF(*data["scene_rect"]))
            view._sync_grid_to_scene()
        self.pdf_path = data.get("pdf")
        self.layout_path = path
        self.project_box.setText(data["project"])
        EQUIPMENT_DEFS.clear()
        self.equipment_list.clear()
        self.equipment_search.clear()
        for key, definition in library_from_layout(data).items():
            EQUIPMENT_DEFS[key] = {"label": definition["name"], "definition": definition,
                "factory": functools.partial(make_user_equipment, definition["symbol"], definition["name"], definition["properties"])}
            entry = QListWidgetItem(definition["name"])
            entry.setData(Qt.ItemDataRole.UserRole, key)
            self.equipment_list.addItem(entry)
        settings = data["settings"]
        self.grid_show_cb.setChecked(settings["grid_visible"])
        self.grid_snap_cb.setChecked(settings["snap"])
        self.pdf_show_cb.setChecked(settings["pdf_visible"])
        self.pdf_show_cb.setEnabled(pixmap is not None)
        self.grid_opacity_slider.setValue(settings["grid_opacity"])
        view.set_grid_visible(settings["grid_visible"])
        view.set_snap_to_grid(settings["snap"])
        view.set_pdf_visible(settings["pdf_visible"])
        view.set_grid_opacity(settings["grid_opacity"])
        reserve_ids(staged_scene)
        self.last_result = self.last_netlist = None
        self.output_box.clear()
        self.save_results_btn.setEnabled(False)
        self.zone_view.blockSignals(True)
        self.zone_view.clear()
        self.zone_view.addItem("Run Evaluation to display zones")
        self.zone_view.blockSignals(False)
        self._on_selection_changed()
        self._saved_snapshot = self._layout_data()
        self._loading = False
        self._mark_modified()
        view.fitInView(staged_scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def closeEvent(self, event):
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()

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
        self.grid_opacity_label = QLabel(f"{self.grid_opacity_slider.value()}%")
        opacity_row.addWidget(self.grid_opacity_slider, 1)
        opacity_row.addWidget(self.grid_opacity_label)

        def _on_opacity_changed(value: int):
            self.grid_opacity_label.setText(f"{value}%")
            self.workspace_view.set_grid_opacity(value)

        self.grid_opacity_slider.valueChanged.connect(_on_opacity_changed)
        self.grid_opacity_slider.setValue(round(DEFAULT_GRID_OPACITY * 100))

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
        layout = QVBoxLayout(frame)
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
        title.setWordWrap(True)

        project_box = QLineEdit("Untitled Project")
        project_box.setMinimumWidth(160)
        self.project_box = project_box

        save_btn = QPushButton("Save Layout")
        save_btn.clicked.connect(lambda: self.save_layout())
        self.save_as_btn = QPushButton("Save Layout As")
        self.save_as_btn.clicked.connect(lambda: self.save_layout(save_as=True))
        self.open_layout_btn = QPushButton("Open Layout")
        self.open_layout_btn.clicked.connect(lambda: self.open_layout())
        self.new_layout_btn = QPushButton("New Layout")
        self.new_layout_btn.clicked.connect(self.new_layout)
        self.load_btn = QPushButton("Import PDF")
        self.load_btn.clicked.connect(self.on_import_pdf_clicked)
        self.check_btn = QPushButton("Validate")
        self.check_btn.clicked.connect(self._on_check_circuit_clicked)
        self.run_btn = QPushButton("Run Evaluation")
        self.run_btn.clicked.connect(self._on_run_evaluation_clicked)

        self.save_results_btn = QPushButton("Save Calculation Results")
        self.save_results_btn.clicked.connect(self._on_save_results_clicked)
        self.save_results_btn.setEnabled(False)

        identity_row = QHBoxLayout()
        identity_row.addWidget(logo)
        identity_row.addWidget(title, 1)
        identity_row.addWidget(QLabel("Project:"))
        identity_row.addWidget(project_box)
        layout.addLayout(identity_row)

        actions_grid = QGridLayout()
        actions_grid.setHorizontalSpacing(12)
        actions_grid.setVerticalSpacing(10)
        buttons = (self.new_layout_btn, self.open_layout_btn, save_btn, self.save_as_btn,
                   self.load_btn, self.check_btn, self.run_btn, self.save_results_btn)
        for index, button in enumerate(buttons):
            actions_grid.addWidget(button, index // 4, index % 4)
        for column in range(4):
            actions_grid.setColumnStretch(column, 1)
        layout.addLayout(actions_grid)
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
        self.equipment_search = search
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
        self.controls_group.setObjectName("quickActions")
        self.controls_layout = QVBoxLayout(self.controls_group)
        self.controls_layout.setContentsMargins(10, 8, 10, 10)
        self.controls_layout.setSpacing(4)

        self.undo_btn = QPushButton("Undo")
        self.redo_btn = QPushButton("Redo")
        self.controls_layout.addWidget(self.undo_btn)
        self.controls_layout.addWidget(self.redo_btn)

        clipboard_row = QHBoxLayout()
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setToolTip("Copy selected components (Ctrl+C in workspace)")
        self.copy_btn.clicked.connect(lambda: self.workspace_view.copy_selected())
        self.paste_btn = QPushButton("Paste")
        self.paste_btn.setToolTip("Paste components (Ctrl+V in workspace)")
        self.paste_btn.clicked.connect(lambda: self.workspace_view.paste_components())
        clipboard_row.addWidget(self.copy_btn)
        clipboard_row.addWidget(self.paste_btn)
        self.controls_layout.addLayout(clipboard_row)

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

        layout.addWidget(palette_group, 1)
        layout.addWidget(self.controls_group, 0)
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
        self.workspace_view.setStyleSheet("QGraphicsView { background: white; border: none; }")

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
        properties_group.setObjectName("selectedProperties")
        palette = QPalette(properties_group.palette())
        for role, color in ((QPalette.ColorRole.Window, "#ffffff"), (QPalette.ColorRole.Base, "#ffffff"),
                            (QPalette.ColorRole.Text, "#1f2933"), (QPalette.ColorRole.WindowText, "#1f2933"),
                            (QPalette.ColorRole.ButtonText, "#1f2933"), (QPalette.ColorRole.Button, "#ffffff"),
                            (QPalette.ColorRole.Highlight, "#006a4e"), (QPalette.ColorRole.HighlightedText, "#ffffff")):
            palette.setColor(role, QColor(color))
        properties_group.setPalette(palette)
        properties_layout = QGridLayout(properties_group)
        properties_layout.setHorizontalSpacing(16)
        properties_layout.setVerticalSpacing(12)

        properties_layout.addWidget(QLabel("Type:"), 0, 0)
        self.prop_type = QLineEdit("—")
        self.prop_type.setReadOnly(True)
        properties_layout.addWidget(self.prop_type, 0, 1)
        properties_layout.addWidget(QLabel("ID:"), 1, 0)
        self.prop_name = QLineEdit("—")
        self.prop_name.editingFinished.connect(lambda: self._on_property_id_changed(self.prop_name.text()))
        properties_layout.addWidget(self.prop_name, 1, 1)

        self.prop_status_label = QLabel("Status:")
        properties_layout.addWidget(self.prop_status_label, 2, 0)
        self.prop_status = QComboBox()
        self.prop_status.addItems(["Closed", "Open", "Maintenance"])
        self.prop_status.currentTextChanged.connect(self._on_property_status_changed)
        properties_layout.addWidget(self.prop_status, 2, 1)

        self.prop_trip_coil_1_label = QLabel("Trip Coil 1 (A):")
        properties_layout.addWidget(self.prop_trip_coil_1_label, 3, 0)
        self.prop_trip_coil_1 = QLineEdit("—")
        self.prop_trip_coil_1.editingFinished.connect(
            lambda: self._on_numeric_property_changed("trip_coil_1_a", self.prop_trip_coil_1.text())
        )
        properties_layout.addWidget(self.prop_trip_coil_1, 3, 1)

        self.prop_trip_coil_2_label = QLabel("Trip Coil 2 (A):")
        properties_layout.addWidget(self.prop_trip_coil_2_label, 4, 0)
        self.prop_trip_coil_2 = QLineEdit("—")
        self.prop_trip_coil_2.editingFinished.connect(
            lambda: self._on_numeric_property_changed("trip_coil_2_a", self.prop_trip_coil_2.text())
        )
        properties_layout.addWidget(self.prop_trip_coil_2, 4, 1)

        self.prop_close_coil_label = QLabel("Close Coil (A):")
        properties_layout.addWidget(self.prop_close_coil_label, 5, 0)
        self.prop_close_coil = QLineEdit("—")
        self.prop_close_coil.editingFinished.connect(
            lambda: self._on_numeric_property_changed("close_coil_a", self.prop_close_coil.text())
        )
        properties_layout.addWidget(self.prop_close_coil, 5, 1)

        self.prop_motor_inrush_label = QLabel("Motor Inrush Current (A):")
        properties_layout.addWidget(self.prop_motor_inrush_label, 6, 0)
        self.prop_motor_inrush = QLineEdit("—")
        self.prop_motor_inrush.editingFinished.connect(
            lambda: self._on_numeric_property_changed("motor_inrush_a", self.prop_motor_inrush.text())
        )
        properties_layout.addWidget(self.prop_motor_inrush, 6, 1)

        self.prop_motor_run_label = QLabel("Motor Run Current (A):")
        properties_layout.addWidget(self.prop_motor_run_label, 7, 0)
        self.prop_motor_run = QLineEdit("—")
        self.prop_motor_run.editingFinished.connect(
            lambda: self._on_numeric_property_changed("motor_run_a", self.prop_motor_run.text())
        )
        properties_layout.addWidget(self.prop_motor_run, 7, 1)

        self.prop_voltage_label = QLabel("Voltage (kV):")
        properties_layout.addWidget(self.prop_voltage_label, 8, 0)
        self.prop_voltage = QLineEdit("—")
        self.prop_voltage.setPlaceholderText("kV")
        self.prop_voltage.editingFinished.connect(lambda: self._on_property_voltage_changed(self.prop_voltage.text()))
        properties_layout.addWidget(self.prop_voltage, 8, 1)

        self.breaker_fields = {}
        self._breaker_widgets = []
        choices = {
            "coil_current_basis": [("Whole breaker", "whole_breaker"), ("Per mechanism", "per_mechanism")],
            "mechanism_arrangement": [("Shared mechanism", "shared"), ("Independent per pole", "independent")],
        }
        labels = {"coil_current_basis": "Coil current basis:", "mechanism_arrangement": "Mechanisms:",
                  "pole_count": "Breaker poles:", "trip_coil_1_supply": "Trip Coil 1 supply:",
                  "trip_coil_2_supply": "Trip Coil 2 supply:", "close_coil_supply": "Close Coil supply:",
                  "motor_supply": "Motor supply:"}
        for row, key in enumerate(SETTING_KEYS, 9):
            label = QLabel(labels[key])
            if key in choices:
                field = QComboBox()
                for text, value in choices[key]:
                    field.addItem(text, value)
                field.currentIndexChanged.connect(lambda _index, key=key: self._on_breaker_setting_changed(key))
            elif key == "pole_count":
                field = QSpinBox()
                field.setRange(1, 3)
                field.valueChanged.connect(lambda _value, key=key: self._on_breaker_setting_changed(key))
            else:
                field = QLineEdit()
                field.editingFinished.connect(lambda key=key: self._on_breaker_setting_changed(key))
            properties_layout.addWidget(label, row, 0)
            properties_layout.addWidget(field, row, 1)
            self.breaker_fields[key] = field
            self._breaker_widgets.extend((label, field))
        self.breaker_fields["coil_current_basis"].setToolTip(
            "Applies to trip and close coils. Motor currents are always entered for the whole device."
        )

        self._non_bus_property_widgets = [
            self.prop_status_label,
            self.prop_status,
            self.prop_trip_coil_1_label,
            self.prop_trip_coil_1,
            self.prop_trip_coil_2_label,
            self.prop_trip_coil_2,
            self.prop_close_coil_label,
            self.prop_close_coil,
            self.prop_motor_inrush_label,
            self.prop_motor_inrush,
            self.prop_motor_run_label,
            self.prop_motor_run,
        ]
        self._set_bus_property_mode(False)

        analysis_group = QGroupBox("Analysis Output")
        analysis_layout = QVBoxLayout(analysis_group)
        self.zone_setup_btn = QPushButton("Configure Protection Zones")
        self.zone_setup_btn.clicked.connect(self._configure_protection_zones)
        analysis_layout.addWidget(self.zone_setup_btn)
        self.expand_analysis_btn = QPushButton("Expand Analysis")
        self.expand_analysis_btn.clicked.connect(self._expand_zone_analysis)
        analysis_layout.addWidget(self.expand_analysis_btn)
        self.zone_view = QComboBox()
        self.zone_view.addItem("Run Evaluation to display zones")
        self.zone_view.currentIndexChanged.connect(self._show_zone_highlights)
        analysis_layout.addWidget(self.zone_view)
        self.output_box = QTextEdit()
        self.output_box.setPlaceholderText("Analysis results will appear here...")
        self.output_box.setReadOnly(True)
        analysis_layout.addWidget(self.output_box)

        properties_scroll = QScrollArea()
        properties_scroll.setObjectName("propertiesScroll")
        properties_scroll.viewport().setAutoFillBackground(True)
        properties_scroll.viewport().setPalette(palette)
        properties_scroll.setWidgetResizable(True)
        properties_scroll.setWidget(properties_group)
        properties_scroll.setMinimumHeight(180)
        layout.addWidget(properties_scroll, 3)
        layout.addWidget(analysis_group, 4)

        if hasattr(self, "workspace_scene"):
            self.workspace_scene.selectionChanged.connect(self._on_selection_changed)

        return container

    # --- Event handlers -----------------------------------------------------

    def _on_check_circuit_clicked(self):
        """Report open terminals and short circuits in the drawn one-line."""
        if not hasattr(self, "workspace_scene"):
            return
        report = check_circuit(self.workspace_scene)
        lines = [
            "Circuit check: OK" if report["ok"] else "Circuit check: problems found",
            "",
        ]
        open_ends = report["open_ends"]
        shorts = report["shorts"]
        lines.append("Open ends:" if open_ends else "Open ends: none")
        for issue in open_ends:
            lines.append(f"  • {issue}")
        lines.append("")
        lines.append("Short circuits:" if shorts else "Short circuits: none")
        for issue in shorts:
            lines.append(f"  • {issue}")
        if hasattr(self, "footer_status_label"):
            if report["ok"]:
                self.footer_status_label.setText("Circuit check passed")
            elif open_ends == ["No components on the canvas."]:
                self.footer_status_label.setText("Place components before checking the circuit")
            else:
                self.footer_status_label.setText(
                    f"Circuit check: {len(open_ends)} open, {len(shorts)} short"
                )
        message = "\n".join(lines)
        if report["ok"]:
            QMessageBox.information(self, "Validate", message)
        else:
            QMessageBox.warning(self, "Validate", message)

    def _protection_netlist(self):
        netlist = netlist_from_scene(self.workspace_scene,
            system_name=self.project_box.text().strip() or "Untitled Project")
        labels = {item.instance_id: item.visible_id() for item in self.workspace_scene.items()
                  if isinstance(item, OneLineSymbolItem)}
        for component in netlist["components"]:
            component["label"] = labels[component["id"]]
        return netlist

    def _scene_topology(self):
        """Detect real scene components and conductors; never save live Zone objects."""
        items = [item for item in self.workspace_scene.items()
                 if isinstance(item, OneLineSymbolItem)]
        primary = identify_zones(items, (CurrentTransformerItem,))
        return topology_records(primary)

    def _configure_protection_zones(self):
        try:
            netlist = self._protection_netlist()
            dialog = ZoneDialog(netlist, self.protection_zones or propose_zones(netlist), self)
        except (ValueError, TypeError) as error:
            QMessageBox.warning(self, "Protection Zone Settings", str(error))
            return
        if dialog.exec() == QDialog.DialogCode.Accepted:
            changes = []
            for item in self.workspace_scene.items():
                if isinstance(item, OneLineSymbolItem) and item.equip_type == "breaker":
                    props = dialog.components[item.instance_id]["properties"]
                    changes.extend((item, key, props[key]) for key in SETTING_KEYS
                                   if key in props and getattr(item, "properties", {}).get(key) != props[key])
            if changes or self.protection_zones != dialog.zones:
                self.undo_stack.beginMacro("Configure protection zones and breaker settings")
                self.undo_stack.push(ReplaceZonesCommand(self.protection_zones, dialog.zones, self._apply_protection_zones))
                for item, key, value in changes:
                    self.undo_stack.push(EditPropertyCommand(item, key, value, self._on_selection_changed))
                self.undo_stack.endMacro()
            self._mark_modified()
            self._on_run_evaluation_clicked()

    def _apply_protection_zones(self, zones):
        self.protection_zones = zones
        self._on_selection_changed()

    def _expand_zone_analysis(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Momentary Load Analysis")
        dialog.resize(950, 700)
        layout = QVBoxLayout(dialog)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(self.output_box.toPlainText() or "Run Evaluation to generate a protection-zone study.")
        layout.addWidget(text)
        close = QPushButton("Close")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def _clear_zone_highlights(self):
        for overlay in self._zone_overlays:
            if overlay.scene() is not None:
                overlay.scene().removeItem(overlay)
        self._zone_overlays = []

    def _show_zone_highlights(self, *_):
        self._clear_zone_highlights()
        if self._zone_report is None:
            return
        selected = self.zone_view.currentData()
        topology_mode = isinstance(selected, (tuple, list)) and selected[0] == "topology"
        selected_id = selected[1] if topology_mode else selected
        rows = self._zone_report["topology_regions"] if topology_mode else self._zone_report["zones"]
        symbols = {item.instance_id: item for item in self.workspace_scene.items()
                   if isinstance(item, OneLineSymbolItem)}
        wires = [item for item in self.workspace_scene.items() if isinstance(item, ConnectionItem)]
        colors = ("#0072b2", "#d55e00", "#009e73", "#cc79a7", "#e69f00", "#56b4e9")
        for index, zone in enumerate(rows):
            if selected_id and zone["id"] != selected_id:
                continue
            color = QColor(colors[index % len(colors)])
            pen = QPen(color, 3)
            if topology_mode:
                pen.setStyle(Qt.PenStyle.DotLine)
            elif not zone["complete"]:
                pen.setStyle(Qt.PenStyle.DashLine)
            tooltip = zone["name"] + (" (topology only; no automatic current)" if topology_mode else "")
            for ident in zone["members"]:
                if ident not in symbols:
                    continue
                margin = 8 + (index % 3) * 4
                overlay = QGraphicsRectItem(symbols[ident].sceneBoundingRect().adjusted(-margin, -margin, margin, margin))
                overlay.setPen(pen)
                fill = QColor(color)
                fill.setAlpha(25)
                overlay.setBrush(QBrush(fill))
                self._add_zone_overlay(overlay, tooltip, zone["id"])
            # Membership is terminal-aware. Testing only both endpoint IDs would
            # incorrectly highlight a parallel conductor on the other CT sides.
            exact_keys = {tuple(tuple(endpoint) for endpoint in edge) for edge in zone["connections"]}
            for wire in wires:
                key = connection_key(wire)
                if key not in exact_keys:
                    continue
                overlay = QGraphicsPathItem(wire.sceneTransform().map(wire.path()))
                wire_pen = QPen(color, 7)
                wire_pen.setCosmetic(True)
                overlay.setPen(wire_pen)
                overlay.setOpacity(0.4)
                overlay._zone_connection_key = key
                self._add_zone_overlay(overlay, tooltip, zone["id"])

    def _add_zone_overlay(self, overlay, name, zone_id=None):
        overlay.setZValue(10)
        overlay.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        overlay.setToolTip(name)
        overlay.setData(0, "protection-zone-overlay")
        overlay.setData(1, zone_id)
        self.workspace_scene.addItem(overlay)
        self._zone_overlays.append(overlay)

    def _on_run_evaluation_clicked(self):
        """Propose zones, evaluate explicit actions, and display complete or draft results."""
        started = time.perf_counter()
        self._invalidate_results()
        try:
            netlist = self._protection_netlist()
            if not netlist["components"]:
                self.output_box.setPlainText("No components to evaluate. Rebuild the one-line and enter its current loads.")
                self.footer_status_label.setText("Place components before evaluating protection zones")
                return
            if not self.protection_zones:
                self.protection_zones = propose_zones(netlist)
                self._mark_modified()
            report = evaluate_zones(netlist, self.protection_zones)
            # The catalog uses original GUI objects, including CT-only conductors.
            # Editable operating settings remain separate JSON dictionaries.
            report["topology_regions"] = self._scene_topology()
            self._zone_report = report
            self.zone_view.blockSignals(True)
            self.zone_view.clear()
            self.zone_view.addItem("All configured zones (dashed = incomplete)", None)
            for index, zone in enumerate(report["zones"]):
                self.zone_view.addItem(f"{index + 1}. {zone['name']} — {'confirmed' if zone['complete'] else 'review required'}", zone["id"])
            self.zone_view.addItem("All detected regions (topology only)", ("topology", None))
            for region in report["topology_regions"]:
                enclosure = " [enclosed]" if region["enclosed_by"] else " [outermost]"
                self.zone_view.addItem(region["name"] + enclosure, ("topology", region["id"]))
            if not report["zones"]:
                self.zone_view.setCurrentIndex(1)
            self.zone_view.blockSignals(False)
            self._show_zone_highlights()
            lines = [f"Momentary DC protection-zone study: {netlist['system_name']}",
                "COMPLETE" if report["complete"] else "INCOMPLETE — configure/review flagged zones; partial totals are not final.",
                "Each configured zone is a separate fault event. Same-stage loads are simultaneous; other stages are separate.",
                "DC supplies are reported separately. No battery amp-hour calculation.", ""]
            all_loads, warnings = [], list(report["warnings"])
            lines.extend(f"REVIEW: {warning}" for warning in warnings)
            for zone in report["zones"]:
                lines.extend([f"{zone['name']} ({zone['kind']})", "  CT boundaries: " + ", ".join(zone["boundaries"]),
                              "  Equipment: " + ", ".join(zone["members"])])
                for warning in zone["warnings"]:
                    lines.append(f"  REVIEW: {warning}")
                    warnings.append(f"{zone['name']}: {warning}")
                for scenario in zone["scenarios"]:
                    prefix = "" if zone["complete"] else "Provisional "
                    lines.append(f"  {prefix}{scenario['stage']}: {scenario['peak_current_A']:g} A across assigned supplies")
                    for advice in scenario.get("advisories", []):
                        lines.append(f"    NOTE: {advice}")
                    for supply, current in scenario["supplies"].items():
                        lines.append(f"    {supply}: {current:g} A")
                    for load in scenario["loads"]:
                        lines.append(f"    {load['name']}: {load['amps_per_unit']:g} A × {load['quantity']} = {load['total_amps']:g} A ({load['supply']})")
                    all_loads.extend(scenario["loads"])
                lines.append("")
            lines.append("Detected topology regions (not automatically added to the calculation):")
            for region in report["topology_regions"]:
                relation = "enclosed" if region["enclosed_by"] else "outermost"
                lines.append(f"  {region['name']} ({relation}; {len(region['connections'])} conductors)")
            lines.append("")
            lines.append("Peak per supply among confirmed, complete configured zones:")
            for supply, peak in report["supply_peaks"].items():
                lines.append(f"  {supply}: {peak['peak_current_A']:g} A — {peak['zone']} / {peak['stage']}")
            if not report["supply_peaks"]:
                lines.append("  Not available until zone assignments and currents are complete.")
            worst_supply = max(report["supply_peaks"],
                key=lambda supply: report["supply_peaks"][supply]["peak_current_A"], default=None)
            self.last_result = {"scenario_name": netlist["system_name"], "voltage_V": netlist["voltage_V"],
                "peak_current_A": report["supply_peaks"][worst_supply]["peak_current_A"] if worst_supply and report["complete"] else "Incomplete study",
                "worst_case_supply": worst_supply,
                "loads": all_loads, "warnings": warnings, "protection_analysis": report,
                "calculation_steps": ["Group actions by zone and operating stage.",
                    "Count each physical coil or motor once per stage; multiply entered current by the verified unit count.",
                    "Sum currents per DC supply within each stage, then take each supply's maximum across complete zones.",
                    "Stages and separate zone faults are not added together. Flagged zones have provisional results only."]}
            netlist["zones"] = copy.deepcopy(self.protection_zones)
            self.last_netlist = netlist
            self._evaluated_snapshot = self._layout_data()
            self.save_results_btn.setEnabled(True)
            self.output_box.setPlainText("\n".join(lines))
            self.footer_status_label.setText("Zone evaluation complete" if report["complete"] else "Zone evaluation needs review — Configure Protection Zones")
        except Exception as error:
            self._invalidate_results()
            self.output_box.setPlainText(f"Evaluation failed:\n{error}")
            self.footer_status_label.setText("Zone evaluation failed; see analysis output")
        self.response_time_label.setText(f"Response time: {(time.perf_counter() - started) * 1000:.0f} ms")

    def _on_save_results_clicked(self):
        if self.last_result is not None and self._layout_data() != self._evaluated_snapshot:
            self._invalidate_results()
        if self.last_result is None or self.last_netlist is None:
            QMessageBox.information(
                self, "Save Calculation Results", "Run an evaluation first."
            )
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Calculation Results",
            "calculation_results.pdf",
            "PDF Files (*.pdf)",
        )

        if not file_path:
            return

        if not file_path.lower().endswith(".pdf"):
            file_path += ".pdf"

        result = self.last_result
        peak = result.get("peak_current_A", "Not available")
        peak_display = f"{peak:g} A" if isinstance(peak, (int, float)) else str(peak)
        study_status = "COMPLETE" if result.get("protection_analysis", {}).get("complete") else "INCOMPLETE — provisional study"

        loads = result.get("loads") or []
        load_rows = "".join(
            "<tr>"
            f"<td>{escape(str(load.get('name', 'Load')))}</td>"
            f"<td>{escape(str(load.get('total_amps', 'N/A')))} A</td>"
            f"<td>{escape(str(load.get('note', '')))}</td>"
            "</tr>"
            for load in loads
        ) or "<tr><td colspan='3'>No loads returned.</td></tr>"

        steps = result.get("calculation_steps") or []

        if isinstance(steps, str):
            steps = [steps]

        steps_html = (
            "".join(
                f"<li>{escape(str(step))}</li>"
                for step in steps
            )
            if steps
            else "<li>The evaluator did not return individual calculation steps.</li>"
        )

        output_html = escape(self.output_box.toPlainText())

        raw_data = escape(
            json.dumps(
                {
                    "netlist": self.last_netlist,
                    "result": result,
                },
                indent=2,
                default=str,
            )
        )

        html = f"""
        <html>
        <head>
          <style>
            body {{ font-family: sans-serif; font-size: 10pt; }}
            h1, h2 {{ color: #006A4E; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{
                border: 1px solid #cbd2d9;
                padding: 6px;
                text-align: left;
            }}
            pre {{ white-space: pre-wrap; }}
          </style>
        </head>

        <body>
          <h1>Calculation Results</h1>
          <p><b>{escape(study_status)}</b></p>

          <p>
            <b>System:</b>
            {escape(str(result.get('scenario_name', 'N/A')))}
          </p>

          <p>
            <b>Voltage:</b>
            {escape(str(result.get('voltage_V', 'N/A')))} V
          </p>

          <p>
            <b>Highest peak on one DC supply:</b>
            {escape(peak_display)}
            ({escape(str(result.get('worst_case_supply') or 'not available'))})
          </p>

          <h2>Loads</h2>

          <table>
            <tr>
                <th>Load</th>
                <th>Total current</th>
                <th>Note</th>
            </tr>

            {load_rows}
          </table>

          <h2>Calculation Steps</h2>

          <ol>
            {steps_html}
          </ol>

          <h2>Evaluation Output</h2>

          <pre>{output_html}</pre>

          <h2>Netlist and Result Data</h2>

          <pre>{raw_data}</pre>
        </body>
        </html>
        """

        document = QTextDocument()
        document.setHtml(html)

        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=Path(file_path).parent, suffix=".pdf", delete=False) as stream:
                temporary = stream.name
            printer = QPrinter()
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(temporary)
            document.print_(printer)
            if printer.printerState() == QPrinter.PrinterState.Error or Path(temporary).stat().st_size == 0:
                raise OSError("The PDF file was not created or is empty.")
            os.replace(temporary, file_path)
        except Exception as error:
            QMessageBox.critical(
                self, "Save Failed", f"Could not create the PDF:\n{error}"
            )
            return
        finally:
            if temporary and Path(temporary).exists():
                Path(temporary).unlink()

        self.footer_status_label.setText(
            f"Results saved: {file_path}"
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

        data = dialog.component_data()
        name = data["name"]
        symbol_key = str(data.get("symbol_type", "custom"))
        if not name:
            QMessageBox.warning(self, "Create Component", "Please enter a component name.")
            return

        # Bus only sends Rating; other types send coil / motor currents.
        # Use .get() so neither branch KeyErrors on the other's fields.
        properties = {
            "rating_kv": data.get("Rating", 0),
            "trip_coil_1_a": data.get("TripCoil1", 0),
            "trip_coil_2_a": data.get("TripCoil2", 0),
            "close_coil_a": data.get("CloseCoil", 0),
            "motor_inrush_a": data.get("MotorInrushCurrent", 0),
            "motor_run_a": data.get("MotorRunCurrent", 0),
            "symbol_type": symbol_key,
        }
        if not properties["rating_kv"]:
            properties.pop("rating_kv")

        slug = "".join(ch if ch.isalnum() else "_" for ch in name.strip().lower())
        equip_id = f"custom_{slug}" if slug else f"custom_{len(EQUIPMENT_DEFS)}"
        base_id = equip_id
        suffix = 1
        while equip_id in EQUIPMENT_DEFS:
            suffix += 1
            equip_id = f"{base_id}_{suffix}"

        EQUIPMENT_DEFS[equip_id] = {
            "label": name,
            "definition": {"symbol": symbol_key, "name": name, "properties": properties},
            "factory": functools.partial(
                make_user_equipment,
                symbol_key,
                name,
                properties,
            ),
        }

        item = QListWidgetItem(name)
        item.setData(Qt.ItemDataRole.UserRole, equip_id)
        self.equipment_list.addItem(item)
        self._mark_modified()

        glyph = next(
            (label for key, label in SYMBOL_TYPE_CHOICES if key == symbol_key),
            symbol_key,
        )
        if hasattr(self, "footer_status_label"):
            self.footer_status_label.setText(
                f"Added '{name}' ({glyph}) to the Equipment Library"
            )

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
                page_rect = page.rect
                # PDF coordinates are points (72 points per inch). Converting
                # them to the grid's scene scale makes a 48 × 36 inch AEP
                # sheet exactly 2400 × 1800 scene units.
                scene_width = (
                    page_rect.width / 72.0 * SCENE_UNITS_PER_INCH
                )
                scene_height = (
                    page_rect.height / 72.0 * SCENE_UNITS_PER_INCH
                )
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
        self.pdf_path = str(Path(pdf_path).resolve())
        if hasattr(self, "workspace_view"):
            self.workspace_view.set_background_pixmap(
                pm,
                scene_width=scene_width,
                scene_height=scene_height,
            )
        if hasattr(self, "pdf_show_cb"):
            self.pdf_show_cb.setEnabled(True)
            self.pdf_show_cb.setChecked(True)
            self.pdf_show_cb.setToolTip("Show or hide the imported PDF background")
        if hasattr(self, "footer_status_label"):
            sheet_width = scene_width / SCENE_UNITS_PER_INCH
            sheet_height = scene_height / SCENE_UNITS_PER_INCH
            self.footer_status_label.setText(
                f"Imported: {pdf_path} "
                f"({sheet_width:g} × {sheet_height:g} in)"
            )
        self._mark_modified()

    def _on_selection_changed(self):
        """Update the properties panel when the user selects a symbol or wire."""
        if not hasattr(self, "workspace_scene"):
            return
        items = self.workspace_scene.selectedItems()
        breaker = items[0] if len(items) == 1 and isinstance(items[0], OneLineSymbolItem) and items[0].equip_type == "breaker" else None
        for widget in self._breaker_widgets:
            widget.setVisible(breaker is not None)
        if breaker is not None:
            settings = breaker_settings({"id": breaker.instance_id, "properties": self._symbol_properties(breaker)}, self.protection_zones)
            for key, field in self.breaker_fields.items():
                field.blockSignals(True)
                if isinstance(field, QComboBox):
                    field.setCurrentIndex(field.findData(settings[key]))
                elif isinstance(field, QSpinBox):
                    field.setValue(settings[key])
                else:
                    field.setText(settings[key])
                field.blockSignals(False)
        if len(items) != 1:
            self.prop_type.setText("—")
            self.prop_name.setReadOnly(True)
            self.prop_name.setText("—")
            self.prop_status.setEnabled(False)
            self._set_bus_property_mode(False)
            self._clear_custom_component_properties()
            return

        item = items[0]
        if isinstance(item, OneLineSymbolItem):
            self.prop_name.setReadOnly(False)
            self.prop_name.blockSignals(True)
            self.prop_name.setText(item.visible_id())
            self.prop_name.blockSignals(False)
        if isinstance(item, BusItem):
            self.prop_type.setText(
                BUILTIN_SYMBOLS.get(item.equip_type, {}).get("label", "Bus")
            )
            self.prop_status.setEnabled(False)
            self._set_bus_property_mode(True)
            props = getattr(item, "properties", None)
            rating = props.get("rating_kv") if isinstance(props, dict) else None
            self.prop_voltage.blockSignals(True)
            self.prop_voltage.setText("" if rating is None else f"{rating:g}")
            self.prop_voltage.blockSignals(False)
        elif isinstance(item, OneLineSymbolItem):
            self.prop_type.setText(item.label)
            self.prop_status.setEnabled(True)
            self._set_bus_property_mode(False)
            self._set_custom_component_properties(self._symbol_properties(item))
        elif isinstance(item, ConnectionItem):
            self.prop_type.setText("Connection")
            self.prop_name.setReadOnly(True)
            self.prop_status.setEnabled(False)
            self.prop_name.setText(
                f"{item.from_item.visible_id()}:{item.from_port} → "
                f"{item.to_item.visible_id()}:{item.to_port}"
            )
            self._set_bus_property_mode(False)
            self._clear_custom_component_properties()
        else:
            self.prop_type.setText(type(item).__name__)
            self.prop_name.setReadOnly(True)
            self.prop_status.setEnabled(False)
            self._set_bus_property_mode(False)
            self._clear_custom_component_properties()

    def _on_property_id_changed(self, text: str):
        """The id typed in the properties panel is drawn beside the symbol."""
        if not hasattr(self, "workspace_scene"):
            return
        selected = self.workspace_scene.selectedItems()
        if len(selected) != 1 or not isinstance(selected[0], OneLineSymbolItem):
            return
        new_id = text.strip()
        if not new_id or new_id == "—":
            self._on_selection_changed()
            return
        symbol = selected[0]
        if new_id == symbol.visible_id():
            return
        self._edit_property(symbol, "display_id", new_id)

    def _on_breaker_setting_changed(self, key):
        symbol = self._selected_editable_symbol()
        if symbol is None or symbol.equip_type != "breaker":
            return
        field = self.breaker_fields[key]
        value = field.currentData() if isinstance(field, QComboBox) else field.value() if isinstance(field, QSpinBox) else field.text().strip()
        if value is None or value == "":
            self.footer_status_label.setText("Enter a DC supply name for this breaker load")
            self._on_selection_changed()
            return
        settings = breaker_settings({"id": symbol.instance_id, "properties": self._symbol_properties(symbol)}, self.protection_zones)
        settings[key] = value
        # Materialize compatible legacy defaults together, making this equipment
        # authoritative for every zone. One Undo restores the entire edit.
        changes = [(name, val) for name, val in settings.items()
                   if val != "" and symbol.properties.get(name) != val]
        if changes:
            self.undo_stack.beginMacro(f"Edit {symbol.visible_id()} breaker settings")
            for name, val in changes:
                self.undo_stack.push(EditPropertyCommand(symbol, name, val, self._on_selection_changed))
            self.undo_stack.endMacro()

    def _edit_property(self, symbol, key, value):
        old = symbol.display_id if key == "display_id" else getattr(symbol, "properties", {}).get(key)
        if old != value:
            self.undo_stack.push(EditPropertyCommand(symbol, key, value, self._on_selection_changed))
        else:
            self._on_selection_changed()

    def _symbol_properties(self, symbol: OneLineSymbolItem) -> dict:
        props = getattr(symbol, "properties", None)
        if not isinstance(props, dict):
            props = {}
            symbol.properties = props
        return props

    def _selected_editable_symbol(self) -> OneLineSymbolItem | None:
        if not hasattr(self, "workspace_scene"):
            return None
        selected = self.workspace_scene.selectedItems()
        if len(selected) != 1 or not isinstance(selected[0], OneLineSymbolItem):
            return None
        if isinstance(selected[0], BusItem):
            return None
        return selected[0]

    def _on_property_status_changed(self, text: str):
        """Store the status chosen for the selected component."""
        symbol = self._selected_editable_symbol()
        if symbol is None or text in ("", "—"):
            return
        self._edit_property(symbol, "status", text)

    def _on_numeric_property_changed(self, key: str, text: str):
        """Store a typed amp value on the selected component."""
        symbol = self._selected_editable_symbol()
        if symbol is None:
            return
        raw = text.strip()
        try:
            value = float(raw) if raw else 0.0
            if not math.isfinite(value) or value < 0:
                raise ValueError
        except ValueError:
            self.footer_status_label.setText("Current must be a finite number greater than or equal to zero")
            self._on_selection_changed()
            return
        self._edit_property(symbol, key, value)

    def _on_property_voltage_changed(self, text: str):
        """Store a typed bus voltage and refresh the rating drawn on the bar."""
        if not hasattr(self, "workspace_scene"):
            return
        selected = self.workspace_scene.selectedItems()
        if len(selected) != 1 or not isinstance(selected[0], BusItem):
            return
        bus = selected[0]
        raw = text.strip().lower().removesuffix("kv").strip()
        if not raw:
            rating = None
        else:
            try:
                rating = float(raw)
                if not math.isfinite(rating) or rating <= 0:
                    raise ValueError
            except ValueError:
                self.footer_status_label.setText("Bus voltage must be a finite positive number, or blank")
                self._on_selection_changed()
                return
        self._edit_property(bus, "rating_kv", rating)

    def _set_bus_property_mode(self, is_bus: bool):
        """A bus shows voltage only. Other selections keep the coil fields."""
        for widget in self._non_bus_property_widgets:
            widget.setVisible(not is_bus)
        self.prop_voltage_label.setVisible(is_bus)
        self.prop_voltage.setVisible(is_bus)
        if not is_bus:
            self.prop_voltage.blockSignals(True)
            self.prop_voltage.setText("—")
            self.prop_voltage.blockSignals(False)

    def _set_custom_component_properties(self, properties: dict):
        """Fill status and the current rows from the selected component."""
        self._enable_component_property_fields()
        status = str(properties.get("status", "Closed"))
        fields = (
            (self.prop_trip_coil_1, properties.get("trip_coil_1_a", 0)),
            (self.prop_trip_coil_2, properties.get("trip_coil_2_a", 0)),
            (self.prop_close_coil, properties.get("close_coil_a", 0)),
            (self.prop_motor_inrush, properties.get("motor_inrush_a", 0)),
            (self.prop_motor_run, properties.get("motor_run_a", 0)),
        )
        self.prop_status.blockSignals(True)
        index = self.prop_status.findText(status)
        self.prop_status.setCurrentIndex(index if index >= 0 else 0)
        self.prop_status.blockSignals(False)
        for field, value in fields:
            field.blockSignals(True)
            field.setText(f"{value:g}" if isinstance(value, (int, float)) else str(value))
            field.blockSignals(False)

    def _clear_custom_component_properties(self):
        """Reset the Trip Coil / Motor Current rows for non-component selections."""
        self.prop_status.blockSignals(True)
        self.prop_status.setCurrentIndex(0)
        self.prop_status.blockSignals(False)
        for field in (
            self.prop_trip_coil_1,
            self.prop_trip_coil_2,
            self.prop_close_coil,
            self.prop_motor_inrush,
            self.prop_motor_run,
        ):
            field.blockSignals(True)
            field.setReadOnly(True)
            field.setText("—")
            field.blockSignals(False)

    def _enable_component_property_fields(self):
        for field in (
            self.prop_trip_coil_1,
            self.prop_trip_coil_2,
            self.prop_close_coil,
            self.prop_motor_inrush,
            self.prop_motor_run,
        ):
            field.setReadOnly(False)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Open the substation design window."""
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 11))

    window = SubstationGuiMockup()
    window.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
