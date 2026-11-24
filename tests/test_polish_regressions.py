"""Behavioral coverage for load, project, and performance fixes."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QMessageBox

from gft_opto.gui import symbols
from gft_opto.gui.layout_io import build_scene
from gft_opto.gui.substation_gui import SubstationGuiMockup
from gft_opto.gui.symbols import CircuitBreakerItem, OneLineSymbolItem
from gft_opto.gui.zone_dialog import ZoneDialog
from gft_opto.logic.protection_study import evaluate_zones, propose_zones
from test_protection_study import example


class BackgroundLoads(unittest.TestCase):
    def test_background_current_is_added_to_each_stage_on_dc1(self):
        netlist = example()
        zone = propose_zones(netlist)[0]
        zone["actions"] = [
            dict(component="cb1", load="trip_coil_1_a", stage="Primary trip", supply="DC 1", units=1),
            dict(component="cb1", load="close_coil_a", stage="Reclose", supply="DC 1", units=1),
            dict(component="other:1", load="trip_coil_1_a", stage="Miscellaneous load",
                 supply="DC 2", operation="device", custom_load=dict(name="Panel", amps=5)),
        ]
        original = copy.deepcopy(zone)
        stages = evaluate_zones(netlist, [zone])["zones"][0]["scenarios"]
        self.assertEqual([stage["supplies"] for stage in stages], [{"DC 1": 15}, {"DC 1": 17}])
        self.assertEqual(zone, original)
        zone["actions"] = zone["actions"][-1:]
        stages = evaluate_zones(netlist, [zone])["zones"][0]["scenarios"]
        self.assertEqual(stages[0]["stage"], "Background load")
        self.assertEqual(stages[0]["supplies"], {"DC 1": 5})


class PolishGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = SubstationGuiMockup()
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.reject()
            dialog.deleteLater()
        self.window._saved_snapshot = self.window._layout_data()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def editor(self):
        breaker = CircuitBreakerItem()
        breaker.properties = {"trip_coil_1_a": 10, "trip_coil_1_supply": "DC 2"}
        self.window.workspace_scene.addItem(breaker)
        zone = dict(id="z", name="Example", kind="Manual", seed=breaker.instance_id,
                    boundary_cts=[], confirmed=False, actions=[dict(component=breaker.instance_id,
                    load="trip_coil_1_a", stage="Primary trip", operation="all_poles", supply="DC 2")])
        dialog = ZoneDialog(self.window._protection_netlist(), [zone], self.window)
        self.dialogs.append(dialog)
        return breaker, dialog

    def test_other_load_has_visible_current_and_no_inherited_supply(self):
        _, dialog = self.editor()
        dialog.show()
        row = dialog.load_rows[0]
        row.equipment.setCurrentIndex(row.equipment.findText("Other"))
        self.app.processEvents()
        self.assertTrue(row.current.isVisible())
        self.assertFalse(row.stage.isVisible())
        self.assertFalse(row.supply.isVisible())
        row.current.setValue(5)
        dialog.capture()
        saved = dialog.zones[0]["actions"][0]
        self.assertEqual(saved["supply"], "DC 1")
        restored = ZoneDialog(dialog.netlist, json.loads(json.dumps(dialog.zones)))
        self.dialogs.append(restored)
        restored.capture()
        self.assertEqual(restored.zones, dialog.zones)

    def test_blank_name_bulk_verification_reports_error_and_keeps_draft(self):
        _, dialog = self.editor()
        dialog.name.clear()
        with patch.object(QMessageBox, "warning") as warning:
            dialog.verify_all.click()
        warning.assert_called_once()
        self.assertEqual(dialog.zones[0]["name"], "")
        self.assertFalse(dialog.verify_all.isChecked())

    def test_load_table_keeps_four_rows_visible_in_a_short_dialog(self):
        _, dialog = self.editor()
        for _ in range(11):
            dialog.add_action()
        dialog.show()
        dialog.guidance.setText("Review the selected zone's CTs and operating actions. " * 50)
        for height in (650, 750, 900):
            dialog.resize(1050, height)
            self.app.processEvents()
            self.assertGreaterEqual(dialog.actions.viewport().height(),
                                    4 * dialog.actions.rowHeight(0))
        dialog.zone_splitter.setSizes([10000, 1])
        self.app.processEvents()
        self.assertGreaterEqual(dialog.actions.viewport().height(),
                                4 * dialog.actions.rowHeight(0))

    def test_legacy_demo_flags_cannot_bypass_validation(self):
        _, dialog = self.editor()
        dialog.netlist["demonstration_mode"] = True
        dialog.zones[0]["demonstration_complete"] = True
        dialog.actions.setRowCount(0)
        dialog.load_rows.clear()
        dialog.verify_all.click()
        self.assertFalse(dialog.zones[0]["confirmed"])
        report = evaluate_zones(dialog.netlist, dialog.zones)
        self.assertFalse(report["zones"][0]["complete"])
        self.assertNotIn("demonstration_mode", report)
        self.assertNotIn("demonstration_complete", report["zones"][0])

    def test_saved_layout_drops_legacy_demo_metadata(self):
        data = json.loads((Path(__file__).parent / "layouts/1P-1L01-Model.json").read_text())
        data["settings"]["demonstration_mode"] = True
        data["zones"][0]["demonstration_complete"] = True
        data["zones"][0]["name"] = "DEMO - Old zone"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "layout.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertTrue(self.window.open_layout(path))
            saved = self.window._layout_data()
            self.assertNotIn("demonstration_mode", saved["settings"])
            self.assertNotIn("demonstration_complete", saved["zones"][0])
            self.assertEqual(saved["zones"][0]["name"], "Old zone")

    def test_background_load_appears_in_exported_calculation(self):
        import pymupdf
        _, dialog = self.editor()
        dialog.add_action()
        row = dialog.load_rows[1]
        row.equipment.setCurrentIndex(row.equipment.findText("Other"))
        row.current.setValue(5)
        dialog.confirmed.setChecked(True)
        dialog.accept()
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)
        with patch("gft_opto.gui.substation_gui.ZoneDialog", return_value=dialog), patch.object(
                dialog, "exec", return_value=dialog.DialogCode.Accepted):
            self.window._configure_protection_zones()
        report = self.window._zone_report
        self.assertEqual(report["zones"][0]["scenarios"][0]["peak_current_A"], 15)
        self.assertEqual(report["zones"][0]["scenarios"][0]["supplies"], {"DC 1": 5, "DC 2": 10})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "calculation.pdf"
            with patch("gft_opto.gui.substation_gui.QFileDialog.getSaveFileName", return_value=(str(path), "")):
                self.window._on_save_results_clicked()
            with pymupdf.open(path) as document:
                text = " ".join(" ".join(page.get_text().split()) for page in document)
            self.assertIn("Other load", text)
            self.assertIn("Other load: 5 A × 1 = 5 A (DC 1)", text)
            self.assertIn("Stage total: 15 A", text)

    def test_align_to_grid_is_explicit_and_undo_restores_exact_position(self):
        breaker, _ = self.editor()
        snap = OneLineSymbolItem.snap_to_grid_enabled
        OneLineSymbolItem.snap_to_grid_enabled = False
        breaker.setPos(QPointF(113, 217))
        OneLineSymbolItem.snap_to_grid_enabled = snap
        original = QPointF(breaker.pos())
        self.window._align_to_grid()
        self.assertNotEqual(breaker.pos(), original)
        self.window.undo_stack.undo()
        self.assertEqual(breaker.pos(), original)

    def test_import_batches_labels_and_snap_toggle_does_not_reroute(self):
        source = Path(__file__).parent / "layouts" / "1U-1L01-Model_1.json"
        with patch.object(symbols, "refresh_symbol_labels", wraps=symbols.refresh_symbol_labels) as refresh:
            scene = build_scene(json.loads(source.read_text()))
        self.assertEqual(refresh.call_count, 1)
        scene.deleteLater()
        self.assertTrue(self.window.open_layout(source))
        with patch.object(symbols.ConnectionItem, "update_path") as update:
            self.window.workspace_view.set_snap_to_grid(False)
            self.window.workspace_view.set_snap_to_grid(True)
        update.assert_not_called()
