# Checks that two components showing the same ID are caught by Validate, the
# study and the properties panel, so the results always say which is which.

"""Repeated component IDs: study warning, Validate entry and rename refusal."""
import copy
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from gft_opto.gui.circuit_check import check_circuit
from gft_opto.gui.commands import AddEquipmentCommand
from gft_opto.gui.symbols import CircuitBreakerItem, GroundItem, JunctionItem
from gft_opto.logic.momentary_calc2 import evaluate_scene, netlist_from_scene
from gft_opto.logic.protection_study import duplicate_ids, evaluate_zones
from test_protection_study import confirmed, example

APP = QApplication.instance() or QApplication([])
LAYOUTS = Path(__file__).parent / "layouts"


class StudyTests(unittest.TestCase):
    def test_rule(self):
        self.assertEqual(duplicate_ids([("CB-1", "breaker"), ("cb-1 ", "breaker"), ("CB-2", "breaker")]),
                         {"CB-1 / cb-1": 2})
        self.assertEqual(duplicate_ids([("CB-1", "breaker"), ("CB-1", "ct")]), {"CB-1": 2})
        # Grounds and junctions do not draw their IDs.
        self.assertEqual(duplicate_ids([("GND", "ground"), ("GND", "ground"), ("J", "junction"),
                                        ("J", "junction")]), {})
        self.assertEqual(duplicate_ids([("GND", "ground"), ("GND", "breaker")]), {})

    def test_complete_study_becomes_incomplete_when_two_components_share_an_id(self):
        netlist = example()
        zone = confirmed(netlist)
        self.assertTrue(evaluate_zones(netlist, [zone])["complete"])
        repeated = copy.deepcopy(netlist)
        for component in repeated["components"]:
            if component["id"] in ("cb1", "cb2"):
                component["label"] = "CB-1"
        report = evaluate_zones(repeated, [zone])
        self.assertFalse(report["complete"])
        self.assertEqual(report["supply_peaks"], {})
        self.assertTrue(any("same ID: CB-1" in w for w in report["warnings"]), report["warnings"])

    def test_supplied_layouts_have_no_repeated_ids(self):
        from gft_opto.gui.substation_gui import SubstationGuiMockup

        with patch.object(QMessageBox, "warning"), patch.object(QMessageBox, "critical"):
            for path in sorted(LAYOUTS.glob("*.json")):
                window = SubstationGuiMockup()
                try:
                    self.assertTrue(window.open_layout(str(path)))
                    report = evaluate_zones(window._protection_netlist(),
                                            copy.deepcopy(window.protection_zones))
                    with self.subTest(layout=path.name):
                        self.assertFalse([w for w in report["warnings"] if "same ID" in w])
                        self.assertEqual(check_circuit(window.workspace_scene)["duplicate_ids"], [])
                finally:
                    window._saved_snapshot = window._layout_data()
                    window.close()
                    window.deleteLater()
                    APP.processEvents()


class GuiTests(unittest.TestCase):
    def setUp(self):
        from gft_opto.gui.substation_gui import SubstationGuiMockup

        self.window = SubstationGuiMockup()
        self.scene = self.window.workspace_scene

    def tearDown(self):
        self.window._saved_snapshot = self.window._layout_data()
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()

    def add(self, item, shown=""):
        self.window.undo_stack.push(AddEquipmentCommand(self.scene, item))
        item.display_id = shown
        return item

    def rename(self, item, text):
        self.scene.clearSelection()
        item.setSelected(True)
        self.window.prop_name.setText(text)
        self.window.prop_name.editingFinished.emit()
        return item.visible_id()

    def test_rename_to_an_id_in_use_is_refused(self):
        first = self.add(CircuitBreakerItem(), "CB-1")
        second = self.add(CircuitBreakerItem(), "CB-2")
        for text in ("CB-1", "cb-1", "  CB-1 "):
            with self.subTest(text=text):
                self.assertEqual(self.rename(second, text), "CB-2")
                self.assertIn("already used", self.window.footer_status_label.text())
        self.assertEqual(self.rename(second, "CB-3"), "CB-3")
        self.assertEqual(self.rename(first, "CB-1"), "CB-1")  # its own ID is fine

    def test_grounds_may_share_an_id(self):
        self.add(GroundItem(), "GND")
        ground = self.add(GroundItem(), "G2")
        self.assertEqual(self.rename(ground, "GND"), "GND")

    def test_validate_lists_repeated_ids(self):
        self.add(CircuitBreakerItem(), "CB-1")
        self.add(CircuitBreakerItem(), "cb-1")
        self.add(GroundItem(), "GND")
        self.add(GroundItem(), "GND")
        self.add(JunctionItem())
        report = check_circuit(self.scene)
        self.assertFalse(report["ok"])
        self.assertEqual(report["duplicate_ids"], ["CB-1 / cb-1 is shown on 2 components"])
        with patch.object(QMessageBox, "warning") as warning:
            self.window._on_check_circuit_clicked()
        text = warning.call_args.args[2]
        self.assertIn("Repeated IDs", text)
        self.assertIn("CB-1 / cb-1 is shown on 2 components", text)
        self.assertNotIn("GND", text.split("Repeated IDs", 1)[1])
        self.assertIn("repeated ID", self.window.footer_status_label.text())

    def test_paste_never_creates_a_repeated_id(self):
        self.add(CircuitBreakerItem(), "CB-1")
        self.add(CircuitBreakerItem(), "cb-1 (2)")
        for item in self.scene.items():
            item.setSelected(isinstance(item, CircuitBreakerItem) and item.display_id == "CB-1")
        view = self.window.workspace_view
        try:
            self.assertTrue(view.copy_selected())
            self.assertTrue(view.paste_components())
            self.assertTrue(view.paste_components())
        finally:
            APP.clipboard().clear()
        self.assertEqual(check_circuit(self.scene)["duplicate_ids"], [])

    def test_scene_netlists_name_components_by_their_shown_id(self):
        breaker = self.add(CircuitBreakerItem(), "CB-7")
        labels = {c["id"]: c["label"] for c in netlist_from_scene(self.scene)["components"]}
        self.assertEqual(labels[breaker.instance_id], "CB-7")
        # evaluate_scene no longer sees every breaker as "Circuit Breaker".
        self.add(CircuitBreakerItem(), "CB-8")
        report = evaluate_scene(self.scene, zones=[])
        self.assertFalse([w for w in report["warnings"] if "same ID" in w])


if __name__ == "__main__":
    unittest.main()
