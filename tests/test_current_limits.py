# Checks that implausible currents cannot produce a complete study, that the
# entry fields stop at the per-device limit, and that "-0" is stored as zero.

"""Per-device current limit, readable advisory amounts and negative zero."""
import copy
import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from gft_opto.logic.momentary_calc2 import MAX_DEVICE_CURRENT_A, Load, Scenario, format_amps
from gft_opto.logic.protection_study import (
    configuration_signature, evaluate_zones, topology_signature,
)
from test_protection_study import confirmed, example

APP = QApplication.instance() or QApplication([])
DEMO = Path(__file__).parent / "layouts" / "two_zone_demo.json"


def study_with_trip_current(amps):
    """The confirmed example zone, with cb1's trip coil set to ``amps``."""
    netlist = example()
    zone = confirmed(netlist)
    for component in netlist["components"]:
        if component["id"] == "cb1":
            component["properties"]["trip_coil_1_a"] = amps
    zone["topology_signature"] = topology_signature(netlist)
    zone["configuration_signature"] = configuration_signature(zone)
    return netlist, zone


class StudyLimitTests(unittest.TestCase):
    def test_normal_current_still_completes(self):
        netlist, zone = study_with_trip_current(10)
        self.assertTrue(evaluate_zones(netlist, [zone])["complete"])

    def test_current_at_the_limit_is_accepted(self):
        netlist, zone = study_with_trip_current(MAX_DEVICE_CURRENT_A)
        report = evaluate_zones(netlist, [zone])
        self.assertTrue(report["complete"], report["zones"][0]["warnings"])

    def test_implausible_currents_never_complete(self):
        for amps in (MAX_DEVICE_CURRENT_A + 0.01, 1_000_000, 1e308):
            netlist, zone = study_with_trip_current(amps)
            report = evaluate_zones(netlist, [zone])
            with self.subTest(amps=amps):
                self.assertFalse(report["complete"])
                self.assertEqual(report["supply_peaks"], {})
                warnings = report["zones"][0]["warnings"]
                limit = [w for w in warnings if "limit for one device" in w]
                self.assertEqual(len(limit), 1, warnings)
                self.assertIn("cb1", limit[0])
                self.assertIn("Trip Coil 1", limit[0])
                self.assertLess(len(limit[0]), 200, "the amount must be printed compactly")
                # The bad value is left out rather than added into the totals.
                for scenario in report["zones"][0]["scenarios"]:
                    self.assertLess(scenario["peak_current_A"], 100)

    def test_other_load_above_the_limit_is_flagged(self):
        netlist = example()
        zone = confirmed(netlist)
        zone["actions"].append(dict(
            component="other:1", load="trip_coil_1_a", stage="Primary trip", supply="DC 1",
            operation="device", custom_load=dict(name="Panel", amps=5000)))
        zone["configuration_signature"] = configuration_signature(zone)
        report = evaluate_zones(netlist, [zone])
        self.assertFalse(report["complete"])
        self.assertTrue(any("other:1" in w and "limit for one device" in w
                            for w in report["zones"][0]["warnings"]))


class AdvisoryFormattingTests(unittest.TestCase):
    def test_amounts_are_readable(self):
        self.assertEqual(format_amps(1234.56), "1234.6")
        self.assertEqual(format_amps(999999.94), "999999.9")
        self.assertEqual(format_amps(1e6), "1e+06")
        self.assertEqual(format_amps(1e308), "1e+308")

    def test_huge_peak_advisory_is_short(self):
        scenario = Scenario("huge")
        scenario.add_load(Load("Coil", 1e308))
        warning = scenario.calculate()["warnings"][0]
        self.assertIn("1e+308 A", warning)
        self.assertLess(len(warning), 120)


class EntryFieldTests(unittest.TestCase):
    def setUp(self):
        from gft_opto.gui.commands import AddEquipmentCommand
        from gft_opto.gui.substation_gui import SubstationGuiMockup
        from gft_opto.gui.symbols import CircuitBreakerItem

        self.window = SubstationGuiMockup()
        self.breaker = CircuitBreakerItem()
        self.window.undo_stack.push(AddEquipmentCommand(self.window.workspace_scene, self.breaker))
        self.breaker.setSelected(True)
        APP.processEvents()

    def tearDown(self):
        self.window._saved_snapshot = self.window._layout_data()
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()

    def type_current(self, text):
        field = self.window.prop_trip_coil_1
        field.setText(text)
        field.editingFinished.emit()
        return self.breaker.properties.get("trip_coil_1_a")

    def test_values_above_the_limit_are_refused_with_a_message(self):
        self.assertEqual(self.type_current("12"), 12)
        for text in ("1000.5", "1000000", "1e308", "-3", "abc", "inf"):
            with self.subTest(text=text):
                self.assertEqual(self.type_current(text), 12)
                self.assertIn("from 0 to 1,000 A", self.window.footer_status_label.text())
                self.assertEqual(self.window.prop_trip_coil_1.text(), "12")
        self.assertEqual(self.type_current("1000"), 1000)

    def test_negative_zero_is_stored_as_zero(self):
        value = self.type_current("-0")
        self.assertEqual(value, 0)
        self.assertEqual(math.copysign(1.0, value), 1.0)
        self.assertNotIn("-0", json.dumps(self.window._layout_data()["components"]))

    def test_create_component_current_fields_stop_at_the_limit(self):
        from gft_opto.gui.custom_widget_tool import ComponentDialog

        dialog = ComponentDialog(self.window)
        try:
            for spin in (dialog.trip_coil_1_spin, dialog.trip_coil_2_spin, dialog.close_coil_spin,
                         dialog.motor_inrush_spin, dialog.motor_run_spin):
                self.assertEqual(spin.maximum(), MAX_DEVICE_CURRENT_A)
                spin.setValue(5000)
                self.assertEqual(spin.value(), MAX_DEVICE_CURRENT_A)
            # The kV rating is not a current and keeps its own range.
            self.assertGreater(dialog.rating_spin.maximum(), MAX_DEVICE_CURRENT_A)
        finally:
            dialog.deleteLater()

    def test_other_load_field_stops_at_the_limit(self):
        from gft_opto.gui.zone_dialog import ZoneDialog
        from gft_opto.logic.protection_study import propose_zones

        netlist = example()
        dialog = ZoneDialog(netlist, propose_zones(netlist))
        try:
            dialog.zone_list.setCurrentRow(0)
            dialog.add_action()
            self.assertEqual(dialog.load_rows[0].current.maximum(), MAX_DEVICE_CURRENT_A)
        finally:
            dialog.reject()
            dialog.deleteLater()
            APP.processEvents()


class OldFilesTests(unittest.TestCase):
    def test_a_saved_layout_with_a_huge_current_still_opens_and_is_flagged(self):
        from gft_opto.gui.substation_gui import SubstationGuiMockup

        data = json.loads(DEMO.read_text(encoding="utf-8"))
        breaker = next(c for c in data["components"] if c["symbol"] == "breaker")
        breaker["properties"]["trip_coil_1_a"] = 5000
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(QMessageBox, "warning"), patch.object(QMessageBox, "critical"):
            path = Path(folder) / "huge.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            window = SubstationGuiMockup()
            try:
                self.assertTrue(window.open_layout(str(path)))
                report = evaluate_zones(window._protection_netlist(),
                                        copy.deepcopy(window.protection_zones))
            finally:
                window._saved_snapshot = window._layout_data()
                window.close()
                window.deleteLater()
                APP.processEvents()
        self.assertFalse(report["complete"])
        self.assertTrue(any("limit for one device" in w
                            for zone in report["zones"] for w in zone["warnings"]))


if __name__ == "__main__":
    unittest.main()
