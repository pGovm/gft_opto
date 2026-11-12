# Checks editing, layout persistence, breaker properties, copy/paste, library loading, and report export in a headless GUI.

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
from PySide6.QtCore import Qt, QMimeData
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QMessageBox

from gft_opto.gui.commands import AddEquipmentCommand, AddConnectionCommand, SplitWireCommand
from gft_opto.gui.layout_io import build_scene, scene_data, library_from_layout
from gft_opto.gui.substation_gui import SubstationGuiMockup
from gft_opto.gui.symbols import (
    BusItem, CircuitBreakerItem, ConnectionItem, EQUIPMENT_DEFS, OneLineSymbolItem,
    make_user_equipment, CurrentTransformerItem, GroundItem,
)
from gft_opto.logic.protection_study import topology_signature, configuration_signature
from gft_opto.gui.zone_dialog import ZoneDialog
from gft_opto.gui.workspace import COMPONENT_CLIPBOARD_MIME
from gft_opto.logic.netlist_manager import build_netlist, export_netlist_json


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
        self.app.clipboard().clear()
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

    def test_surge_arrester_mcov_and_layout_round_trip(self):
        from gft_opto.gui.custom_widget_tool import ComponentDialog
        dialog = ComponentDialog(self.window)
        dialog.symbol_combo.setCurrentIndex(dialog.symbol_combo.findData("surge_arrester"))
        dialog.rating_spin.setValue(88)
        data = dialog.component_data()
        self.assertEqual(data["Rating"], 88)
        self.assertNotIn("TripCoil1", data)
        arrester = make_user_equipment("surge_arrester", "SA-H1", {"rating_kv": 88, "symbol_type": "surge_arrester"})
        ground = GroundItem()
        scene = self.window.workspace_scene
        scene.addItem(arrester)
        scene.addItem(ground)
        scene.addItem(ConnectionItem(arrester, "right", ground, "node"))
        arrester.setSelected(True)
        self.assertEqual(self.window.prop_voltage_label.text(), "MCOV (kV):")
        self.window._on_property_voltage_changed("90")
        self.assertEqual(arrester.properties["rating_kv"], 90)
        layout = self.window._layout_data()
        restored = build_scene(layout)
        loaded = next(i for i in restored.items() if isinstance(i, OneLineSymbolItem) and i.equip_type == "surge_arrester")
        self.assertEqual(loaded.properties["rating_kv"], 90)
        self.assertEqual(len(loaded.connections()), 1)
        dialog.deleteLater()

    def test_reference_incoming_lines_open_with_single_connected_terminal(self):
        from gft_opto.gui.custom_widget_tool import ComponentDialog
        dialog = ComponentDialog(self.window)
        dialog.symbol_combo.setCurrentIndex(dialog.symbol_combo.findData("incoming_line"))
        self.assertNotIn("TripCoil1", dialog.component_data())
        self.assertNotIn("Rating", dialog.component_data())
        path = Path(__file__).parent / "layouts" / "mvec_reference_one_line_draft.json"
        self.assertTrue(self.window.open_layout(str(path)))
        incoming = [i for i in self.window.workspace_scene.items()
                    if isinstance(i, OneLineSymbolItem) and i.equip_type == "incoming_line"]
        self.assertEqual(len(incoming), 2)
        for item in incoming:
            self.assertEqual(set(item.ports()), {"line"})
            self.assertEqual(len(item.connections()), 1)
        restored = build_scene(self.window._layout_data())
        self.assertEqual(sum(isinstance(i, OneLineSymbolItem) and i.equip_type == "incoming_line"
                             for i in restored.items()), 2)
        dialog.deleteLater()

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
        self.approve_current_zones()

    def approve_current_zones(self):
        """Simulate deliberate user review after fixture assignment/hardware edits."""
        for zone in self.window.protection_zones:
            zone["confirmed"] = True
            zone["topology_signature"] = topology_signature(self.window._protection_netlist())
            zone["configuration_signature"] = configuration_signature(zone)

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
        before["library"] = library_from_layout(before)
        self.assertTrue(w.save_layout(path))
        w.undo_stack.clear()
        self.assertTrue(w.open_layout(str(path)))
        self.assertEqual(w._layout_data(), before)
        self.assertEqual(w.grid_opacity_slider.value(), 60)
        self.assertEqual(w.workspace_view._grid_item.opacity(), 0.6)
        self.assertEqual(w.equipment_list.count(), len(before["library"]))
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
        with patch("gft_opto.gui.substation_gui.QFileDialog.getSaveFileName", return_value=(str(path), "")):
            w._on_save_results_clicked()
        self.assertGreater(path.stat().st_size, 0)
        with pymupdf.open(path) as document:
            pages = [page.get_text() for page in document]
        text = "".join(pages)
        self.assertIn("18", text)
        self.assertIn("Zone Summary", pages[0])
        self.assertIn("Warnings Summary", pages[0])
        self.assertIn("Test Bus Zone", text)
        self.assertNotIn("bus_zone", text)
        self.assertIn("Primary trip: Confirmed 18.0 A", text)
        self.assertIn("Appendix: Detected Topology Regions", pages[-1])
        self.assertIn("Results saved:", w.footer_status_label.text())
        bad_path = Path(self.temp.name) / "missing" / "results.pdf"
        with patch("gft_opto.gui.substation_gui.QFileDialog.getSaveFileName", return_value=(str(bad_path), "")), patch.object(QMessageBox, "critical") as error:
            w._on_save_results_clicked()
            error.assert_called_once()

    def test_incomplete_zone_pdf_calls_out_provisional_values_and_missing_currents(self):
        w = self.window
        breaker = self.add_breaker()
        self.assign_test_zone(breaker)
        w._on_run_evaluation_clicked()
        path = Path(self.temp.name) / "incomplete-results.pdf"
        with patch("gft_opto.gui.substation_gui.QFileDialog.getSaveFileName", return_value=(str(path), "")):
            w._on_save_results_clicked()
        with pymupdf.open(path) as document:
            pages = [page.get_text() for page in document]
        self.assertIn("REVIEW REQUIRED", pages[0])
        self.assertIn("Missing or invalid currents", pages[0])
        self.assertIn("current is missing", pages[0])
        self.assertIn("Test Bus Zone", pages[0])
        self.assertNotIn("bus_zone", "".join(pages))
        self.assertIn("Not calculated", pages[0])

    def test_netlist_pdf_export(self):
        w = self.window
        self.add_breaker()
        path = Path(self.temp.name) / "netlist.pdf"
        with patch("gft_opto.gui.substation_gui.QFileDialog.getSaveFileName", return_value=(str(path), "")):
            w._on_save_netlist_pdf()
        self.assertGreater(path.stat().st_size, 0)
        with pymupdf.open(path) as document:
            text = "".join(page.get_text() for page in document)
        self.assertIn("Netlist:", text)
        self.assertIn("Components", text)
        self.assertIn("Netlist PDF saved:", w.footer_status_label.text())

    def test_netlist_json_import_rebuilds_diagram(self):
        w = self.window
        breaker = self.add_breaker()
        breaker.setPos(320, 460)
        breaker.set_rotation_deg(90)
        breaker.properties["trip_coil_1_a"] = 12
        expected_position = breaker.pos()
        bus = BusItem()
        tap, _ = bus.add_tap(0)
        bus.setPos(700, 500)
        w.workspace_scene.addItem(bus)
        w.workspace_scene.addItem(
            ConnectionItem(breaker, "left", bus, tap)
        )
        w.project_box.setText("Imported Project")
        path = Path(self.temp.name) / "netlist.json"
        export_netlist_json(build_netlist(w.workspace_scene, "Imported Project"), path)

        with patch.object(w, "_confirm_discard", return_value=True), patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            self.assertTrue(w.import_netlist_json(str(path)))

        imported = next(
            item for item in w.workspace_scene.items()
            if isinstance(item, CircuitBreakerItem)
        )
        self.assertEqual(w.project_box.text(), "Imported Project")
        self.assertIn("*", w.windowTitle())
        self.assertEqual(imported.pos(), expected_position)
        self.assertEqual(imported.rotation_deg, 90)
        self.assertEqual(imported.properties["trip_coil_1_a"], 12)
        imported_bus = next(
            item for item in w.workspace_scene.items() if isinstance(item, BusItem)
        )
        self.assertIn(tap, imported_bus.ports())
        self.assertEqual(
            sum(isinstance(item, ConnectionItem) for item in w.workspace_scene.items()),
            1,
        )

    def test_invalid_netlist_json_keeps_current_diagram(self):
        w = self.window
        breaker = self.add_breaker()
        original_scene = w.workspace_scene
        netlist = build_netlist(original_scene, "Invalid Project")
        netlist["connections"] = [{
            "id": "connection_1",
            "from": {"component": netlist["components"][0]["id"], "port": "left"},
            "to": {"component": netlist["components"][0]["id"], "port": "unknown"},
        }]
        path = Path(self.temp.name) / "invalid_netlist.json"
        export_netlist_json(netlist, path)

        with patch.object(QMessageBox, "critical") as error:
            self.assertFalse(w.import_netlist_json(str(path)))
        error.assert_called_once()
        self.assertIs(w.workspace_scene, original_scene)
        self.assertIn(breaker, w.workspace_scene.items())

    def test_unknown_netlist_component_type_is_rejected(self):
        w = self.window
        breaker = self.add_breaker()
        original_scene = w.workspace_scene
        netlist = build_netlist(original_scene, "Unsupported Project")
        netlist["components"][0]["type"] = "unknown_equipment"
        path = Path(self.temp.name) / "unsupported_netlist.json"
        export_netlist_json(netlist, path)

        with patch.object(QMessageBox, "critical") as error:
            self.assertFalse(w.import_netlist_json(str(path)))
        self.assertIn("unsupported type", error.call_args.args[2])
        self.assertIs(w.workspace_scene, original_scene)
        self.assertIn(breaker, w.workspace_scene.items())

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

    def test_ribbon_controls_fit_and_open_layout_button_works(self):
        w = self.window
        w.show()
        self.assertEqual([w.ribbon.tabText(i).replace("&", "") for i in range(w.ribbon.count())],
                         ["File", "Edit", "View", "Analysis"])
        pages = ((w.new_layout_btn, w.open_layout_btn, w.save_layout_btn, w.save_as_btn,
                  w.load_btn, w.import_netlist_btn, w.save_results_btn),
                 (w.undo_btn, w.redo_btn, w.copy_btn, w.paste_btn, w.delete_btn, w.rotate_btn,
                  w.create_component_btn),
                 (w.grid_show_cb, w.pdf_show_cb, w.circuit_show_cb, w.zones_show_cb,
                  w.grid_snap_cb, w.grid_opacity_slider),
                 (w.check_btn, w.zone_setup_btn, w.run_btn, w.expand_analysis_btn))
        for width in (1000, 1280):
            w.resize(width, 800)
            for index, controls in enumerate(pages):
                w.ribbon.setCurrentIndex(index)
                self.app.processEvents()
                for control in controls:
                    self.assertTrue(control.isVisible())
                    origin = control.mapTo(w, QPointF(0, 0).toPoint())
                    self.assertGreaterEqual(origin.x(), 0)
                    self.assertLessEqual(origin.x() + control.width(), w.width())
        self.assertTrue(any(action.shortcut().toString() == "Ctrl+O" for action in w.actions()))
        w.ribbon.setCurrentIndex(0)
        with patch("gft_opto.gui.substation_gui.QFileDialog.getOpenFileName", return_value=("", "")) as dialog:
            w.open_layout_btn.click()
            dialog.assert_called_once()

    def test_ribbon_undo_redo_follow_the_actual_edit_history(self):
        w = self.window
        w.ribbon.setCurrentIndex(1)
        self.assertFalse(w.undo_btn.isEnabled())
        self.assertFalse(w.redo_btn.isEnabled())
        breaker = self.add_breaker()
        self.assertTrue(w.undo_btn.isEnabled())
        w.undo_btn.click()
        self.assertIsNone(breaker.scene())
        self.assertFalse(w.undo_btn.isEnabled())
        self.assertTrue(w.redo_btn.isEnabled())
        w.redo_btn.click()
        self.assertIs(breaker.scene(), w.workspace_scene)
        self.assertEqual(w.undo_btn.text(), "Undo")

    def test_ribbon_view_controls_keep_working_after_changing_tabs(self):
        w = self.window
        breaker = self.add_breaker()
        w.ribbon.setCurrentIndex(2)
        w.grid_show_cb.click()
        self.assertFalse(w.workspace_view._grid_item.isVisible())
        w.circuit_show_cb.click()
        self.assertFalse(breaker.isVisible())
        w.zones_show_cb.click()
        self.assertFalse(w._zones_visible)
        w.grid_opacity_slider.setValue(60)
        w.grid_snap_cb.click()
        self.assertFalse(w.workspace_view._snap_to_grid)
        w.ribbon.setCurrentIndex(0)
        w.ribbon.setCurrentIndex(2)
        self.assertFalse(w.grid_show_cb.isChecked())
        self.assertEqual(w.workspace_view._grid_item.opacity(), 0.6)
        w.grid_snap_cb.setChecked(True)

    def test_ribbon_evaluation_commits_pending_property_edits(self):
        w = self.window
        w.show()
        w.activateWindow()
        breaker = self.add_breaker()
        w.ribbon.setCurrentIndex(3)
        w.prop_trip_coil_1.setFocus()
        w.prop_trip_coil_1.setText("17")
        self.app.processEvents()
        QTest.mouseClick(w.run_btn, Qt.MouseButton.LeftButton)
        self.assertEqual(breaker.properties["trip_coil_1_a"], 17)
        self.assertIsNotNone(w.last_result)

    def test_pdf_background_round_trip(self):
        w = self.window
        pdf_path = Path(self.temp.name) / "background.pdf"
        with pymupdf.open() as document:
            document.new_page(width=720, height=360)
            document.save(pdf_path)
        with patch("gft_opto.gui.substation_gui.QFileDialog.getOpenFileName", return_value=(str(pdf_path), "")):
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
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Save), patch("gft_opto.gui.substation_gui.QFileDialog.getSaveFileName", return_value=(str(path), "")):
            self.assertTrue(w._confirm_discard())
        w.new_layout()
        self.assertEqual(scene_data(w.workspace_scene)["components"], [])

    def test_zone_dialog_edits_highlights_and_persistence(self):
        w = self.window
        breaker = self.add_breaker()
        self.commit_current("12")
        self.assign_test_zone(breaker)
        w.breaker_fields["coil_current_basis"].setCurrentIndex(1)
        w.breaker_fields["mechanism_arrangement"].setCurrentIndex(1)
        self.approve_current_zones()
        dialog = ZoneDialog(w._protection_netlist(), w.protection_zones, w)
        self.assertTrue(dialog.confirmed.isChecked())
        dialog.actions.cellWidget(0, 4).setCurrentIndex(1)
        self.assertFalse(dialog.confirmed.isChecked())
        dialog.actions.cellWidget(0, 4).setCurrentIndex(0)
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
        self.assertEqual(w.protection_zones[0]["actions"][0]["operation"], "all_poles")
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

    def test_selected_breaker_equipment_settings_undo_save_and_evaluate(self):
        w = self.window
        breaker = self.add_breaker()
        self.commit_current("10")
        self.assign_test_zone(breaker)
        w.breaker_fields["coil_current_basis"].setCurrentIndex(1)
        w.breaker_fields["mechanism_arrangement"].setCurrentIndex(1)
        field = w.breaker_fields["trip_coil_1_supply"]
        field.setText("Battery A")
        field.editingFinished.emit()
        w._on_run_evaluation_clicked()
        self.assertFalse(w.last_result["protection_analysis"]["complete"])
        self.approve_current_zones()
        w._on_run_evaluation_clicked()
        self.assertEqual(w.last_result["protection_analysis"]["supply_peaks"]["Battery A"]["peak_current_A"], 30)
        w.undo_stack.undo()
        self.assertEqual(breaker.properties["trip_coil_1_supply"], "DC 1")
        w.undo_stack.redo()
        self.assertEqual(breaker.properties["trip_coil_1_supply"], "Battery A")
        dialog = ZoneDialog(w._protection_netlist(), w.protection_zones, w)
        self.assertTrue(dialog.actions.cellWidget(0, 3).isReadOnly())
        self.assertEqual(dialog.actions.cellWidget(0, 3).text(), "Battery A")
        self.assertEqual(dialog.actions.cellWidget(0, 5).text(), "×3")
        dialog.reject()
        path = Path(self.temp.name) / "hardware.json"
        self.assertTrue(w.save_layout(path))
        self.assertTrue(w.open_layout(str(path)))
        w._on_run_evaluation_clicked()
        self.assertEqual(w.last_result["peak_current_A"], 30)

    def test_legacy_zone_migration_and_hardware_initialization_undo_together(self):
        w = self.window
        breaker = self.add_breaker()
        self.commit_current("10")
        self.assign_test_zone(breaker)
        w.protection_zones[0]["actions"][0]["supply"] = "Legacy DC 2"
        before = w._layout_data()
        dialog = ZoneDialog(w._protection_netlist(), w.protection_zones, w)
        self.assertEqual(dialog.actions.cellWidget(0, 3).text(), "Legacy DC 2")
        dialog.confirmed.setChecked(True)
        dialog.accept()
        with patch("gft_opto.gui.substation_gui.ZoneDialog", return_value=dialog), patch.object(dialog, "exec", return_value=dialog.DialogCode.Accepted):
            w._configure_protection_zones()
        self.assertEqual(breaker.properties["trip_coil_1_supply"], "Legacy DC 2")
        self.assertEqual(w.last_result["protection_analysis"]["supply_peaks"]["Legacy DC 2"]["peak_current_A"], 10)
        w.undo_stack.undo()
        self.assertEqual(w._layout_data(), before)
        w.undo_stack.redo()
        w._on_run_evaluation_clicked()
        self.assertEqual(w.last_result["protection_analysis"]["supply_peaks"]["Legacy DC 2"]["peak_current_A"], 10)

    def test_loaded_components_populate_library_with_properties_without_duplicates(self):
        w = self.window
        sample = Path(__file__).parent / "layouts" / "two_zone_demo.json"
        self.assertTrue(w.open_layout(str(sample)))
        names = [w.equipment_list.item(i).text() for i in range(w.equipment_list.count())]
        self.assertIn("CB-HV", names)
        self.assertIn("CB-LV", names)
        self.assertIn("BUS-1", names)
        self.assertIn("T1", names)
        definition = next(meta for meta in EQUIPMENT_DEFS.values() if meta["definition"]["name"] == "CB-HV")
        item = definition["factory"]()
        self.assertEqual(item.properties["trip_coil_1_a"], 10)
        self.assertEqual(item.properties["trip_coil_2_supply"], "DC 2")
        count = w.equipment_list.count()
        path = Path(self.temp.name) / "library.json"
        self.assertTrue(w.save_layout(path))
        self.assertTrue(w.open_layout(str(path)))
        self.assertEqual(w.equipment_list.count(), count)
        self.assertEqual(len(w._layout_data()["library"]), count)

    def test_component_copy_preserves_equipment_with_new_ids_and_undo(self):
        w = self.window
        original = self.add_breaker()
        self.commit_current("12")
        original.set_rotation_deg(90)
        original.set_scale_factor(1.5)
        original.display_id = "CB-A"
        original.properties.update(coil_current_basis="per_mechanism", mechanism_arrangement="independent",
                                   pole_count=3, trip_coil_1_supply="Battery A")
        view = w.workspace_view
        self.assertTrue(view.copy_selected())
        before_count = w.undo_stack.count()
        self.assertTrue(view.paste_components())
        pasted = w.workspace_scene.selectedItems()[0]
        self.assertIsNot(pasted, original)
        self.assertNotEqual(pasted.instance_id, original.instance_id)
        self.assertNotEqual(pasted.visible_id(), original.visible_id())
        self.assertEqual(pasted.properties, original.properties)
        self.assertEqual(pasted.rotation_deg, 90)
        self.assertEqual(pasted.scale_factor, 1.5)
        self.assertNotEqual(pasted.pos(), original.pos())
        self.assertEqual(w.undo_stack.count(), before_count + 1)
        w.undo_stack.undo()
        self.assertIsNone(pasted.scene())
        w.undo_stack.redo()
        self.assertIs(pasted.scene(), w.workspace_scene)
        self.assertTrue(view.paste_components())
        ids = [i.instance_id for i in w.workspace_scene.items() if isinstance(i, OneLineSymbolItem)]
        labels = [i.visible_id() for i in w.workspace_scene.items() if isinstance(i, OneLineSymbolItem)]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(labels), len(set(labels)))
        pasted.properties["trip_coil_1_a"] = 99
        self.assertEqual(original.properties["trip_coil_1_a"], 12)

    def test_copy_group_includes_internal_wires_and_bus_taps_only(self):
        w = self.window
        breaker = self.add_breaker()
        bus, external = BusItem(), CircuitBreakerItem()
        for item in (bus, external):
            w.workspace_scene.addItem(item)
        tap, _ = bus.add_tap(20, "test_tap")
        w.workspace_scene.addItem(ConnectionItem(breaker, "right", bus, tap))
        w.workspace_scene.addItem(ConnectionItem(external, "left", bus, "left"))
        bus.setSelected(True)
        self.assertTrue(w.workspace_view.copy_selected())
        self.assertTrue(w.workspace_view.paste_components())
        group = w.workspace_scene.selectedItems()
        self.assertEqual(len(group), 2)
        cloned_bus = next(i for i in group if isinstance(i, BusItem))
        self.assertIn(tap, cloned_bus.ports())
        self.assertEqual(len(cloned_bus.connections()), 1)
        wire = cloned_bus.connections()[0]
        self.assertIn(wire.from_item, group)
        self.assertIn(wire.to_item, group)
        self.assertNotIn(external, group)
        w.undo_stack.undo()
        self.assertEqual(len(bus.connections()), 2)
        w.undo_stack.redo()
        self.assertEqual(len(cloned_bus.connections()), 1)

    def test_copy_paste_shortcuts_keep_normal_text_editing(self):
        w = self.window
        original = self.add_breaker()
        w.show()
        w.workspace_view.setFocus()
        self.app.processEvents()
        QTest.keyClick(w.workspace_view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(self.app.clipboard().mimeData().hasFormat(COMPONENT_CLIPBOARD_MIME))
        QTest.keyClick(w.workspace_view, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len([i for i in w.workspace_scene.items() if isinstance(i, CircuitBreakerItem)]), 2)
        original.setSelected(True)
        w.workspace_scene.clearSelection()
        original.setSelected(True)
        w.prop_name.setFocus()
        w.prop_name.selectAll()
        QTest.keyClick(w.prop_name, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.app.clipboard().text(), original.visible_id())
        mime = QMimeData()
        mime.setData(COMPONENT_CLIPBOARD_MIME, b"invalid json")
        self.app.clipboard().setMimeData(mime)
        count = len(w.workspace_scene.items())
        self.assertFalse(w.workspace_view.paste_components())
        self.assertEqual(len(w.workspace_scene.items()), count)


if __name__ == "__main__":
    unittest.main()
