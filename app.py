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
        frameStyle = QFrame.Shadow.Raised | QFrame.Shape.Panel

        self._component_label = QLabel()
        self._component_label.setFrameStyle(frameStyle)
        self._component_button = QPushButton("Click to Edit Button")
        self._component_button.clicked.connect(self.set_name)

        self._value_label = QLabel()
        self._value_label.setFrameStyle(frameStyle)
        self._value_button = QPushButton()
        self._value_button.clicked.connect(self.set_value)

        vertLayout = QVBoxLayout()
        toolbox = QToolBox()

        vertLayout.addWidget(toolbox)

        page = QWidget()
        layout = QGridLayout(page)
        layout.addWidget(self._component_button, 0, 0)
        layout.addWidget(self._component_label, 0, 1)
        layout.addWidget(self._value_button, 1, 0)
        layout.addWidget(self._value_label, 1, 1)
        toolbox.addItem(page, "Input Dialogs")

        self.setLayout(layout)

    @Slot()
    def set_name(self):
        _component_name = ("Inductor", "Resistor", "Breaker", "Autotransformer")

        name, ok = QInputDialog.getItem(self, "Component Widget Name", "Component:", _component_name, 0, False)

        if name and ok:
            self._component_label.setText(f'{name}')
            self.name = name

    @Slot()
    def set_value(self):
        val, ok = QInputDialog.getDouble(self, "Component Widget Value", "Value:", 65, 0, 10000, 10)

        if val and ok:
            match(self.name):
                case "Inductor":
                    unit = "H"
                case "Resistor":
                    unit = "Ohm"
                case "Breaker":
                    unit = "W"
                case "Autotransformer":
                    unit = "MW"

            self._value_label.setText(f'{val} {unit}')

if __name__ == '__main__':
    app = QApplication(sys.argv)
    dialog = Dialog()
    availableGeometry = dialog.screen().availableGeometry()
    dialog.resize(availableGeometry.width() / 3, availableGeometry.height() * 2 / 3)
    dialog.move((availableGeometry.width() - dialog.width()) / 2,
                (availableGeometry.height() - dialog.height()) / 2)
    dialog.show()
    sys.exit(app.exec())