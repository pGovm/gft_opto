import sys
import json
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton, QInputDialog
from PySide6.QtGui import QPainter, QColor, QPen, QFont
from PySide6.QtCore import Qt, QRect, Slot

class ComponentWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Set a fixed size for our LED
        self.setFixedSize(400, 400)
        # Default state is Off (False)
        self._state = False
        self._color_on = QColor(0, 255, 0)  # Green when on
        self._color_off = QColor(100, 100, 100)  # Grey when off

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)  # For smooth edges

        painter.setFont(QFont("Sans-Serif", 20))

        # Define the area to draw in (a circle)
        rect = QRect(30, 30, 350, 350)

        # Set the brush colour based on the state
        if self._state:
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"Component: {self._component_name}\n\n\n")
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"Rating: {self._component_rating} kV")
        else:
            painter.setBrush(self._color_off)

        # Draw the ellipse (circle) with a black border
        painter.setPen(QPen(Qt.GlobalColor.black, 1))
        painter.drawRect(rect)

    @Slot()
    def choose(self):
        components = ("Inductor", "Resistor", "Breaker", "Current Transformer", "Feeder")
        self._component_name, ok = QInputDialog.getItem(self, "Component Widget Name", "", components, 0, False)
        self._component_rating, ok = QInputDialog.getDouble(self, "Component Widget Value", "", 36, 0, 50, 10)
        if self._component_name and ok and self._component_rating:
            self._state = not self._state
            self.export()
            self.update()
    
    def export(self):
        component = {
            "name": self._component_name,
            "Rating": self._component_rating,
            "tags": ["Manufacturer", "Year"]
        }

        with open("widet_library.json", "w") as f:
            json.dump(component, f)

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