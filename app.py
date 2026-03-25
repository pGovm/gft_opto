import sys
from textwrap import dedent

from PySide6.QtCore import QDir, QLibraryInfo, QLocale, QTranslator, Qt, Slot
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import (QApplication, QColorDialog, QCheckBox, QDialog,
                               QErrorMessage, QFontDialog, QFileDialog, QFrame,
                               QGridLayout, QGroupBox, QInputDialog, QLabel,
                               QLineEdit, QMessageBox, QPushButton,
                               QSizePolicy, QSpacerItem, QToolBox,
                               QVBoxLayout, QWidget)

class Dialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.label = QLabel()
        self.label.setFrameStyle(QFrame.Shadow.Raised | QFrame.Shape.Panel)

        
        self.button = QPushButton("Click to Edit Button")
        self.button.clicked.connect(self.set_name)

        vertLayout = QVBoxLayout()
        toolbox = QToolBox()

        vertLayout.addWidget(toolbox)

        page = QWidget()
        layout = QGridLayout(page)
        layout.addWidget(self.button, 0, 0)
        layout.addWidget(self.label, 0, 1)
        toolbox.addItem(page, "Input Dialogs")

        self.setLayout(layout)

    @Slot()
    def set_name(self):
        name = ("Inductor", "Resistor", "Breaker", "Autotransformer")

        name, ok = QInputDialog.getItem(self, "Component Widget Name", "Component:", name, 0, False)

        if name and ok:
            self.label.setText(name)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    dialog = Dialog()
    availableGeometry = dialog.screen().availableGeometry()
    dialog.resize(availableGeometry.width() / 3, availableGeometry.height() * 2 / 3)
    dialog.move((availableGeometry.width() - dialog.width()) / 2,
                (availableGeometry.height() - dialog.height()) / 2)
    dialog.show()
    app.exec()