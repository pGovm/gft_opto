import sys
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication, QComboBox, QFrame, QGridLayout, QGroupBox, 
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, 
    QMainWindow, QPushButton, QSlider, QStatusBar, QTextEdit, 
    QToolButton, QVBoxLayout, QWidget,
)

# Main Window Class defining the GUI structure
class SubstationGuiMockup(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI-Assisted Substation Design Tool")
        self.setMinimumSize(1400, 800)

        # Global Stylesheet: Defines colors, borders, and padding for all widgets (CSS-like)
        self.setStyleSheet("""
            QMainWindow { background-color: #f4f6f8; }
            QLabel { color: #1f2933; font-size: 11pt; }
            QGroupBox { 
                background: white; border: 1px solid #d9e2ec; 
                border-radius: 12px; margin-top: 20px; 
                font-weight: bold; font-size: 12pt; padding-top: 20px; 
            }
            QGroupBox::title { subcontrol-origin: margin; left: 16px; padding: 0 8px; }
            QPushButton { 
                background-color: #2f6fed; color: white; border: none; 
                border-radius: 8px; padding: 8px 16px; font-weight: 600; 
            }
            QToolButton { 
                background-color: #2f6fed; color: white; border-radius: 8px; 
                padding: 6px 12px; min-width: 80px; 
            }
            QPushButton:hover, QToolButton:hover { background-color: #2559bd; }
            QLineEdit, QTextEdit, QComboBox, QListWidget { 
                background: white; border: 1px solid #cbd2d9; 
                border-radius: 8px; padding: 8px; 
            }
            QFrame#canvasFrame { background: white; border: 2px dashed #9fb3c8; border-radius: 14px; }
            QFrame#toolbarFrame { background: white; border: 1px solid #d9e2ec; border-radius: 12px; }
        """)

        self._build_ui()

    # Main UI assembler
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central) # Vertical layout for Header -> Content -> Status
        main_layout.setContentsMargins(22, 22, 22, 22)
        main_layout.setSpacing(18)

        # Create the high-level sections
        header = self._build_header()
        content = self._build_content()

        main_layout.addWidget(header)
        main_layout.addLayout(content)

        # Status Bar at the bottom
        status = QStatusBar()
        status.showMessage("Ready")
        self.setStatusBar(status)

    # Top Bar: Contains title, project selection, and main action buttons
    def _build_header(self):
        frame = QFrame()
        frame.setObjectName("toolbarFrame")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(20, 14, 20, 14)

        title = QLabel("AI-Assisted Substation Protection & Control Design")
        title.setFont(QFont("Arial", 22, QFont.Bold))

        project_box = QComboBox()
        project_box.addItems(["Demo Project - One Line A", "Breaker-and-a-Half Yard", "Ring Bus Example"])
        project_box.setMinimumWidth(320)

        save_btn = QPushButton("Save Layout")
        load_btn = QPushButton("Load Diagram")
        run_btn = QPushButton("Run Evaluation")

        layout.addWidget(title)
        layout.addStretch() # Pushes the following widgets to the right
        layout.addWidget(QLabel("Project:"))
        layout.addWidget(project_box)
        layout.addWidget(load_btn)
        layout.addWidget(save_btn)
        layout.addWidget(run_btn)

        return frame

    # Middle Content: Split into Left, Center, and Right panels
    def _build_content(self):
        layout = QHBoxLayout()
        layout.setSpacing(18)

        left_panel = self._build_left_panel()
        center_panel = self._build_center_panel()
        right_panel = self._build_right_panel()

        # Add panels with "stretch factors" to control relative widths (2:5:3 ratio)
        layout.addWidget(left_panel, 2)
        layout.addWidget(center_panel, 5)
        layout.addWidget(right_panel, 3)

        return layout

    # Left Panel: Equipment list and Quick Actions
    def _build_left_panel(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(16)
        layout.setContentsMargins(0, 0, 0, 0)

        # Equipment Palette section
        palette_group = QGroupBox("Equipment Palette")
        palette_layout = QVBoxLayout(palette_group)

        search = QLineEdit()
        search.setPlaceholderText("Search equipment...")
        palette_layout.addWidget(search)

        equipment_list = QListWidget()
        for item in ["Bus", "Breaker", "Disconnect Switch", "CT", "PT/VT", "Transformer", "Relay", "Battery Bank", "Feeder", "Line Segment"]:
            QListWidgetItem(item, equipment_list)
        palette_layout.addWidget(equipment_list)

        # Quick Actions placeholder
        self.controls_group = QGroupBox("Quick Actions")
        self.controls_layout = QVBoxLayout(self.controls_group)
        
        layout.addWidget(palette_group, 3)
        layout.addWidget(self.controls_group, 1)

        return container

    # Center Panel: The main drawing/workspace area
    def _build_center_panel(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(16)
        layout.setContentsMargins(0, 0, 0, 0)

        # The "Canvas" where the diagram would be drawn
        canvas = QFrame()
        canvas.setObjectName("canvasFrame")
        canvas_layout = QVBoxLayout(canvas)
        
        canvas_layout.addStretch(1) # Center content vertically

        canvas_title = QLabel("One-Line Diagram Workspace")
        canvas_title.setFont(QFont("Arial", 22, QFont.Bold))
        canvas_title.setAlignment(Qt.AlignCenter)
        
        canvas_hint = QLabel("Drag components from the palette to build the model\nor import an image for AI-assistance")
        canvas_hint.setAlignment(Qt.AlignCenter)
        canvas_hint.setStyleSheet("color: #52606d; font-size: 13pt; margin-top: 10px;")

        canvas_layout.addWidget(canvas_title)
        canvas_layout.addWidget(canvas_hint)
        
        canvas_layout.addStretch(1)

        layout.addWidget(canvas, 1)
        return container

    # Right Panel: Properties, Analysis Output, and Notes
    def _build_right_panel(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(16)
        layout.setContentsMargins(0, 0, 0, 0)

        # Form-style layout for element properties
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

        # Read-only text box for AI/Analysis results
        analysis_group = QGroupBox("Analysis Output")
        analysis_layout = QVBoxLayout(analysis_group)
        self.output_box = QTextEdit()
        self.output_box.setPlaceholderText("Analysis results will appear here...")
        self.output_box.setReadOnly(True) 
        analysis_layout.addWidget(self.output_box)

        # Editable text box for user notes
        notes_group = QGroupBox("Engineer Notes")
        notes_layout = QVBoxLayout(notes_group)
        notes = QTextEdit()
        notes.setPlaceholderText("Add design notes here...")
        notes_layout.addWidget(notes)

        layout.addWidget(properties_group, 1)
        layout.addWidget(analysis_group, 2)
        layout.addWidget(notes_group, 2)

        return container

# Application Entry Point
if __name__ == "__main__":
    # Ensure UI looks sharp on high-resolution screens
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 11))
    
    window = SubstationGuiMockup()
    window.showMaximized()
    sys.exit(app.exec_())