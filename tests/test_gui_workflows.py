"""Headless regression tests for layout and calculation workflows.

Run with QT_QPA_PLATFORM=offscreen and python -m unittest discover -s tests.
"""

import functools
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PySide6.QtCore import QPointF
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QMessageBox

from gft_opto.gui.commands import AddEquipmentCommand, AddConnectionCommand, SplitWireCommand
from gft_opto.gui.layout_io import build_scene, scene_data
from gft_opto.gui.substation_gui4 import SubstationGuiMockup
from gft_opto.gui.symbols import (
    BusItem, CircuitBreakerItem, ConnectionItem, EQUIPMENT_DEFS, OneLineSymbolItem,
    make_user_equipment, CurrentTransformerItem, GroundItem,
)
from gft_opto.logic.protection_study import topology_signature
from gft_opto.gui.zone_dialog import ZoneDialog


class GuiWorkflows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # The Windows offscreen plugin has no system fonts unless registered.
        if not QFontDatabase.families():
            font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf"
            if font.exists():
                QFontDatabase.addApplicationFont(str(font))

    def setUp(self):
        EQUIPMENT_DEFS.clear()
        self.window = SubstationGuiMockup()
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.window._saved_snapshot = self.window._layout_data()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def add_breaker(self):
        item = CircuitBreakerItem()
        self.window.undo_stack.push(AddEquipmentCommand(self.window.workspace_scene, item))
        item.setSelected(True)
        return item

    def commit_current(self, text):
        field = self.window.prop_trip_coil_1
        field.setText(text)
        field.editingFinished.emit()

    def assign_test_zone(self, breaker):
        w = self.window
        bus, ct, ground = BusItem(), CurrentTransformerItem(), GroundItem()
        for item in (bus, ct, ground):
            w.workspace_scene.addItem(item)
        for a, ap, b, bp in ((bus, "right", ct, "left"), (ct, "right", breaker, "left"), (breaker, "right", ground, "node")):
            w.workspace_scene.addItem(ConnectionItem(a, ap, b, bp))
        w.protection_zones = [{"id": "bus_zone", "name": "Test Bus Zone", "kind": "Bus",
            "seed": bus.instance_id, "boundary_cts": [ct.instance_id], "confirmed": True,
            "topology_signature": topology_signature(w._protection_netlist()),
            "actions": [{"component": breaker.instance_id, "load": "trip_coil_1_a", "stage": "Primary trip", "supply": "DC 1", "units": 1}]}]

    def test_live_evaluation_and_stale_results(self):
        w = self.window
        w._on_run_evaluation_clicked()
        self.assertIsNone(w.last_result)
        self.assertIn("No components", w.output_box.toPlainText())
        item = self.add_breaker()
        self.commit_current("12")
        self.assign_test_zone(item)
        w._on_run_evaluation_clicked()
        self.assertEqual(w.last_result["peak_current_A"], 12)
        self.assertIn(item.instance_id, [c["id"] for c in w.last_netlist["components"]])
        self.assertTrue(w.save_results_btn.isEnabled())
        self.commit_current("15")
        self.assertIsNone(w.last_result)
        self.assertFalse(w.save_results_btn.isEnabled())

    def test_property_validation_and_undo(self):
        w = self.window
        item = self.add_breaker()
        self.commit_current("12")
        self.commit_current("")
        self.assertEqual(item.properties["trip_coil_1_a"], 0)
        w.undo_stack.undo()
        self.assertEqual(item.properties["trip_coil_1_a"], 12)
        self.assertEqual(w.prop_trip_coil_1.text(), "12")
        w.undo_stack.redo()
        self.assertEqual(w.prop_trip_coil_1.text(), "0")
        for value in ("nan", "inf", "-1", "oops"):
            self.commit_current(value)
            self.assertEqual(item.properties["trip_coil_1_a"], 0)
            self.assertEqual(w.prop_trip_coil_1.text(), "0")
        w.prop_name.setText("CB-1")
        w.prop_name.editingFinished.emit()
        self.assertEqual(item.visible_id(), "CB-1")
        w.undo_stack.undo()
        self.assertEqual(item.visible_id(), item.instance_id)
        w.prop_status.setCurrentText("Open")
        self.assertEqual(item.properties["status"], "Open")
        w.undo_stack.undo()
        self.assertNotIn("status", item.properties)

    def test_round_trip_bus_branches_library_and_ids(self):
        w = self.window
        definition = {"symbol": "breaker", "name": "Custom Breaker",
                      "properties": {"symbol_type": "breaker", "trip_coil_1_a": 9}}
        EQUIPMENT_DEFS["custom_breaker"] = {"label": definition["name"], "definition": definition,
            "factory": functools.partial(make_user_equipment, "breaker", definition["name"], definition["properties"])}
        breaker = self.add_breaker()
        self.commit_current("12")
        breaker.set_rotation_deg(90)
        breaker.set_scale_factor(1.5)
        bus = BusItem()
        bus.set_end_base_x("left", -200)
        tap, _ = bus.add_tap(20)
        bus.properties["rating_kv"] = 36
        w.workspace_scene.addItem(bus)
        bus.setPos(500, 500)
        wire = ConnectionItem(breaker, "right", bus, tap)
        w.workspace_scene.addItem(wire)
        w.undo_stack.push(SplitWireCommand(w.workspace_scene, wire, QPointF(300, 300)))
        w.grid_opacity_slider.setValue(60)
        w.project_box.setText("Regression Project")
        path = Path(self.temp.name) / "layout.json"
        before = w._layout_data()
        self.assertTrue(w.save_layout(path))
        w.undo_stack.clear()
        self.assertTrue(w.open_layout(str(path)))
        self.assertEqual(w._layout_data(), before)
        self.assertEqual(w.grid_opacity_slider.value(), 60)
        self.assertEqual(w.workspace_view._grid_item.opacity(), 0.6)
        self.assertEqual(w.equipment_list.count(), 1)
        ids = {i.instance_id for i in w.workspace_scene.items() if isinstance(i, OneLineSymbolItem)}
        self.assertNotIn(CircuitBreakerItem().instance_id, ids)
        restored_bus = next(i for i in w.workspace_scene.items() if isinstance(i, BusItem))
        restored_bus.setSelected(True)
        w.prop_voltage.setText("69")
        w.prop_voltage.editingFinished.emit()
        self.assertEqual(restored_bus.properties["rating_kv"], 69)
        w.undo_stack.undo()
        self.assertEqual(restored_bus.properties["rating_kv"], 36)

    def test_invalid_open_preserves_current_scene(self):
        w = self.window
        self.add_breaker()
        original = w.workspace_scene
        data = w._layout_data()
        data["connections"] = [["missing", "left", "missing", "right"]]
        path = Path(self.temp.name) / "invalid.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with patch.object(QMessageBox, "critical") as error:
            self.assertFalse(w.open_layout(str(path)))
            error.assert_called_once()
        self.assertIs(w.workspace_scene, original)

    def test_pdf_export_and_failure_feedback(self):
        w = self.window
        breaker = self.add_breaker()
        self.commit_current("18")
        self.assign_test_zone(breaker)
        w._on_run_evaluation_clicked()
        path = Path(self.temp.name) / "results.pdf"
        with patch("gft_opto.gui.substation_gui4.QFileDialog.getSaveFileName", return_value=(str(path), "")):
            w._on_save_results_clicked()
        self.assertGreater(path.stat().st_size, 0)
        with pymupdf.open(path) as document:
            text = "".join(page.get_text() for page in document)
        self.assertIn("18", text)
        self.assertIn("Results saved:", w.footer_status_label.text())
        bad_path = Path(self.temp.name) / "missing" / "results.pdf"
        with patch("gft_opto.gui.substation_gui4.QFileDialog.getSaveFileName", return_value=(str(bad_path), "")), patch.object(QMessageBox, "critical") as error:
            w._on_save_results_clicked()
            error.assert_called_once()

    def test_multi_selection_and_initial_grid(self):
        w = self.window
        self.assertEqual(w.grid_opacity_slider.value(), 25)
        self.assertEqual(w.workspace_view._grid_item.opacity(), 0.25)
        a = self.add_breaker()
        b = self.add_breaker()
        a.setSelected(True)
        b.setSelected(True)
        self.assertTrue(w.prop_name.isReadOnly())
        self.assertTrue(w.prop_trip_coil_1.isReadOnly())

    def test_header_controls_fit_and_open_layout_button_works(self):
        w = self.window
        w.resize(1280, 800)
        w.show()
        self.app.processEvents()
        self.assertLessEqual(w.minimumSizeHint().width(), 1280)
        for control in (w.new_layout_btn, w.open_layout_btn, w.load_btn, w.run_btn, w.save_results_btn):
            self.assertTrue(control.isVisible())
            origin = control.mapTo(w, QPointF(0, 0).toPoint())
            self.assertGreaterEqual(origin.x(), 0)
            self.assertLessEqual(origin.x() + control.width(), w.width())
        self.assertIsNone(w.menuWidget())
        self.assertTrue(w.save_as_btn.isVisible())
        self.assertTrue(any(action.shortcut().toString() == "Ctrl+O" for action in w.actions()))
        with patch("gft_opto.gui.substation_gui4.QFileDialog.getOpenFileName", return_value=("", "")) as dialog:
            w.open_layout_btn.click()
            dialog.assert_called_once()

    def test_pdf_background_round_trip(self):
        w = self.window
        pdf_path = Path(self.temp.name) / "background.pdf"
        with pymupdf.open() as document:
            document.new_page(width=720, height=360)
            document.save(pdf_path)
        with patch("gft_opto.gui.substation_gui4.QFileDialog.getOpenFileName", return_value=(str(pdf_path), "")):
            w.on_import_pdf_clicked()
        w.pdf_show_cb.setChecked(False)
        w.grid_snap_cb.setChecked(False)
        path = Path(self.temp.name) / "layout.json"
        self.assertTrue(w.save_layout(path))
        self.assertTrue(w.open_layout(str(path)))
        self.assertEqual(w.workspace_scene.sceneRect().width(), 500)
        self.assertTrue(w.workspace_view.has_pdf_background())
        self.assertFalse(w.workspace_view._background_item.isVisible())
        self.assertFalse(w.workspace_view._snap_to_grid)
        self.assertEqual(w.pdf_path, str(pdf_path.resolve()))

    def test_unsaved_cancel_save_and_new(self):
        w = self.window
        self.add_breaker()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            self.assertFalse(w._confirm_discard())
        path = Path(self.temp.name) / "save.json"
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Save), patch("gft_opto.gui.substation_gui4.QFileDialog.getSaveFileName", return_value=(str(path), "")):
            self.assertTrue(w._confirm_discard())
        w.new_layout()
        self.assertEqual(scene_data(w.workspace_scene)["components"], [])

    def test_zone_dialog_edits_highlights_and_persistence(self):
        w = self.window
        breaker = self.add_breaker()
        self.commit_current("12")
        self.assign_test_zone(breaker)
        dialog = ZoneDialog(w._protection_netlist(), w.protection_zones, w)
        self.assertTrue(dialog.confirmed.isChecked())
        dialog.actions.cellWidget(0, 4).setValue(3)
        self.assertFalse(dialog.confirmed.isChecked())
        dialog.confirmed.setChecked(True)
        dialog.accept()
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)
        w.protection_zones = dialog.zones
        w._on_run_evaluation_clicked()
        self.assertEqual(w.last_result["peak_current_A"], 36)
        self.assertGreater(len(w._zone_overlays), 0)
        # Configured events and the new display-only topology catalog coexist.
        self.assertEqual(w.zone_view.count(), 2 + len(w._zone_report["zones"])
                         + len(w._zone_report["topology_regions"]))
        self.assertEqual(w.zone_view.itemData(1), w.protection_zones[0]["id"])
        # View-only highlights must not become saved electrical components.
        self.assertEqual(len(scene_data(w.workspace_scene)["components"]), 4)
        path = Path(self.temp.name) / "zones.json"
        self.assertTrue(w.save_layout(path))
        self.assertTrue(w.open_layout(str(path)))
        self.assertEqual(w.protection_zones[0]["actions"][0]["units"], 3)
        w._on_run_evaluation_clicked()
        self.assertEqual(w.last_result["peak_current_A"], 36)
        restored = next(i for i in w.workspace_scene.items() if isinstance(i, CircuitBreakerItem))
        restored.setSelected(True)
        w.prop_status.setCurrentText("Open")
        self.assertFalse(w._zone_overlays)
        self.assertIsNone(w.last_result)
        dialog = ZoneDialog(w._protection_netlist(), w.protection_zones, w)
        self.assertFalse(dialog.confirmed.isChecked())
        dialog.reject()

    def test_detected_zones_require_confirmation(self):
        w = self.window
        breaker = self.add_breaker()
        self.commit_current("10")
        self.assign_test_zone(breaker)
        w.protection_zones = []
        w._on_run_evaluation_clicked()
        self.assertEqual(len(w.protection_zones), 1)
        self.assertFalse(w.protection_zones[0]["confirmed"])
        self.assertIn("INCOMPLETE", w.output_box.toPlainText())
        self.assertEqual(w.last_result["protection_analysis"]["supply_peaks"], {})
        self.assertGreater(len(w._zone_overlays), 0)

    def test_dialog_cancel_and_stage_assignment(self):
        w = self.window
        breaker = self.add_breaker()
        self.assign_test_zone(breaker)
        before = w._layout_data()
        dialog = ZoneDialog(w._protection_netlist(), w.protection_zones, w)
        dialog.add_action({"component": breaker.instance_id, "load": "close_coil_a",
                           "stage": "Reclose", "supply": "DC 2", "units": 1})
        dialog.capture()
        self.assertEqual(dialog.zones[0]["actions"][1]["stage"], "Reclose")
        dialog.reject()
        self.assertEqual(before, w._layout_data())


if __name__ == "__main__":
    unittest.main()
