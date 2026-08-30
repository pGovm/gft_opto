import sys
import json
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton, QDialog, QDialogButtonBox,
    QFormLayout, QDoubleSpinBox, QComboBox, QLabel,
)
from PySide6.QtGui import QPainter, QColor, QPen, QFont
from PySide6.QtCore import Qt, QRect, Slot


class ComponentDialog(QDialog):
    """Popup for entering a component's name and electrical properties."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Component Widget")

        form = QFormLayout(self)

        # Preset list of common component types; editable so a custom name
        # can still be typed if needed.
        self.name_combo = QComboBox()
        self.name_combo.setEditable(True)
        self.name_combo.addItems(
            ["Inductor", "Resistor", "Breaker", "Current Transformer", "Feeder"]
        )
        self.name_combo.setCurrentIndex(-1)
        line_edit = self.name_combo.lineEdit()
        if line_edit is not None:
            line_edit.setPlaceholderText("e.g. Inductor, Breaker, Feeder")

        self._add_row(form, "Component Name:", self.name_combo)

        self.rating_spin = self._make_spin(default=36)
        self._add_row(form, "Rating (kV):", self.rating_spin)

        self.trip_coil_1_spin = self._make_spin()
        self._add_row(form, "Trip Coil 1 (A):", self.trip_coil_1_spin)

        self.trip_coil_2_spin = self._make_spin()
        self._add_row(form, "Trip Coil 2 (A):", self.trip_coil_2_spin)

        self.close_coil_spin = self._make_spin()
        self._add_row(form, "Close Coil (A):", self.close_coil_spin)

        self.motor_inrush_spin = self._make_spin()
        self._add_row(form, "Motor Inrush Current (A):", self.motor_inrush_spin)

        self.motor_run_spin = self._make_spin()
        self._add_row(form, "Motor Run Current (A):", self.motor_run_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    @staticmethod
    def _add_row(form: QFormLayout, text: str, field_widget):
        # Explicit label color so it stays readable regardless of the
        # active Qt style/theme (labels were rendering near-invisible
        # against a dark background before this fix).
        label = QLabel(text)
        label.setStyleSheet("color: #e5e7eb;")
        form.addRow(label, field_widget)

    @staticmethod
    def _make_spin(default=0):
        spin = QDoubleSpinBox()
        spin.setRange(0, 100000)
        spin.setDecimals(2)
        spin.setValue(default)
        return spin

    def component_name(self) -> str:
        return self.name_combo.currentText().strip()

    def component_data(self) -> dict:
        return {
            "name": self.component_name(),
            "Rating": self.rating_spin.value(),
            "TripCoil1": self.trip_coil_1_spin.value(),
            "TripCoil2": self.trip_coil_2_spin.value(),
            "CloseCoil": self.close_coil_spin.value(),
            "MotorInrushCurrent": self.motor_inrush_spin.value(),
            "MotorRunCurrent": self.motor_run_spin.value(),
            "tags": ["Manufacturer", "Year"],
        }


class ComponentWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Set a fixed size for our LED
        self.setFixedSize(400, 400)
        # Default state is Off (False)
        self._state = False
        self._color_on = QColor(0, 255, 0)  # Green when on
        self._color_off = QColor(100, 100, 100)  # Grey when off
        self._component_data = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)  # For smooth edges

        painter.setFont(QFont("Sans-Serif", 20))

        # Define the area to draw in (a circle)
        rect = QRect(30, 30, 350, 350)

        # Set the brush colour based on the state
        if self._state and self._component_data:
            painter.drawText(
                rect, Qt.AlignmentFlag.AlignCenter,
                f"Component: {self._component_data['name']}\n\n\n"
                f"Rating: {self._component_data['Rating']} kV",
            )
        else:
            painter.setBrush(self._color_off)

        # Draw the ellipse (circle) with a black border
        painter.setPen(QPen(Qt.GlobalColor.black, 1))
        painter.drawRect(rect)

    @Slot()
    def choose(self):
        dialog = ComponentDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        name = dialog.component_name()
        if not name:
            return

        self._component_data = dialog.component_data()
        self._state = not self._state
        self.export()
        self.update()

    def export(self):
        if not self._component_data:
            return
        with open("widget_library.json", "w") as f:
            json.dump(self._component_data, f)


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Custom Component Widget Demo")
        layout = QVBoxLayout()

        # Create our custom LED widget
        self.comp = ComponentWidget()
        layout.addWidget(self.comp, alignment=Qt.AlignmentFlag.AlignCenter)

        # Create a button to toggle the LED
        self.editButton = QPushButton("Choose a Component")
        self.editButton.clicked.connect(self.comp.choose)
        layout.addWidget(self.editButton)

        self.setLayout(layout)