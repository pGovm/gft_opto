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
from html import escape

import pymupdf
from PySide6.QtCore import Qt, QByteArray, QMimeData, QRectF
from PySide6.QtGui import (
    QAction,
    QDrag, QFont, QImage, QKeySequence, QPixmap, QTextDocument, QUndoStack,
)
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGraphicsScene,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSlider, QTextEdit,
    QVBoxLayout, QWidget,
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
    CustomComponentItem,
    OneLineSymbolItem,
    make_user_equipment,
)
from gft_opto.gui.workspace import WorkspaceView
from gft_opto.gui.commands import EditPropertyCommand
from gft_opto.gui.layout_io import scene_data, build_scene, reserve_ids, write_layout, validate_properties
from gft_opto.logic.momentary_calc2 import netlist_from_scene, scenario_from_netlist
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

    QLineEdit, QTextEdit, QComboBox, QListWidget {
        background: white; border: 1px solid #cbd2d9;
        border-radius: 8px; padding: 8px; color: #1f2933;
    }
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
            "library": {key: meta["definition"] for key, meta in EQUIPMENT_DEFS.items()},
            "settings": {"grid_visible": self.grid_show_cb.isChecked(),
                         "grid_opacity": self.grid_opacity_slider.value(),
                         "snap": self.grid_snap_cb.isChecked(),
                         "pdf_visible": self.pdf_show_cb.isChecked()},
            **scene_data(self.workspace_scene),
        }

    def _invalidate_results(self):
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
            pixmap = None
            if pdf:
                pdf = str((Path(path).resolve().parent / pdf).resolve())
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
            self._install_layout(data, scene, str(Path(path).resolve()), pixmap)
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
        for key, definition in data["library"].items():
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

    def _on_run_evaluation_clicked(self):
        """Evaluate the currents entered on the actual drawing."""
        system_name = self.project_box.text().strip() or "Untitled Project"

        started = time.perf_counter()
        try:
            netlist = netlist_from_scene(self.workspace_scene, system_name=system_name)
            for component in netlist["components"]:
                validate_properties(component["properties"])
                component["label"] = next(
                    item.visible_id() for item in self.workspace_scene.items()
                    if isinstance(item, OneLineSymbolItem) and item.instance_id == component["id"]
                )
            scenario = scenario_from_netlist(netlist)
            if not scenario.loads:
                self.last_result = self.last_netlist = None
                self.save_results_btn.setEnabled(False)
                self.output_box.setPlainText(
                    "No current loads to evaluate. Place components and enter their coil or motor currents."
                )
                self.footer_status_label.setText("No current loads to evaluate")
                return
            result = scenario.calculate()
            if not math.isfinite(result["peak_current_A"]):
                raise ValueError("The total current is too large. Check the entered loads.")
            result["calculation_steps"] = [
                "Assume all entered coil and motor currents operate simultaneously.",
                "Peak current = sum of all entered loads = "
                + " + ".join(f"{load.total_amps():g}" for load in scenario.loads)
                + f" = {result['peak_current_A']:g} A.",
                "Connectivity and switch status do not select loads; this is not a network simulation or an amp-hour battery sizing calculation.",
            ]
        except Exception as e:
            self.last_result = self.last_netlist = None
            self.save_results_btn.setEnabled(False)
            if hasattr(self, "output_box"):
                self.output_box.setPlainText(f"Evaluation failed:\n{e}")
            if hasattr(self, "footer_status_label"):
                self.footer_status_label.setText(f"Evaluation failed: {e}")
            return
        
        self.last_netlist = netlist
        self.last_result = result
        self._evaluated_snapshot = self._layout_data()
        self.save_results_btn.setEnabled(True)

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if hasattr(self, "response_time_label"):
            self.response_time_label.setText(f"Response time: {elapsed_ms:.0f} ms")

        lines: list[str] = []
        lines.append("Momentary peak current — all entered loads operating simultaneously")
        lines.append("Connections and switch status do not select loads. Battery capacity (Ah) is not calculated.")
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

          <p>
            <b>System:</b>
            {escape(str(result.get('scenario_name', 'N/A')))}
          </p>

          <p>
            <b>Voltage:</b>
            {escape(str(result.get('voltage_V', 'N/A')))} V
          </p>

          <p>
            <b>Peak current:</b>
            {escape(str(result.get('peak_current_A', 'N/A')))} A
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
