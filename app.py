import sys
from tutorial import Dialog
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
        painter.setRenderHint(QPainter.Antialiasing)  # For smooth edges

        painter.setFont(QFont("Sans-Serif", 20))

        # Define the area to draw in (a circle)
        rect = QRect(30, 30, 350, 350)

        # Set the brush colour based on the state
        if self._state:
            painter.drawText(rect, Qt.AlignCenter, f"Component: {self._component_name}\n\n\n")
            painter.drawText(rect, Qt.AlignCenter, f"Power Rating: {self._component_rating} kVA")
        else:
            painter.setBrush(self._color_off)

        # Draw the ellipse (circle) with a black border
        painter.setPen(QPen(Qt.black, 4))
        painter.drawRect(rect)

    @Slot()
    def choose(self):
        components = ("Inductor", "Resistor", "Breaker", "Current Transformer", "Feeder")
        self._component_name, ok = QInputDialog.getItem(self, "Component Widget Name", "", components, 0, False)
        self._component_rating, ok = QInputDialog.getDouble(self, "Component Widget Value", "", 65, 0, 1000, 10)
        if self._component_name and ok and self._component_rating:
            self._state = not self._state
            self.update()

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Custom Component Widget Demo")
        layout = QVBoxLayout()

        # Create our custom LED widget
        self.comp = ComponentWidget()
        layout.addWidget(self.comp, alignment=Qt.AlignCenter)

        # Create a button to toggle the LED
        self.editButton = QPushButton("Choose a Component")
        self.editButton.clicked.connect(self.comp.choose)
        layout.addWidget(self.editButton)

        self.setLayout(layout)

# if __name__ == "__main__":
#     app = QApplication(sys.argv)
#     window = MainWindow()
#     window.show()
#     sys.exit(app.exec())

# class Dialog(QDialog):
#     def __init__(self, parent=None):
#         super().__init__(parent)
#         frameStyle = QFrame.Shadow.Raised | QFrame.Shape.Panel

#         self._component_label = QLabel()
#         self._component_label.setFrameStyle(frameStyle)
#         self._component_button = QPushButton("Click to Edit Button")
#         self._component_button.clicked.connect(self.set_name)

#         # self._value_label = QLabel()
#         # self._value_label.setFrameStyle(frameStyle)
#         # self._value_button = QPushButton()
#         # self._value_button.clicked.connect(self.set_value)

#         vertLayout = QVBoxLayout()
#         toolbox = QToolBox()

#         vertLayout.addWidget(toolbox)

#         page = QWidget()
#         layout = QGridLayout(page)
#         layout.addWidget(self._component_button, 0, 0)
#         layout.addWidget(self._component_label, 0, 1)
#         # layout.addWidget(self._value_button, 1, 0)
#         # layout.addWidget(self._value_label, 1, 1)
#         toolbox.addItem(page, "Input Dialogs")

#         self.setLayout(layout)

#     @Slot()
#     def set_name(self):
#         _component_names = ("Inductor", "Resistor", "Breaker", "Autotransformer")

#         name, ok = QInputDialog.getItem(self, "Component Widget Name", "Component:", _component_names, 0, False)
#         val, ok = QInputDialog.getDouble(self, "Component Widget Value", "Value:", 65, 0, 1000, 10)

#         if name and val and ok:
#             self._component_label.setText(f'{name}')
#             match(name):
#                 case "Inductor":
#                     unit = "H"
#                 case "Resistor":
#                     unit = "Ohm"
#                 case "Breaker":
#                     unit = "W"
#                 case "Autotransformer":
#                     unit = "MW"

#             self._component_label.setText(f'{val} {unit}')