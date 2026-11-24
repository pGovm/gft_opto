# Provides the Create Component dialog for defining equipment symbols and electrical properties.

"""
Create-Component dialog.

Used by substation_gui.py when the user clicks "Create Component".
Symbol type keys must stay in sync with EQUIPMENT_DEFS there.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QAbstractSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
)



# ---------------------------------------------------------------------------
# Symbol types offered in the Create Component dialog
# (keys match BUILTIN_SYMBOLS in substation_gui.py)
# ---------------------------------------------------------------------------

SYMBOL_TYPE_CHOICES: list[tuple[str, str]] = [
    ("breaker", "Circuit Breaker"),
    ("incoming_line", "Incoming AC Line"),
    ("disconnect", "Disconnect Switch"),
    ("mos", "Motor Operated Switch (MOS)"),
    ("ct", "Current Transformer (CT/BCT)"),
    ("pt", "Potential Transformer (PT/VT)"),
    ("line_trap", "Line Trap"),
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
        """Show electrical inputs supported by the selected symbol."""
        symbol = self.symbol_type_key()
        self.form.setRowVisible(self.rating_spin, symbol == "bus")
        for field_widget in (self.trip_coil_1_spin, self.trip_coil_2_spin, self.close_coil_spin):
            self.form.setRowVisible(field_widget, symbol in {"breaker", "custom"})
        for field_widget in (self.motor_inrush_spin, self.motor_run_spin):
            self.form.setRowVisible(field_widget, symbol in {"breaker", "mos", "custom", "xfmr_2w", "xfmr_3w", "pt"})
        self.adjustSize()

    @staticmethod
    def _add_row(form: QFormLayout, text: str, field_widget):
        form.addRow(QLabel(text), field_widget)

    @staticmethod
    def _make_spin(default: float = 0):
        spin = QDoubleSpinBox()
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
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
        symbol = self.symbol_type_key()
        if symbol == "bus":
            data["Rating"] = self.rating_spin.value()
        if symbol in {"breaker", "custom"}:
            data.update({
                "TripCoil1": self.trip_coil_1_spin.value(),
                "TripCoil2": self.trip_coil_2_spin.value(),
                "CloseCoil": self.close_coil_spin.value(),
            })
        if symbol in {"breaker", "mos", "custom", "xfmr_2w", "xfmr_3w", "pt"}:
            data.update({
                "MotorInrushCurrent": self.motor_inrush_spin.value(),
                "MotorRunCurrent": self.motor_run_spin.value(),
            })
        return data
