# Checks that the properties panel only offers status and current fields that
# are kept and used, so nothing typed there is silently lost on save.

"""Junction and ground selections offer no status or current fields."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from gft_opto.gui.commands import AddEquipmentCommand
from gft_opto.gui.symbols import BUILTIN_SYMBOLS, CircuitBreakerItem, GroundItem, JunctionItem

APP = QApplication.instance() or QApplication([])
LAYOUTS = Path(__file__).parent / "layouts"


class PropertyPanelFieldTests(unittest.TestCase):
    def setUp(self):
        from gft_opto.gui.substation_gui import SubstationGuiMockup

        self.window = SubstationGuiMockup()
        self.window.show()
        APP.processEvents()
        w = self.window
        self.fields = {
            "status": w.prop_status, "trip_coil_1_a": w.prop_trip_coil_1,
            "trip_coil_2_a": w.prop_trip_coil_2, "close_coil_a": w.prop_close_coil,
            "motor_inrush_a": w.prop_motor_inrush, "motor_run_a": w.prop_motor_run,
        }

    def tearDown(self):
        self.window._saved_snapshot = self.window._layout_data()
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()

    def select(self, item):
        if item.scene() is None:
            self.window.undo_stack.push(AddEquipmentCommand(self.window.workspace_scene, item))
        self.window.workspace_scene.clearSelection()
        item.setSelected(True)
        APP.processEvents()
        return [key for key, field in self.fields.items() if field.isVisible()]

    def test_junction_and_ground_show_no_status_or_current_fields(self):
        for item in (JunctionItem(), GroundItem(), BUILTIN_SYMBOLS["incoming_line"]["factory"]()):
            with self.subTest(item=item.equip_type):
                self.assertEqual(self.select(item), [])
                self.assertTrue(self.window.prop_name.isVisible())

    def test_other_equipment_keeps_its_fields(self):
        self.assertEqual(self.select(CircuitBreakerItem()), list(self.fields))
        transformer = BUILTIN_SYMBOLS["xfmr_2w"]["factory"]()
        self.assertEqual(self.select(transformer), ["motor_inrush_a", "motor_run_a"])

    def test_hidden_fields_cannot_write_values(self):
        junction = JunctionItem()
        self.select(junction)
        w = self.window
        w._on_numeric_property_changed("trip_coil_1_a", "12")
        w._on_property_status_changed("Open")
        self.assertNotIn("trip_coil_1_a", getattr(junction, "properties", {}) or {})
        self.assertNotIn("status", getattr(junction, "properties", {}) or {})

    def test_current_fields_match_create_component_for_every_type(self):
        from gft_opto.gui.custom_widget_tool import ComponentDialog

        dialog = ComponentDialog(self.window)
        spins = {"trip_coil_1_a": dialog.trip_coil_1_spin, "trip_coil_2_a": dialog.trip_coil_2_spin,
                 "close_coil_a": dialog.close_coil_spin, "motor_inrush_a": dialog.motor_inrush_spin,
                 "motor_run_a": dialog.motor_run_spin}
        try:
            for key, spec in BUILTIN_SYMBOLS.items():
                index = dialog.symbol_combo.findData(key)
                if index < 0:
                    continue
                dialog.symbol_combo.setCurrentIndex(index)
                offered = [k for k, spin in spins.items() if dialog.form.isRowVisible(spin)]
                shown = [k for k in self.select(spec["factory"]()) if k != "status"]
                with self.subTest(symbol=key):
                    self.assertEqual(shown, offered)
        finally:
            dialog.deleteLater()

    def test_ct_line_trap_and_disconnect_have_no_current_fields(self):
        for key in ("ct", "line_trap", "disconnect"):
            item = BUILTIN_SYMBOLS[key]["factory"]()
            with self.subTest(symbol=key):
                self.assertEqual([k for k in self.select(item) if k != "status"], [])
                self.window._on_numeric_property_changed("trip_coil_1_a", "12")
                self.window._on_numeric_property_changed("motor_run_a", "3")
                stored = getattr(item, "properties", None) or {}
                self.assertNotIn("trip_coil_1_a", stored)
                self.assertNotIn("motor_run_a", stored)
        # A disconnect can still be opened, which changes the zones.
        self.assertIn("status", self.select(BUILTIN_SYMBOLS["disconnect"]["factory"]()))

    def test_switching_from_a_junction_back_to_a_breaker_restores_the_fields(self):
        breaker = CircuitBreakerItem()
        self.select(breaker)
        self.select(JunctionItem())
        self.assertEqual(self.select(breaker), list(self.fields))

    def test_values_typed_in_a_real_layout_survive_save_and_reopen(self):
        """What the panel offers is what the file keeps, for every selectable type."""
        w = self.window
        with patch.object(QMessageBox, "warning"), patch.object(QMessageBox, "critical"), \
                patch.object(QMessageBox, "information"), tempfile.TemporaryDirectory() as folder:
            # This layout has both junctions and grounds, the types that lost values.
            self.assertTrue(w.open_layout(str(LAYOUTS / "mvec_reference_one_line_draft.json")))
            types = {getattr(i, "equip_type", None) for i in w.workspace_scene.items()}
            self.assertTrue({"junction", "ground", "breaker"} <= types)
            typed = {}
            for item in list(w.workspace_scene.items()):
                if not hasattr(item, "equip_type") or item.equip_type == "bus":
                    continue
                offered = self.select(item)
                if "trip_coil_1_a" in offered:
                    w.prop_trip_coil_1.setText("7")
                    w.prop_trip_coil_1.editingFinished.emit()
                    typed[item.instance_id] = 7
            self.assertTrue(typed)
            path = Path(folder) / "saved.json"
            self.assertTrue(w.save_layout(str(path)))
            self.assertTrue(w.open_layout(str(path)))
            reopened = {item.instance_id: (getattr(item, "properties", None) or {}).get("trip_coil_1_a")
                        for item in w.workspace_scene.items() if hasattr(item, "instance_id")}
        for ident, value in typed.items():
            self.assertEqual(reopened[ident], value, ident)


if __name__ == "__main__":
    unittest.main()
