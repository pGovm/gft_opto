"""Tabbed command bar for the main window's existing actions and view controls."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QStyle, QTabWidget,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)


def _edit_icon(command):
    """Small vector-drawn icons for commands without a native Qt equivalent."""
    pixmap = QPixmap(48, 48)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#006a4e"), 1.6))
    if command == "copy":
        painter.drawRoundedRect(4, 3, 12, 15, 1, 1)
        painter.fillRect(8, 7, 12, 15, QColor("white"))
        painter.drawRoundedRect(8, 7, 12, 15, 1, 1)
    elif command == "paste":
        painter.drawRoundedRect(5, 4, 14, 18, 1, 1)
        painter.fillRect(9, 2, 6, 4, QColor("white"))
        painter.drawRoundedRect(9, 2, 6, 4, 1, 1)
        painter.drawLine(9, 11, 16, 11)
        painter.drawLine(9, 15, 16, 15)
    else:
        painter.drawRect(7, 6, 10, 12)
        painter.drawLine(2, 12, 7, 12)
        painter.drawLine(17, 12, 22, 12)
        painter.drawLine(12, 1, 12, 6)
        painter.drawLine(12, 18, 12, 23)
    painter.end()
    return QIcon(pixmap)


class RibbonBar(QTabWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("commandRibbon")
        self.setDocumentMode(True)
        # Windows can paint a native dark document-tab base beyond the last tab.
        # The ribbon owns that background instead of delegating it to the OS.
        self.tabBar().setDrawBase(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("""
            QTabWidget#commandRibbon { background-color: #ffffff; }
            QTabWidget#commandRibbon::pane {
                background: #ffffff; border: 1px solid #d9e2ec;
                border-top: 2px solid #006a4e; border-radius: 4px;
            }
            QTabBar { background-color: #ffffff; border: none; }
            QTabBar::tab {
                background: #ffffff; color: #334155; padding: 5px 18px;
                font-size: 10pt; font-weight: 600; border: none;
            }
            QTabBar::tab:selected { background: #006a4e; color: white; }
            QTabBar::tab:hover:!selected { background: #e6f2ed; color: #006a4e; }
            QWidget#ribbonPage { background-color: #ffffff; color: #1f2933; }
            QFrame#ribbonGroup { background: #ffffff; border: none; border-right: 1px solid #e4e7eb; }
            QLabel { color: #1f2933; background: transparent; font-size: 10pt; font-weight: normal; }
            QLabel#ribbonCaption { color: #52606d; font-size: 9pt; }
            QToolButton {
                background: transparent; color: #1f2933; border: 1px solid transparent;
                border-radius: 5px; padding: 4px 8px; font-size: 10pt; font-weight: normal;
            }
            QToolButton:hover { background: #e6f2ed; border-color: #b5d4c7; }
            QToolButton:pressed { background: #cee6db; }
            QToolButton:disabled { color: #64748b; }
            QToolButton:focus { border-color: #006a4e; }
            QCheckBox { color: #1f2933; background: transparent; font-size: 10pt; font-weight: normal; }
            QCheckBox:disabled { color: #64748b; }
        """)

    def add_page(self, title):
        page = QWidget()
        page.setObjectName("ribbonPage")
        row = QHBoxLayout(page)
        row.setContentsMargins(8, 4, 8, 3)
        row.setSpacing(8)
        row.addStretch(1)
        self.addTab(page, title)
        return row


class RibbonGroup(QFrame):
    def __init__(self, row, title):
        super().__init__()
        self.setObjectName("ribbonGroup")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 0, 10, 0)
        layout.setSpacing(2)
        self.controls = QGridLayout()
        self.controls.setContentsMargins(0, 0, 0, 0)
        self.controls.setHorizontalSpacing(4)
        self.controls.setVerticalSpacing(4)
        layout.addLayout(self.controls, 1)
        caption = QLabel(title)
        caption.setObjectName("ribbonCaption")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(caption)
        row.insertWidget(row.count() - 1, self)

    def add_action(self, action, column, icon):
        action.setIcon(icon if isinstance(icon, QIcon) else self.style().standardIcon(icon, None, self))
        button = QToolButton()
        button.setDefaultAction(action)
        button.setIconSize(QSize(20, 20))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        # Moving focus commits pending property edits before commands run.
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.setMinimumSize(72, 48)
        self.controls.addWidget(button, 0, column, 2, 1)
        button.ensurePolished()
        button.setMinimumWidth(max(72, button.sizeHint().width()))
        return button


def populate_ribbon(window):
    """Reuse canonical actions; legacy button attributes still name the controls."""
    icon = QStyle.StandardPixmap
    ribbon = window.ribbon

    file = ribbon.add_page("&File")
    layout = RibbonGroup(file, "Layout")
    window.new_layout_btn = layout.add_action(window.new_layout_action, 0, icon.SP_FileIcon)
    window.open_layout_btn = layout.add_action(window.open_layout_action, 1, icon.SP_DirOpenIcon)
    window.save_layout_btn = layout.add_action(window.save_layout_action, 2, icon.SP_DialogSaveButton)
    window.save_as_btn = layout.add_action(window.save_as_action, 3, icon.SP_DialogSaveButton)
    pdf = RibbonGroup(file, "Background")
    window.load_btn = pdf.add_action(window.import_pdf_action, 0, icon.SP_FileDialogContentsView)
    netlist = RibbonGroup(file, "Netlist")
    window.import_netlist_btn = netlist.add_action(window.import_netlist_action, 0, icon.SP_ArrowDown)
    export = RibbonGroup(file, "Export")
    window.save_results_btn = export.add_action(window.export_results_action, 0, icon.SP_DialogSaveButton)
    # One button offers both PDFs; it stays disabled until results exist.
    export_menu = QMenu(window.save_results_btn)
    export_menu.addAction(window.export_results_action)
    export_menu.addAction(window.save_netlist_pdf_action)
    window.save_results_btn.setMenu(export_menu)
    window.save_results_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

    edit = ribbon.add_page("&Edit")
    history = RibbonGroup(edit, "History")
    window.undo_btn = history.add_action(window.undo_action, 0, icon.SP_ArrowBack)
    window.redo_btn = history.add_action(window.redo_action, 1, icon.SP_ArrowForward)
    clipboard = RibbonGroup(edit, "Clipboard")
    window.copy_btn = clipboard.add_action(window.copy_action, 0, _edit_icon("copy"))
    window.paste_btn = clipboard.add_action(window.paste_action, 1, _edit_icon("paste"))
    selection = RibbonGroup(edit, "Selection")
    window.delete_btn = selection.add_action(window.delete_action, 0, icon.SP_TrashIcon)
    window.rotate_btn = selection.add_action(window.rotate_action, 1, icon.SP_BrowserReload)
    equipment = RibbonGroup(edit, "Equipment")
    window.create_component_btn = equipment.add_action(window.create_component_action, 0, _edit_icon("component"))

    view = ribbon.add_page("&View")
    layers = RibbonGroup(view, "Drawing layers")
    layers.controls.setHorizontalSpacing(20)
    for row, col, control in ((0, 0, window.grid_show_cb), (0, 1, window.pdf_show_cb),
                              (1, 0, window.circuit_show_cb), (1, 1, window.zones_show_cb)):
        layers.controls.addWidget(control, row, col)
    grid = RibbonGroup(view, "Grid")
    grid.controls.addWidget(window.grid_snap_cb, 0, 0)
    grid.controls.addWidget(window.grid_opacity_controls, 1, 0)

    analysis = ribbon.add_page("&Analysis")
    validation = RibbonGroup(analysis, "Connectivity")
    window.check_btn = validation.add_action(window.validate_action, 0, icon.SP_DialogApplyButton)
    study = RibbonGroup(analysis, "Protection study")
    window.zone_setup_btn = study.add_action(window.configure_zones_action, 0, icon.SP_FileDialogListView)
    window.run_btn = study.add_action(window.evaluate_action, 1, icon.SP_MediaPlay)
    results = RibbonGroup(analysis, "Results")
    window.expand_analysis_btn = results.add_action(window.expand_analysis_action, 0, icon.SP_TitleBarMaxButton)
    # Give space to complete command labels before shrinking the wrapped title.
    ribbon.setMinimumWidth(ribbon.minimumSizeHint().width())
