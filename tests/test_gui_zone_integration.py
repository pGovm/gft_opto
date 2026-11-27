# Checks that the real GUI correctly connects zone configuration, detected regions, highlighting, saved layouts, and evaluation.

"""Real Qt workflow regressions for the new topology/study boundary.

No fake Qt modules or skip-on-import fallbacks: missing GUI dependencies must
remain visible as collection errors, not be reported as successful tests.
"""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QGraphicsPathItem

from gft_opto.gui.layout_io import build_scene, scene_data, library_from_layout
from gft_opto.gui.substation_gui import SubstationGuiMockup
from gft_opto.gui.symbols import (
    BusItem, CircuitBreakerItem, CurrentTransformerItem, GroundItem, IncomingLineItem,
    ConnectionItem, EQUIPMENT_DEFS, OneLineSymbolItem,
)
from gft_opto.gui.zone_dialog import ZoneDialog
from gft_opto.logic.protection_zones import connection_key
from gft_opto.logic.protection_study import (
    analyze_topology, topology_signature, configuration_signature, propose_zones,
)


class GuiZoneIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        if not QFontDatabase.families():
            font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf"
            if font.exists():
                QFontDatabase.addApplicationFont(str(font))

    def setUp(self):
        EQUIPMENT_DEFS.clear()
        self.window = SubstationGuiMockup()
        self.temp = tempfile.TemporaryDirectory()
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.reject()
            dialog.deleteLater()
        self.window._saved_snapshot = self.window._layout_data()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def add(self, item, x):
        item.setPos(x, 300)
        self.window.workspace_scene.addItem(item)
        return item

    def wire(self, a, ap, b, bp):
        wire = ConnectionItem(a, ap, b, bp)
        self.window.workspace_scene.addItem(wire)
        return wire

    def example(self):
        bus = self.add(BusItem(), 180)
        ct = self.add(CurrentTransformerItem(), 380)
        breaker = self.add(CircuitBreakerItem(), 530)
        breaker.properties = {"trip_coil_1_a": 12, "close_coil_a": 18,
                              "motor_inrush_a": 20, "motor_run_a": 4}
        ground = self.add(GroundItem(), 680)
        wires = [self.wire(bus, "right", ct, "left"),
                 self.wire(ct, "right", breaker, "left"),
                 self.wire(breaker, "right", ground, "node")]
        zone = {"id": "bus-event", "name": "Bus event", "kind": "Bus",
                "seed": bus.instance_id, "boundary_cts": [ct.instance_id],
                "actions": [{"component": breaker.instance_id, "load": "trip_coil_1_a",
                             "stage": "Primary trip", "supply": "DC 1", "units": 1}],
                "confirmed": False}
        self.approve(zone)
        self.window.protection_zones = [zone]
        return bus, ct, breaker, ground, wires, zone

    def approve(self, zone):
        zone["confirmed"] = True
        zone["topology_signature"] = topology_signature(self.window._protection_netlist())
        zone["configuration_signature"] = configuration_signature(zone)

    def dialog(self, zones=None):
        dialog = ZoneDialog(self.window._protection_netlist(),
                            self.window.protection_zones if zones is None else zones, self.window)
        self.dialogs.append(dialog)
        return dialog

    def show_active(self, dialog):
        """Keyboard shortcuts only reach a window that is active."""
        dialog.show()
        dialog.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(dialog))

    def select_region(self, ident):
        for index in range(self.window.zone_view.count()):
            data = self.window.zone_view.itemData(index)
            if isinstance(data, (tuple, list)) and tuple(data) == ("topology", ident):
                self.window.zone_view.setCurrentIndex(index)
                return
        self.fail(f"Region {ident} missing from selector")

    def test_live_scene_catalog_matches_netlist_catalog(self):
        self.example()
        self.assertEqual(self.window._scene_topology(), analyze_topology(self.window._protection_netlist()))

    def test_shared_bus_region_is_explicitly_flagged_in_zone_editor(self):
        netlist = {
            "components": [
                {"id": "bus1", "type": "bus", "label": "Bus 1", "properties": {}},
                {"id": "bus2", "type": "bus", "label": "Bus 2", "properties": {}},
                {"id": "ct1", "type": "ct", "properties": {}},
                {"id": "ct2", "type": "ct", "properties": {}},
                {"id": "cb1", "type": "breaker", "properties": {"trip_coil_1_a": 10}},
                {"id": "cb2", "type": "breaker", "properties": {"trip_coil_1_a": 20}},
                {"id": "g1", "type": "ground", "properties": {}},
                {"id": "g2", "type": "ground", "properties": {}},
            ],
            "connections": [
                ["bus1", "right", "bus2", "left"],
                ["bus1", "left", "ct1", "left"],
                ["ct1", "right", "cb1", "right"],
                ["cb1", "left", "g1", "node"],
                ["bus2", "right", "ct2", "left"],
                ["ct2", "right", "cb2", "right"],
                ["cb2", "left", "g2", "node"],
            ],
        }
        dialog = ZoneDialog(netlist, propose_zones(netlist))
        try:
            self.assertEqual(dialog.zone_list.count(), 1)
            self.assertIn("multiple buses share this detected region",
                          dialog.guidance.text().lower())
            self.assertIn("Bus 1", dialog.guidance.text())
            self.assertIn("Bus 2", dialog.guidance.text())
            self.assertIn("suggested nearby breakers (not assigned)",
                          dialog.guidance.text().lower())
            self.assertEqual(dialog.actions.rowCount(), 0)
        finally:
            dialog.reject()
            dialog.deleteLater()
            self.app.processEvents()

    def test_operating_table_deletes_selected_rows_and_retains_other_load(self):
        from gft_opto.logic.protection_study import evaluate_zones
        self.example()
        dialog = self.dialog()
        self.show_active(dialog)
        dialog.add_action()
        dialog.add_action()
        equipment = dialog.actions.cellWidget(1, 0)
        equipment.setCurrentIndex(equipment.findText("Other"))
        dialog.load_rows[1].saved_action["custom_load"] = {"name": "Panel heater DC"}
        dialog.load_rows[1].current.setValue(7.25)
        # Select rows 1 and 3 the way a user does (click, then Ctrl-click), then
        # press Delete. Multiplier cells are used because they open no dropdown.
        QTest.mouseClick(dialog.load_rows[0].multiplier, Qt.MouseButton.LeftButton)
        QTest.mouseClick(dialog.load_rows[2].multiplier, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.ControlModifier)
        self.assertEqual({index.row() for index in dialog.actions.selectedIndexes()}, {0, 2})
        focus = self.app.focusWidget()
        self.assertTrue(focus is dialog.actions or dialog.actions.isAncestorOf(focus))
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            QTest.keyClick(focus, Qt.Key.Key_Delete)
        self.assertEqual(dialog.actions.rowCount(), 1)
        dialog.capture()
        action = dialog.zones[0]["actions"][0]
        self.assertEqual(action["custom_load"], {"name": "Panel heater DC", "amps": 7.25})
        restored = self.dialog(json.loads(json.dumps(dialog.zones)))
        restored.capture()
        self.assertEqual(restored.zones[0]["actions"], dialog.zones[0]["actions"])
        report = evaluate_zones(restored.netlist, restored.zones)
        stage = report["zones"][0]["scenarios"][0]
        self.assertEqual(stage["peak_current_A"], 7.25)
        self.assertEqual(stage["loads"][0]["name"], "Panel heater DC")

    def test_shift_click_selects_load_range_and_ctrl_click_adds_a_row(self):
        self.example()
        dialog = self.dialog()
        self.show_active(dialog)
        for _ in range(4):
            dialog.add_action()
        QTest.mouseClick(dialog.load_rows[0].multiplier, Qt.MouseButton.LeftButton)
        QTest.mouseClick(dialog.load_rows[3].multiplier, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual({i.row() for i in dialog.actions.selectedIndexes()}, {0, 1, 2, 3})
        # A second Shift-click still uses the original anchor.
        QTest.mouseClick(dialog.load_rows[1].multiplier, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual({i.row() for i in dialog.actions.selectedIndexes()}, {0, 1})
        header = dialog.actions.verticalHeader()
        position = QPoint(header.width() // 2,
                          header.sectionViewportPosition(4) + header.sectionSize(4) // 2)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as prompt:
            QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.ControlModifier, position)
            prompt.assert_not_called()
            self.assertEqual({i.row() for i in dialog.actions.selectedIndexes()}, {0, 1, 4})
            self.assertTrue(dialog.delete_loads_button.isEnabled())
            QTest.mouseClick(dialog.delete_loads_button, Qt.MouseButton.LeftButton)
        prompt.assert_called_once()
        self.assertEqual(dialog.actions.rowCount(), 2)

    def test_row_number_clicks_select_without_opening_delete_confirmation(self):
        self.example()
        dialog = self.dialog()
        self.show_active(dialog)
        for _ in range(3):
            dialog.add_action()
        header = dialog.actions.verticalHeader()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as prompt:
            for row, modifiers in ((0, Qt.KeyboardModifier.NoModifier),
                                   (2, Qt.KeyboardModifier.ShiftModifier)):
                point = QPoint(10, header.sectionViewportPosition(row) + header.sectionSize(row) // 2)
                QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, modifiers, point)
            prompt.assert_not_called()
            self.assertEqual({i.row() for i in dialog.actions.selectedIndexes()}, {0, 1, 2})
            self.assertEqual(dialog.actions.rowCount(), 4)
            QTest.keyClick(dialog.actions, Qt.Key.Key_Delete)
            prompt.assert_called_once()
            self.assertEqual(dialog.actions.rowCount(), 4)

    def test_selected_rows_are_highlighted_and_deleted_together(self):
        self.example()
        dialog = self.dialog()
        self.show_active(dialog)
        dialog.add_action()
        dialog.add_action()
        self.app.processEvents()
        header = dialog.actions.verticalHeader()
        for row, modifiers in ((0, Qt.KeyboardModifier.NoModifier),
                               (2, Qt.KeyboardModifier.ControlModifier)):
            point = QPoint(10, header.sectionViewportPosition(row) + header.sectionSize(row) // 2)
            QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, modifiers, point)
        self.assertEqual(dialog._selected_load_rows(), {0, 2})
        self.assertEqual(dialog.actions.verticalHeader().selected_rows, {0, 2})
        for row in (0, 2):
            self.assertTrue(dialog.load_rows[row].equipment.property("loadSelected"))
            self.assertTrue(dialog.load_rows[row].multiplier.property("loadSelected"))
        self.assertFalse(dialog.load_rows[1].equipment.property("loadSelected"))
        self.assertEqual(dialog.delete_loads_button.text(), "Delete Selected Loads (2)")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as prompt:
            QTest.mouseClick(dialog.delete_loads_button, Qt.MouseButton.LeftButton)
        prompt.assert_called_once()
        self.assertEqual(dialog.actions.rowCount(), 1)

    def test_delete_and_backspace_inside_load_text_fields_edit_text_not_rows(self):
        self.example()
        dialog = self.dialog()
        self.show_active(dialog)
        dialog.add_action()
        self.app.processEvents()  # Lay out the new row before clicking into it.
        row = dialog.load_rows[-1]
        rows = dialog.actions.rowCount()
        # Clicking a field also selects its row, so a wrongly triggered table
        # shortcut would delete that row. An editable drop-down keeps focus
        # itself and passes keys to its text field.
        QTest.mouseClick(row.stage.lineEdit(), Qt.MouseButton.LeftButton)
        self.assertIs(self.app.focusWidget(), row.stage)
        row.stage.lineEdit().setText("Primary tripX")
        row.stage.lineEdit().end(False)
        QTest.keyClick(row.stage, Qt.Key.Key_Backspace)
        self.assertEqual(row.stage.currentText(), "Primary trip")
        supply = row.supply
        self.assertFalse(supply.isReadOnly())
        QTest.mouseClick(supply, Qt.MouseButton.LeftButton)
        self.assertIs(self.app.focusWidget(), supply)
        supply.setText("DC 1X")
        supply.setCursorPosition(4)
        QTest.keyClick(supply, Qt.Key.Key_Delete)
        self.assertEqual(supply.text(), "DC 1")
        self.assertEqual(dialog.actions.rowCount(), rows)

    def test_delete_button_confirms_selected_load_and_preserves_cancelled_row(self):
        self.example()
        dialog = self.dialog()
        dialog.show()
        dialog.add_action()
        dialog.add_action()
        dialog.load_rows[1].load.setCurrentIndex(dialog.load_rows[1].load.findData("close_coil_a"))
        dialog.load_rows[2].load.setCurrentIndex(dialog.load_rows[2].load.findData("motor_inrush_a"))
        dialog.confirmed.setChecked(True)
        header = dialog.actions.verticalHeader()
        middle = header.sectionViewportPosition(1) + header.sectionSize(1) // 2
        QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, QPoint(header.width() // 2, middle))
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as prompt:
            dialog.delete_loads_button.click()
        prompt.assert_called_once()
        self.assertEqual(prompt.call_args.args[-1], QMessageBox.StandardButton.No)
        self.assertEqual(dialog.actions.rowCount(), 3)
        self.assertTrue(dialog.confirmed.isChecked())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog.delete_loads_button.click()
        self.assertEqual(dialog.actions.rowCount(), 2)
        self.assertFalse(dialog.confirmed.isChecked())
        dialog.capture()
        self.assertEqual([action["load"] for action in dialog.zones[0]["actions"]],
                         ["trip_coil_1_a", "motor_inrush_a"])

    def test_verify_all_zones_preserves_edits_and_tracks_individual_changes(self):
        *_, zone = self.example()
        second = copy.deepcopy(zone)
        second.update(id="second-event", name="Second event", confirmed=False)
        dialog = self.dialog([zone, second])
        self.assertFalse(dialog.verify_all.isChecked())
        dialog.name.setText("Edited event")
        dialog.verify_all.click()
        self.assertTrue(all(z["confirmed"] for z in dialog.zones))
        self.assertEqual(dialog.zones[0]["name"], "Edited event")
        self.assertTrue(dialog.verify_all.isChecked())
        dialog.zone_list.setCurrentRow(1)
        self.assertTrue(dialog.confirmed.isChecked())
        self.assertTrue(dialog.verify_all.isChecked())
        dialog.unconfirm()
        self.assertFalse(dialog.verify_all.isChecked())
        dialog.verify_all.click()
        dialog.verify_all.click()
        self.assertFalse(any(z["confirmed"] for z in dialog.zones))
        self.assertFalse(dialog.confirmed.isChecked())

    def test_verify_all_zones_still_validates_on_save(self):
        *_, zone = self.example()
        zone.update(actions=[], confirmed=False)
        dialog = self.dialog([zone])
        dialog.verify_all.click()
        self.assertFalse(dialog.confirmed.isChecked())
        self.assertIn("remain drafts", dialog.verification_summary.text())
        # Bulk verification skips invalid zones; manually forcing confirmation
        # still cannot bypass the save-time validation.
        dialog.confirmed.setChecked(True)
        with patch.object(QMessageBox, "warning") as warning:
            dialog.accept()
        warning.assert_called_once()
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_verify_all_zones_can_save_valid_zones_with_incomplete_drafts(self):
        *_, zone = self.example()
        incomplete = copy.deepcopy(zone)
        incomplete.update(id="incomplete", name="Incomplete bus", actions=[],
                          boundary_cts=[], confirmed=False)
        dialog = self.dialog([zone, incomplete])
        dialog.verify_all.click()
        self.assertTrue(dialog.zones[0]["confirmed"])
        self.assertFalse(dialog.zones[1]["confirmed"])
        self.assertIn("Verified 1 of 2", dialog.verification_summary.text())
        self.assertIn("boundary CTs", dialog.verification_summary.toolTip())
        with patch.object(QMessageBox, "warning") as warning:
            dialog.accept()
        warning.assert_not_called()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_verify_all_keeps_incomplete_drafts_unconfirmed(self):
        *_, zone = self.example()
        zone.update(name="incomplete bus", actions=[], boundary_cts=[], confirmed=False)
        dialog = self.dialog([zone])
        dialog.verify_all.click()
        self.assertFalse(dialog.verify_all.isChecked())
        with patch.object(QMessageBox, "warning") as warning:
            dialog.accept()
        warning.assert_not_called()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        from gft_opto.logic.protection_study import evaluate_zones
        row = evaluate_zones(dialog.netlist, dialog.zones)["zones"][0]
        self.assertFalse(row["complete"])
        self.assertNotIn("demonstration_complete", row)
        self.assertTrue(row["warnings"])
        self.assertEqual(row["scenarios"], [])
        self.assertEqual(row["boundaries"], [])

    def test_verify_all_zones_is_disabled_until_a_zone_exists(self):
        dialog = self.dialog([])
        self.assertFalse(dialog.verify_all.isEnabled())
        dialog.add_zone()
        self.assertTrue(dialog.verify_all.isEnabled())
        self.assertFalse(dialog.verify_all.isChecked())

    def test_open_breaker_scene_and_netlist_partition_the_same_terminals(self):
        bus, ct, breaker, ground, _, _ = self.example()
        breaker.properties["status"] = "Open"
        direct = self.window._scene_topology()
        self.assertEqual(direct, analyze_topology(self.window._protection_netlist()))
        primary = [r for r in direct if r["level"] == "Primary"]
        self.assertFalse(any({ct.instance_id, ground.instance_id} <= set(r["members"]) for r in primary))
        self.assertEqual(sum(breaker.instance_id in r["core"] for r in primary), 2)

    def test_automatic_bus_and_feeder_highlights_overlap_at_bracketed_breaker(self):
        bus = self.add(BusItem(), 100)
        ct_bus = self.add(CurrentTransformerItem(), 300)
        breaker = self.add(CircuitBreakerItem(), 450)
        ct_line = self.add(CurrentTransformerItem(), 600)
        line = self.add(IncomingLineItem(), 800)
        for a, b in zip((bus, ct_bus, breaker, ct_line), (ct_bus, breaker, ct_line, line)):
            self.wire(a, "right", b, "line" if b is line else "left")
        before = scene_data(self.window.workspace_scene)
        self.window._on_run_evaluation_clicked()
        report = self.window._zone_report
        self.assertIsNotNone(report, self.window.output_box.toPlainText())
        self.assertEqual({z["kind"] for z in report["zones"]}, {"Bus", "Line"})
        self.assertEqual(len(report["zones"]), 2)
        self.assertTrue(all(breaker.instance_id in z["core"] for z in report["zones"]))
        self.assertEqual(len(self.window._zone_overlays), 2)
        self.assertEqual(scene_data(self.window.workspace_scene), before)
        self.assertFalse(report["supply_peaks"])

    def test_detection_and_highlights_do_not_change_saved_electrical_scene(self):
        self.example()
        before = copy.deepcopy(self.window._layout_data())
        for _ in range(3):
            self.window._on_run_evaluation_clicked()
            self.assertEqual(self.window.last_result["peak_current_A"], 12)
            self.assertEqual(self.window._layout_data(), before)
            self.assertTrue(self.window._zone_overlays)
            self.assertTrue(all(not isinstance(it, (OneLineSymbolItem, ConnectionItem))
                                for it in self.window._zone_overlays))
        self.window._clear_zone_highlights()
        self.assertEqual(self.window._layout_data(), before)

    def test_parallel_ct_regions_highlight_only_their_exact_terminal_wire(self):
        a = self.add(CurrentTransformerItem(), 220)
        b = self.add(CurrentTransformerItem(), 500)
        first = self.wire(a, "left", b, "left")
        self.wire(a, "right", b, "right")
        before = scene_data(self.window.workspace_scene)
        self.window._on_run_evaluation_clicked()
        self.assertIsNotNone(self.window._zone_report, self.window.output_box.toPlainText())
        region = next(r for r in self.window._zone_report["topology_regions"]
                      if r["level"] == "Primary" and
                      r["connections"] == json.loads(json.dumps([connection_key(first)])))
        self.select_region(region["id"])
        highlighted = [key for it in self.window._zone_overlays for key in it._zone_connection_keys]
        self.assertEqual(highlighted, [connection_key(first)])
        self.assertEqual(scene_data(self.window.workspace_scene), before)
        self.assertEqual(self.window._zone_report["supply_peaks"], {})
        self.assertEqual(self.window.last_result["peak_current_A"], "Incomplete study")

    def test_reference_zones_span_internal_cts_and_draw_one_rectangle_each(self):
        from PySide6.QtWidgets import QGraphicsRectItem
        path = Path(__file__).parent / "layouts" / "mvec_reference_one_line_draft.json"
        self.assertTrue(self.window.open_layout(str(path)))
        before = copy.deepcopy(self.window._layout_data())
        self.window._on_run_evaluation_clicked()
        zones = self.window._zone_report["zones"]
        self.assertEqual(len(zones), 3)
        transformer = next(z for z in zones if z["name"].startswith("TR2"))
        self.assertEqual(set(transformer["boundaries"]), {"ct_1", "ct_6"})
        self.assertTrue({"ct_2", "ct_3", "ct_4", "ct_5"} <= set(transformer["core"]))
        self.assertEqual(len(self.window._zone_overlays), 3)
        for overlay in self.window._zone_overlays:
            self.assertIsInstance(overlay, (QGraphicsRectItem, QGraphicsPathItem))
            self.assertEqual(overlay.brush().style(), Qt.BrushStyle.BDiagPattern)
            self.assertEqual(overlay.acceptedMouseButtons(), Qt.MouseButton.NoButton)
            self.assertLess(overlay.zValue(), 0)
        self.assertFalse(self.window._zone_report["supply_peaks"])
        self.assertEqual(self.window._layout_data(), before)

    def test_ct_only_drawing_displays_regions_without_fabricating_loads(self):
        a = self.add(CurrentTransformerItem(), 200)
        b = self.add(CurrentTransformerItem(), 400)
        wire = self.wire(a, "right", b, "left")
        self.window._on_run_evaluation_clicked()
        report = self.window._zone_report
        self.assertIsNotNone(report, self.window.output_box.toPlainText())
        self.assertEqual(report["zones"], [])
        self.assertEqual(len(report["topology_regions"]), 1)
        self.assertTrue(report["topology_regions"][0]["wire_only"])
        self.assertEqual(self.window.last_result["loads"], [])
        self.assertEqual([key for it in self.window._zone_overlays for key in it._zone_connection_keys], [connection_key(wire)])

    def test_zone_dialog_selects_and_confirms_wire_only_region(self):
        left = self.add(CircuitBreakerItem(), 150)
        a = self.add(CurrentTransformerItem(), 300)
        b = self.add(CurrentTransformerItem(), 450)
        right = self.add(CircuitBreakerItem(), 600)
        right.properties = {"trip_coil_1_a": 7}
        self.wire(left, "right", a, "left")
        self.wire(a, "right", b, "left")
        self.wire(b, "right", right, "left")
        zone = {"id": "gap", "name": "Gap event", "kind": "Manual", "seed": right.instance_id,
                "boundary_cts": [], "confirmed": False,
                "actions": [{"component": right.instance_id, "load": "trip_coil_1_a",
                             "stage": "Primary trip", "supply": "DC 1", "units": 1}]}
        dialog = self.dialog([zone])
        region = next(r for r in dialog.detected_regions if r["wire_only"] and r["level"] == "Primary")
        dialog.region_combo.setCurrentIndex(dialog.region_combo.findData(region["id"]))
        self.assertEqual(dialog.seed.currentData(), "")
        self.assertFalse(dialog.confirmed.isChecked())
        dialog.confirmed.setChecked(True)
        with patch.object(QMessageBox, "warning") as warning:
            dialog.accept()
        warning.assert_not_called()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.window.protection_zones = dialog.zones
        self.window._on_run_evaluation_clicked()
        self.assertEqual(self.window.last_result["peak_current_A"], 7)
        self.assertTrue(self.window._zone_report["zones"][0]["wire_only"])
        self.assertEqual(self.window.protection_zones[0]["topology_region"], region["signature"])

    def test_selected_region_and_assignments_survive_layout_roundtrip(self):
        bus, ct, breaker, ground, wires, zone = self.example()
        region = next(r for r in self.window._scene_topology() if r["core"] == [bus.instance_id])
        zone["topology_region"] = region["signature"]
        self.approve(zone)
        self.window._on_run_evaluation_clicked()
        expected = copy.deepcopy(self.window._layout_data())
        expected["library"] = library_from_layout(expected)
        path = Path(self.temp.name) / "zone-roundtrip.json"
        with patch.object(QMessageBox, "critical") as critical:
            self.assertTrue(self.window.save_layout(path))
            self.assertTrue(self.window.open_layout(str(path)))
        critical.assert_not_called()
        self.assertEqual(self.window._layout_data(), expected)
        self.window._on_run_evaluation_clicked()
        self.assertEqual(self.window.last_result["peak_current_A"], 12)
        self.assertEqual(self.window._zone_report["zones"][0]["connections"], region["connections"])
        self.assertNotIn("topology_regions", json.loads(path.read_text()))

    def test_old_layout_keeps_load_rows_but_requires_reconfirmation(self):
        *_, zone = self.example()
        zone["topology_signature"] = "old-detector-hash"
        zone.pop("configuration_signature", None)
        zone["notes"] = "Retain existing project metadata"
        data = self.window._layout_data()
        staged = build_scene(data)
        staged.clear()
        self.window._on_run_evaluation_clicked()
        self.assertFalse(self.window._zone_report["complete"])
        self.assertEqual(self.window.protection_zones[0]["actions"], zone["actions"])
        dialog = self.dialog()
        self.assertFalse(dialog.confirmed.isChecked())
        self.assertEqual(dialog.zones[0]["notes"], zone["notes"])
        dialog.confirmed.setChecked(True)
        with patch.object(QMessageBox, "warning") as warning:
            dialog.accept()
        warning.assert_not_called()
        self.assertTrue(dialog.zones[0]["topology_signature"].startswith("ct-study-v3:"))
        self.assertIn("configuration_signature", dialog.zones[0])

    def test_editing_boundary_clears_snapshot_and_confirmation(self):
        bus, ct, breaker, ground, wires, zone = self.example()
        region = next(r for r in self.window._scene_topology() if r["core"] == [bus.instance_id])
        zone["topology_region"] = region["signature"]
        self.approve(zone)
        dialog = self.dialog()
        self.assertTrue(dialog.confirmed.isChecked())
        dialog.cts.item(0).setCheckState(Qt.CheckState.Unchecked)
        dialog.capture()
        self.assertFalse(dialog.confirmed.isChecked())
        self.assertNotIn("topology_region", dialog.zones[0])
        self.assertEqual(dialog.region_combo.currentIndex(), 0)
        self.assertEqual(zone["topology_region"], region["signature"])

    def test_canceling_region_edit_does_not_mutate_main_window(self):
        self.example()
        before = copy.deepcopy(self.window._layout_data())
        dialog = self.dialog()
        dialog.region_combo.setCurrentIndex(1)
        dialog.capture()
        dialog.reject()
        self.assertEqual(self.window._layout_data(), before)

    def test_deleted_seed_and_boundary_ids_are_not_silently_discarded(self):
        *_, zone = self.example()
        zone.update(seed="deleted-seed", boundary_cts=["deleted-ct"], confirmed=False)
        dialog = self.dialog()
        self.assertEqual(dialog.seed.currentData(), "deleted-seed")
        self.assertIn("Missing", dialog.seed.currentText())
        dialog.capture()
        self.assertEqual(dialog.zones[0]["boundary_cts"], ["deleted-ct"])
        with patch.object(QMessageBox, "warning") as warning:
            dialog.accept()
        warning.assert_not_called()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_missing_current_blocks_confirmation_but_draft_can_be_saved(self):
        bus, ct, breaker, ground, wires, zone = self.example()
        breaker.properties.pop("trip_coil_1_a")
        dialog = self.dialog()
        dialog.confirmed.setChecked(True)
        with patch.object(QMessageBox, "warning") as warning:
            dialog.accept()
        warning.assert_called_once()
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        dialog.confirmed.setChecked(False)
        with patch.object(QMessageBox, "warning") as warning:
            dialog.accept()
        warning.assert_not_called()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_stale_detected_snapshot_is_preserved_for_review(self):
        bus, ct, breaker, ground, wires, zone = self.example()
        region = next(r for r in self.window._scene_topology() if r["core"] == [bus.instance_id])
        zone["topology_region"] = copy.deepcopy(region["signature"])
        zone["topology_region"]["connections"] = []
        self.approve(zone)
        dialog = self.dialog()
        self.assertEqual(dialog.zones[0]["topology_region"], zone["topology_region"])
        with patch.object(QMessageBox, "warning") as warning:
            dialog.accept()
        warning.assert_called_once()
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_view_controls_preserve_results_overlays_and_saved_state(self):
        _, _, breaker, *_ = self.example()
        w = self.window
        w._on_run_evaluation_clicked()
        self.assertIsNotNone(w.last_result, w.output_box.toPlainText())
        result, report = w.last_result, w._zone_report
        overlays = list(w._zone_overlays)
        self.assertTrue(overlays)
        output = w.output_box.toPlainText()
        w._saved_snapshot = w._layout_data()
        breaker.setSelected(True)
        for control in (w.grid_show_cb, w.pdf_show_cb, w.grid_snap_cb,
                        w.circuit_show_cb, w.zones_show_cb):
            for _ in range(2):
                with self.subTest(control=control.text(), checked=control.isChecked()):
                    control.toggle()
                    self.app.processEvents()
                    w._mark_modified()
                    self.assertIs(w.last_result, result)
                    self.assertIs(w._zone_report, report)
                    self.assertEqual(w._zone_overlays, overlays)
                    shown = w.zones_show_cb.isChecked() and w.circuit_show_cb.isChecked()
                    self.assertTrue(all(item.isVisible() == shown
                                        for item in overlays))
                    self.assertTrue(w.export_results_action.isEnabled())
                    self.assertEqual(w.output_box.toPlainText(), output)
                    self.assertNotIn(" *", w.windowTitle())
                    with patch.object(QMessageBox, "question") as prompt:
                        self.assertTrue(w._confirm_discard())
                    prompt.assert_not_called()
        w.grid_opacity_slider.setValue(60)
        self.assertIs(w.last_result, result)
        with patch.object(QMessageBox, "question") as prompt:
            self.assertTrue(w._confirm_discard())
        prompt.assert_not_called()

    def test_export_after_view_changes_keeps_the_evaluated_study(self):
        self.example()
        w = self.window
        w._on_run_evaluation_clicked()
        result = w.last_result
        self.assertIsNotNone(result)
        for control in (w.grid_show_cb, w.pdf_show_cb, w.circuit_show_cb, w.grid_snap_cb):
            control.toggle()
        w.grid_opacity_slider.setValue(60)
        with patch.object(QMessageBox, "information") as prompt, patch.object(w, "_save_html_as_pdf") as save:
            w._on_save_results_clicked()
        prompt.assert_not_called()
        save.assert_called_once()
        self.assertIn("Calculation Results", save.call_args.args[2])
        self.assertIs(w.last_result, result)

    def test_hidden_circuit_keeps_new_overlays_hidden_and_respects_show_zones(self):
        self.example()
        w = self.window
        w.circuit_show_cb.setChecked(False)
        w._on_run_evaluation_clicked()
        self.assertIsNotNone(w.last_result, w.output_box.toPlainText())
        self.assertTrue(w._zone_overlays)
        self.assertTrue(all(not overlay.isVisible() for overlay in w._zone_overlays))
        result = w.last_result
        w.zones_show_cb.setChecked(False)
        w.zones_show_cb.setChecked(True)
        self.assertTrue(all(not overlay.isVisible() for overlay in w._zone_overlays))
        w.circuit_show_cb.setChecked(True)
        self.assertTrue(all(overlay.isVisible() for overlay in w._zone_overlays))
        w.zones_show_cb.setChecked(False)
        w.circuit_show_cb.setChecked(False)
        w.circuit_show_cb.setChecked(True)
        self.assertTrue(all(not overlay.isVisible() for overlay in w._zone_overlays))
        self.assertIs(w.last_result, result)

    def test_current_edit_invalidates_result_but_not_topology(self):
        bus, ct, breaker, ground, wires, zone = self.example()
        topology = self.window._scene_topology()
        self.window._on_run_evaluation_clicked()
        self.assertEqual(self.window.last_result["peak_current_A"], 12)
        breaker.setSelected(True)
        self.window._on_selection_changed()
        self.window.prop_trip_coil_1.setText("15")
        self.window.prop_trip_coil_1.editingFinished.emit()
        self.assertIsNone(self.window.last_result)
        self.assertFalse(self.window._zone_overlays)
        self.assertEqual(self.window._scene_topology(), topology)
        self.window._on_run_evaluation_clicked()
        self.assertEqual(self.window.last_result["peak_current_A"], 15)

    def test_two_stages_and_supplies_use_separate_maxima(self):
        bus, ct, breaker, ground, wires, zone = self.example()
        zone["actions"] += [
            {"component": breaker.instance_id, "load": "close_coil_a", "stage": "Reclose", "supply": "DC 2", "units": 1},
            {"component": breaker.instance_id, "load": "motor_inrush_a", "stage": "Motor inrush", "supply": "DC 1", "units": 1},
        ]
        self.approve(zone)
        self.window._on_run_evaluation_clicked()
        report = self.window._zone_report
        self.assertTrue(report["complete"], self.window.output_box.toPlainText())
        self.assertEqual(report["supply_peaks"]["DC 1"]["peak_current_A"], 20)
        self.assertEqual(report["supply_peaks"]["DC 2"]["peak_current_A"], 18)
        self.assertEqual(self.window.last_result["peak_current_A"], 20)
        self.assertEqual(len(report["zones"][0]["scenarios"]), 3)

    def test_bad_saved_zone_is_rejected_without_replacing_current_scene(self):
        self.example()
        old_scene = self.window.workspace_scene
        data = self.window._layout_data()
        data["zones"][0]["actions"][0]["units"] = 0
        path = Path(self.temp.name) / "bad-zone.json"
        path.write_text(json.dumps(data))
        with patch.object(QMessageBox, "critical") as error:
            self.assertFalse(self.window.open_layout(str(path)))
        error.assert_called_once()
        self.assertIs(self.window.workspace_scene, old_scene)

    def test_bad_saved_zone_is_also_refused_by_configure_zones(self):
        # The same setting that stops a layout opening (test above) must not
        # reach the zone editor either; both use the same validation rules.
        self.example()
        self.window.protection_zones[0]["actions"][0]["units"] = 0
        before = copy.deepcopy(self.window.protection_zones)
        with patch.object(QMessageBox, "warning") as warning, \
                patch.object(ZoneDialog, "exec", return_value=QDialog.DialogCode.Rejected) as opened:
            self.window.configure_zones_action.trigger()
        warning.assert_called_once()
        self.assertIn("units", warning.call_args.args[2])
        opened.assert_not_called()
        self.assertEqual(self.window.protection_zones, before)


    def test_missing_configuration_approval_is_unconfirmed_in_the_dialog(self):
        *_, zone = self.example()
        del zone["configuration_signature"]
        dialog = self.dialog()
        self.assertFalse(dialog.confirmed.isChecked())
        self.window._on_run_evaluation_clicked()
        report = self.window.last_result["protection_analysis"]
        self.assertFalse(report["complete"])
        self.assertEqual(report["supply_peaks"], {})


    def test_action_extension_metadata_survives_dialog_capture_and_save(self):
        *_, zone = self.example()
        zone["actions"][0]["review_notes"] = {"reference": "field-check", "keep": [1, 2]}
        self.approve(zone)
        dialog = self.dialog()
        dialog.load_rows[0].stage.setCurrentText("Reclose")
        self.assertFalse(dialog.confirmed.isChecked())
        dialog.accept()  # Save a draft with the edited quantity, not a false approval.
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.zones[0]["actions"][0]["review_notes"], zone["actions"][0]["review_notes"])
        self.assertEqual(dialog.zones[0]["actions"][0]["stage"], "Reclose")
        self.assertEqual(zone["actions"][0]["units"], 1)


    def test_scene_adapter_preserves_ambiguous_colon_strings_as_two_wires(self):
        a, b, z = self.add(BusItem(), 180), self.add(BusItem(), 450), self.add(BusItem(), 780)
        a.instance_id, b.instance_id, z.instance_id = "a", "a:b", "z"
        ap, _ = a.add_tap(0, "b:c")
        bp, _ = b.add_tap(0, "c")
        self.wire(a, ap, z, "left")
        self.wire(b, bp, z, "left")
        self.assertEqual(len(self.window._protection_netlist()["connections"]), 2)


    def test_scene_adapter_ignores_an_off_scene_connection_endpoint(self):
        a, b = self.add(BusItem(), 180), self.add(BusItem(), 450)
        self.wire(a, "right", b, "left")
        self.window.workspace_scene.removeItem(b)
        net = self.window._protection_netlist()
        self.assertEqual(net["connections"], [])
        self.assertEqual([c["id"] for c in net["components"]], [a.instance_id])
        self.assertEqual(analyze_topology(net), self.window._scene_topology())
        # Restore before teardown: the saved-layout validator rightly refuses a
        # stale on-scene wire; this test is about the netlist scope only.
        self.window.workspace_scene.addItem(b)


    def test_layout_rejects_conflicting_symbol_type_metadata(self):
        _, _, breaker, *_ = self.example()
        before = self.window._layout_data()
        data = copy.deepcopy(before)
        record = next(c for c in data["components"] if c["id"] == breaker.instance_id)
        record["properties"]["symbol_type"] = "ct"
        with self.assertRaisesRegex(ValueError, "conflicts"):
            build_scene(data)
        self.assertEqual(self.window._layout_data(), before)


    def test_library_rejects_conflicting_symbol_type_metadata(self):
        self.example()
        data = self.window._layout_data()
        data["library"]["conflicting"] = {"symbol": "breaker", "name": "Mismatch",
            "properties": {"symbol_type": "ct"}}
        with self.assertRaisesRegex(ValueError, "conflicts"):
            build_scene(data)


    def test_relative_background_pdf_does_not_mark_just_opened_layout_dirty(self):
        import pymupdf
        pdf = Path(self.temp.name) / "background.pdf"
        with pymupdf.open() as document:
            document.new_page(width=72, height=72)
            document.save(pdf)
        data = self.window._layout_data()
        data["pdf"] = "background.pdf"
        path = Path(self.temp.name) / "layout.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertTrue(self.window.open_layout(str(path)))
        self.assertEqual(self.window.pdf_path, str(pdf.resolve()))
        self.assertEqual(self.window._layout_data(), self.window._saved_snapshot)
        self.assertNotIn(" *", self.window.windowTitle())


    def test_dialog_reject_does_not_materialize_settings_in_caller_netlist(self):
        self.example()
        netlist = self.window._protection_netlist()
        before = copy.deepcopy(netlist)
        dialog = ZoneDialog(netlist, self.window.protection_zones, self.window)
        self.dialogs.append(dialog)
        dialog.reject()
        self.assertEqual(netlist, before)

    def test_effective_breaker_settings_change_requires_new_dialog_review(self):
        _, _, breaker, _, _, zone = self.example()
        breaker.properties.update(coil_current_basis="per_mechanism",
            mechanism_arrangement="independent", pole_count=3, trip_coil_1_supply="A")
        self.approve(zone)
        breaker.properties["pole_count"] = 2
        dialog = self.dialog()
        self.assertFalse(dialog.confirmed.isChecked())
        self.window._on_run_evaluation_clicked()
        self.assertEqual(self.window.last_result["protection_analysis"]["supply_peaks"], {})

    def test_conflicting_legacy_supplies_remain_unresolved_in_draft(self):
        _, _, breaker, _, _, zone = self.example()
        zone["actions"].append(copy.deepcopy(zone["actions"][0]))
        zone["actions"][0]["supply"] = "A"
        zone["actions"][1]["supply"] = "B"
        self.approve(zone)
        dialog = self.dialog()
        self.assertFalse(dialog.confirmed.isChecked())
        self.assertNotIn("trip_coil_1_supply", dialog.components[breaker.instance_id]["properties"])
        dialog.capture()
        self.assertEqual({a["supply"] for a in dialog.zones[0]["actions"]}, {"A", "B"})
        dialog.accept()  # A draft may be saved without inventing a supply.
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        with patch("gft_opto.gui.substation_gui.ZoneDialog", return_value=dialog), \
             patch.object(dialog, "exec", return_value=QDialog.DialogCode.Accepted):
            self.window._configure_protection_zones()
        self.assertNotIn("trip_coil_1_supply", breaker.properties)
        self.assertEqual(self.window.last_result["protection_analysis"]["supply_peaks"], {})
        path = Path(self.temp.name) / "draft-supplies.json"
        self.assertTrue(self.window.save_layout(path))

    def test_draft_legacy_quantity_is_retained_until_operation_review(self):
        _, _, _, _, _, zone = self.example()
        zone["actions"][0]["units"] = 2
        zone["actions"][0]["notes"] = "Verify legacy quantity"
        self.approve(zone)
        dialog = self.dialog()
        self.assertEqual(dialog.load_rows[0].operation.currentData(), "legacy")
        dialog.confirmed.setChecked(False)
        dialog.capture()
        self.assertEqual(dialog.zones[0]["actions"][0]["units"], 2)
        self.assertEqual(dialog.zones[0]["actions"][0]["notes"], "Verify legacy quantity")
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)


if __name__ == "__main__":
    unittest.main()
