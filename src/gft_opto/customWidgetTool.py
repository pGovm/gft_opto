"""
Create-Component dialog and a small standalone demo widget.

Used by substation_gui4.py when the user clicks "Create Component".
Symbol type keys must stay in sync with EQUIPMENT_DEFS there.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QRect, Slot
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


# ---------------------------------------------------------------------------
# Symbol types offered in the Create Component dialog
# (keys match EQUIPMENT_DEFS in substation_gui4.py; "bus" uses the custom box)
# ---------------------------------------------------------------------------

SYMBOL_TYPE_CHOICES: list[tuple[str, str]] = [
    ("breaker", "Circuit Breaker"),
    ("disconnect", "Disconnect Switch"),
    ("mos", "Motor Operated Switch (MOS)"),
    ("ct", "Current Transformer (CT/BCT)"),
    ("pt", "Potential Transformer (PT/VT)"),
    ("xfmr_2w", "Transformer (2-winding)"),
    ("xfmr_3w", "Transformer (3-winding)"),
    ("ground", "Ground"),
    ("bus", "Bus"),
    ("custom", "Custom (labeled box)"),
]


# ---------------------------------------------------------------------------
# Create Component dialog
# ---------------------------------------------------------------------------

class ComponentDialog(QDialog):
    """Choose a one-line symbol type and enter electrical properties."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Component")
        self.setMinimumWidth(420)
        self._apply_stylesheet()
        self._build_form()

    # --- UI setup -----------------------------------------------------------

    def _apply_stylesheet(self):
        # Local styles so OS dark mode / parent window CSS can't hide text.
        self.setStyleSheet("""
            QDialog {
                background-color: #ffffff;
                color: #1f2933;
            }
            QLabel {
                color: #1f2933;
                background: transparent;
                font-size: 11pt;
            }
            QLineEdit, QComboBox, QDoubleSpinBox {
                background: #ffffff;
                color: #1f2933;
                border: 1px solid #cbd2d9;
                border-radius: 6px;
                padding: 6px 8px;
                font-size: 11pt;
                min-height: 24px;
            }
            QComboBox QAbstractItemView {
                background: #ffffff;
                color: #1f2933;
                selection-background-color: #006A4E;
                selection-color: #ffffff;
            }
            QPushButton {
                background-color: #006A4E;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: 600;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #085c44;
            }
            QDialogButtonBox QPushButton {
                min-width: 88px;
            }
        """)

    def _build_form(self):
        form = QFormLayout(self)
        form.setContentsMargins(16, 16, 16, 16)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        self.form = form

        # Symbol glyph to place on the canvas
        self.symbol_combo = QComboBox()
        for key, label in SYMBOL_TYPE_CHOICES:
            self.symbol_combo.addItem(label, key)
        self._add_row(form, "Symbol Type:", self.symbol_combo)

        # Optional display name (defaults to the symbol type label)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Optional display name (defaults to symbol type)")
        self._add_row(form, "Component Name:", self.name_edit)

        # Electrical properties shown in the main window properties panel
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

        # Bus only needs voltage rating; hide coil / motor rows for it.
        self._non_bus_fields = [
            self.trip_coil_1_spin,
            self.trip_coil_2_spin,
            self.close_coil_spin,
            self.motor_inrush_spin,
            self.motor_run_spin,
        ]

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self.symbol_combo.currentIndexChanged.connect(self._on_symbol_changed)
        self.name_edit.textChanged.connect(self._update_fields_for_component)
        self._on_symbol_changed(self.symbol_combo.currentIndex())

    def _is_bus(self) -> bool:
        if self.symbol_type_key() == "bus":
            return True
        return self.component_name().strip().lower() == "bus"

    def _on_symbol_changed(self, _index: int):
        """Keep the name field in sync when it still matches a preset label."""
        preset_names = {label for _, label in SYMBOL_TYPE_CHOICES}
        current = self.name_edit.text().strip()
        if not current or current in preset_names:
            self.name_edit.setText(self.symbol_combo.currentText())
        self._update_fields_for_component()

    def _update_fields_for_component(self, _text: str = ""):
        """Bus shows rating only; other types show coil / motor fields."""
        is_bus = self._is_bus()
        self.form.setRowVisible(self.rating_spin, is_bus)
        for field_widget in self._non_bus_fields:
            self.form.setRowVisible(field_widget, not is_bus)
        self.adjustSize()

    @staticmethod
    def _add_row(form: QFormLayout, text: str, field_widget):
        form.addRow(QLabel(text), field_widget)

    @staticmethod
    def _make_spin(default: float = 0):
        spin = QDoubleSpinBox()
        spin.setRange(0, 100000)
        spin.setDecimals(2)
        spin.setValue(default)
        return spin

    # --- Results for the main window ----------------------------------------

    def symbol_type_key(self) -> str:
        key = self.symbol_combo.currentData()
        return str(key) if key is not None else "custom"

    def component_name(self) -> str:
        name = self.name_edit.text().strip()
        return name or self.symbol_combo.currentText().strip()

    def component_data(self) -> dict:
        """Payload consumed by SubstationGuiMockup._on_create_component_clicked."""
        data: dict[str, str | float | list[str]] = {
            "name": self.component_name(),
            "symbol_type": self.symbol_type_key(),
            "tags": ["Manufacturer", "Year"],
        }
        if self._is_bus():
            data["Rating"] = self.rating_spin.value()
        else:
            data.update({
                "TripCoil1": self.trip_coil_1_spin.value(),
                "TripCoil2": self.trip_coil_2_spin.value(),
                "CloseCoil": self.close_coil_spin.value(),
                "MotorInrushCurrent": self.motor_inrush_spin.value(),
                "MotorRunCurrent": self.motor_run_spin.value(),
            })
        return data


# ---------------------------------------------------------------------------
# Standalone demo widget (optional — run this file directly)
# ---------------------------------------------------------------------------

class ComponentWidget(QWidget):
    """Tiny preview widget used only when launching this module alone."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(400, 400)
        self._state = False
        self._color_on = QColor(0, 255, 0)
        self._color_off = QColor(100, 100, 100)
        self._component_data = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont("Sans-Serif", 20))
        rect = QRect(30, 30, 350, 350)

        if self._state and self._component_data:
            name_line = f"Component: {self._component_data['name']}"
            if "Rating" in self._component_data:
                detail_line = f"Rating: {self._component_data['Rating']} kV"
            else:
                detail_line = (
                    f"Trip Coil 1: {self._component_data['TripCoil1']} A"
                )
            painter.drawText(
                rect,
                Qt.AlignmentFlag.AlignCenter,
                f"{name_line}\n\n\n{detail_line}",
            )
        else:
            painter.setBrush(self._color_off)
            painter.setPen(QPen(QColor(0, 0, 0), 2))
            painter.drawEllipse(rect)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle()

    def toggle(self):
        self._state = not self._state
        self.update()

    @Slot()
    def open_dialog(self):
        dialog = ComponentDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._component_data = dialog.component_data()
            self._state = True
            self.update()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = QWidget()
    window.setWindowTitle("Create Component demo")
    layout = QVBoxLayout(window)
    preview = ComponentWidget()
    configure_btn = QPushButton("Configure")
    configure_btn.clicked.connect(preview.open_dialog)
    layout.addWidget(preview)
    layout.addWidget(configure_btn)
    window.show()
    sys.exit(app.exec())
