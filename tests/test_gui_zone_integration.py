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

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from gft_opto.gui.layout_io import build_scene, scene_data, library_from_layout
from gft_opto.gui.substation_gui4 import SubstationGuiMockup
from gft_opto.gui.symbols import (
    BusItem, CircuitBreakerItem, CurrentTransformerItem, GroundItem,
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
        highlighted = [it._zone_connection_key for it in self.window._zone_overlays
                       if hasattr(it, "_zone_connection_key")]
        self.assertEqual(highlighted, [connection_key(first)])
        self.assertEqual(scene_data(self.window.workspace_scene), before)
        self.assertEqual(self.window._zone_report["supply_peaks"], {})
        self.assertEqual(self.window.last_result["peak_current_A"], "Incomplete study")

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
        self.assertEqual([it._zone_connection_key for it in self.window._zone_overlays
                          if hasattr(it, "_zone_connection_key")], [connection_key(wire)])

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
        self.assertTrue(dialog.zones[0]["topology_signature"].startswith("ct-study-v2:"))
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
        dialog.actions.cellWidget(0, 2).setCurrentText("Reclose")
        self.assertFalse(dialog.confirmed.isChecked())
        dialog.accept()  # Save a draft with the edited quantity, not a false approval.
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.zones[0]["actions"][0]["review_notes"], zone["actions"][0]["review_notes"])
        self.assertEqual(dialog.zones[0]["actions"][0]["stage"], "Reclose")
        self.assertEqual(zone["actions"][0]["units"], 1)


    def test_detect_missing_after_seed_edit_keeps_event_ids_unique(self):
        bus, ct, breaker, ground, wires, _ = self.example()
        proposals = propose_zones(self.window._protection_netlist())
        old_id = proposals[0]["id"]
        proposals[0]["seed"] = breaker.instance_id
        dialog = self.dialog(proposals)
        dialog.detect()
        self.assertEqual(len(dialog.zones), 2)
        self.assertEqual(len({z["id"] for z in dialog.zones}), 2)
        self.assertEqual(dialog.zones[0]["id"], old_id)
        self.assertEqual({z["seed"] for z in dialog.zones}, {bus.instance_id, breaker.instance_id})
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)


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
        with patch("gft_opto.gui.substation_gui4.ZoneDialog", return_value=dialog), \
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
        self.assertEqual(dialog.actions.cellWidget(0, 4).currentData(), "legacy")
        dialog.confirmed.setChecked(False)
        dialog.capture()
        self.assertEqual(dialog.zones[0]["actions"][0]["units"], 2)
        self.assertEqual(dialog.zones[0]["actions"][0]["notes"], "Verify legacy quantity")
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)


if __name__ == "__main__":
    unittest.main()
