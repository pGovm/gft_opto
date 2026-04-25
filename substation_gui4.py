import sys
from PySide6.QtCore import Qt, QEvent, QPoint
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QPushButton, QSlider, QStatusBar, QTextEdit,
    QToolButton, QVBoxLayout, QWidget, QFileDialog, QScrollArea,
)

import fitz

class SubstationGuiMockup(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI-Assisted Substation Design Tool")
        self.setMinimumSize(1400, 800)

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

    QLineEdit, QTextEdit, QComboBox, QListWidget { 
        background: white; border: 1px solid #cbd2d9; 
        border-radius: 8px; padding: 8px; color: #1f2933;
    }
    QFrame#canvasFrame { background: white; border: 2px dashed #9fb3c8; border-radius: 14px; }
    QFrame#toolbarFrame { background: white; border: 1px solid #d9e2ec; border-radius: 12px; }
""")

        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(22, 22, 22, 22)
        main_layout.setSpacing(18)

        header = self._build_header()
        content = self._build_content()

        main_layout.addWidget(header)
        main_layout.addLayout(content)

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

        main_layout.addWidget(footer)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_pixmap_to_viewport()

    def eventFilter(self, obj, event):
        if hasattr(self, "workspace_scroll") and obj is self.workspace_scroll.viewport():
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._panning = True
                self._pan_start = event.pos()
                self.workspace_scroll.viewport().setCursor(Qt.ClosedHandCursor)
                return True

            if event.type() == QEvent.MouseMove and getattr(self, "_panning", False):
                delta = event.pos() - self._pan_start
                h = self.workspace_scroll.horizontalScrollBar()
                v = self.workspace_scroll.verticalScrollBar()
                h.setValue(h.value() - delta.x())
                v.setValue(v.value() - delta.y())
                self._pan_start = event.pos()
                return True

            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self._panning = False
                self.workspace_scroll.viewport().setCursor(Qt.ArrowCursor)
                return True

            if event.type() == QEvent.Wheel:
                modifiers = event.modifiers()
                if modifiers & Qt.ControlModifier:
                    steps = event.angleDelta().y() / 120.0
                    if steps != 0:
                        factor = 1.15 ** steps
                        self._zoom = max(0.1, min(10.0, self._zoom * factor))
                        self._update_workspace_pixmap()
                    return True

        return super().eventFilter(obj, event)

    def _build_header(self):
        frame = QFrame()
        frame.setObjectName("toolbarFrame")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(20, 14, 20, 14)

        title = QLabel("AI-Assisted Substation Protection & Control Design")
        title.setFont(QFont("Arial", 12, QFont.Bold))

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

        left_panel = self._build_left_panel()
        center_panel = self._build_center_panel()
        right_panel = self._build_right_panel()

        layout.addWidget(left_panel, 2)
        layout.addWidget(center_panel, 5)
        layout.addWidget(right_panel, 3)

        return layout

    def _build_left_panel(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(16)
        layout.setContentsMargins(0, 0, 0, 0)

        palette_group = QGroupBox("Equipment Library")
        palette_layout = QVBoxLayout(palette_group)

        search = QLineEdit()
        search.setPlaceholderText("Search equipment...")
        palette_layout.addWidget(search)

        equipment_list = QListWidget()
        for item in [""]:
            QListWidgetItem(item, equipment_list)
        palette_layout.addWidget(equipment_list)

        self.controls_group = QGroupBox("Quick Actions")
        self.controls_layout = QVBoxLayout(self.controls_group)
        
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

        self.workspace_scroll = QScrollArea()
        self.workspace_scroll.setFrameShape(QFrame.NoFrame)
        self.workspace_scroll.setWidgetResizable(False)
        self.workspace_scroll.setAlignment(Qt.AlignCenter)
        self.workspace_scroll.setStyleSheet("QScrollArea { background: white; }")

        self.workspace_image = QLabel()
        self.workspace_image.setAlignment(Qt.AlignCenter)
        self.workspace_image.setMinimumSize(1, 1)
        self.workspace_image.setStyleSheet("background: white;")
        self.workspace_scroll.setWidget(self.workspace_image)
        self.workspace_scroll.viewport().setStyleSheet("background: white;")

        self._workspace_original = None
        self._fit_scale = 1.0
        self._zoom = 1.0
        self._panning = False
        self._pan_start = QPoint()
        self.workspace_scroll.viewport().installEventFilter(self)

        canvas_layout.addWidget(self.workspace_scroll, 1)

        self.workspace_hint = QLabel("Import PDF to view Diagram.\nHold Ctrl to zoom.")
        self.workspace_hint.setAlignment(Qt.AlignCenter)
        self.workspace_hint.setStyleSheet("color: #52606d; font-size: 13pt; margin-top: 10px;")
        canvas_layout.addWidget(self.workspace_hint)

        layout.addWidget(canvas, 1)
        return container

    def on_import_pdf_clicked(self):
        pdf_path, _ = QFileDialog.getOpenFileName(self, "Select PDF", "", "PDF Files (*.pdf)")
        if not pdf_path:
            return

        try:
            doc = fitz.open(pdf_path)
            try:
                page = doc.load_page(0)
                zoom = 2.0
                mat = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                png_bytes = pix.tobytes("png")
            finally:
                doc.close()
        except Exception as e:
            if hasattr(self, "footer_status_label"):
                self.footer_status_label.setText(f"Import failed: {e}")
            return

        pm = QPixmap()
        if not pm.loadFromData(png_bytes, "PNG"):
            if hasattr(self, "footer_status_label"):
                self.footer_status_label.setText("Import failed: could not decode rendered PNG")
            return

        self._workspace_original = pm
        self._zoom = 1.0
        if hasattr(self, "workspace_hint"):
            self.workspace_hint.hide()
        self._fit_pixmap_to_viewport()
        if hasattr(self, "footer_status_label"):
            self.footer_status_label.setText(f"Imported: {pdf_path}")

    def _fit_pixmap_to_viewport(self):
        if not getattr(self, "_workspace_original", None):
            return
        if not hasattr(self, "workspace_scroll") or not hasattr(self, "workspace_image"):
            return

        viewport_size = self.workspace_scroll.viewport().size()
        if viewport_size.width() <= 0 or viewport_size.height() <= 0:
            return

        ow = self._workspace_original.width()
        oh = self._workspace_original.height()
        if ow <= 0 or oh <= 0:
            return

        self._fit_scale = min(viewport_size.width() / ow, viewport_size.height() / oh)
        if self._fit_scale <= 0:
            self._fit_scale = 1.0

        self._update_workspace_pixmap()

    def _update_workspace_pixmap(self):
        if not getattr(self, "_workspace_original", None):
            return
        if not hasattr(self, "workspace_image") or not hasattr(self, "workspace_scroll"):
            return

        scale = max(0.01, self._fit_scale * self._zoom)
        target_w = max(1, int(self._workspace_original.width() * scale))
        target_h = max(1, int(self._workspace_original.height() * scale))

        scaled = self._workspace_original.scaled(
            target_w,
            target_h,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.workspace_image.setPixmap(scaled)
        self.workspace_image.resize(scaled.size())

    def _build_right_panel(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(16)
        layout.setContentsMargins(0, 0, 0, 0)

        properties_group = QGroupBox("Selected Element Properties")
        properties_layout = QGridLayout(properties_group)
        properties_layout.setHorizontalSpacing(16)
        properties_layout.setVerticalSpacing(12)
        
        properties_layout.addWidget(QLabel("Type:"), 0, 0)
        properties_layout.addWidget(QLineEdit("Breaker"), 0, 1)
        properties_layout.addWidget(QLabel("Name:"), 1, 0)
        properties_layout.addWidget(QLineEdit("BRK-102"), 1, 1)
        properties_layout.addWidget(QLabel("Voltage Level:"), 2, 0)
        
        volt_cb = QComboBox()
        volt_cb.addItems(["230 kV", "115 kV", "34.5 kV"])
        properties_layout.addWidget(volt_cb, 2, 1)
        
        properties_layout.addWidget(QLabel("Status:"), 3, 0)
        status_cb = QComboBox()
        status_cb.addItems(["Closed", "Open", "Maintenance"])
        properties_layout.addWidget(status_cb, 3, 1)

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

        return container

if __name__ == "__main__":
    
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 11))
    
    window = SubstationGuiMockup()
    window.showMaximized()
    sys.exit(app.exec()) 
