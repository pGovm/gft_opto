"""Transfer buses remain in the circuit without standalone operating zones."""
import json
import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from gft_opto.gui.substation_gui import SubstationGuiMockup
from gft_opto.gui.symbols import DisconnectSwitchItem
from gft_opto.gui.zone_dialog import ZoneDialog
from gft_opto.logic.protection_study import analyze_topology, evaluate_zones, propose_zones
from gft_opto.logic.protection_zones import is_open_switch


class TransferBusExclusions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def fixture(self):
        return dict(components=[
            dict(id="main", type="bus", label="MAIN BUS", properties={}),
            dict(id="transfer", type="bus", label="12 kV TRANSFER BUS", properties={}),
            dict(id="switch", type="disconnect", properties={"status": "Normally Open"})],
            connections=[["main", "right", "switch", "left"],
                         ["switch", "right", "transfer", "left"]])

    def test_transfer_bus_is_not_proposed_or_reported_as_an_uncovered_asset(self):
        netlist = self.fixture()
        zones = propose_zones(netlist)
        self.assertEqual([zone["seed"] for zone in zones], ["main"])
        saved_transfer = dict(id="transfer-event", name="Transfer", kind="Bus", seed="transfer",
                              boundary_cts=[], actions=[], confirmed=False)
        report = evaluate_zones(netlist, zones + [saved_transfer])
        self.assertEqual(len(report["zones"]), 1)
        self.assertFalse(any("transfer" in warning for warning in report["warnings"]))
        self.assertFalse(any("transfer" in region["core"] for region in analyze_topology(netlist)))
        dialog = ZoneDialog(netlist, zones + [saved_transfer])
        self.assertEqual(len(dialog.zones), 1)
        dialog.reject()
        dialog.deleteLater()
        self.app.processEvents()

    def test_normally_open_is_available_and_stored_for_switches(self):
        window = SubstationGuiMockup()
        switch = DisconnectSwitchItem()
        window.workspace_scene.addItem(switch)
        switch.setSelected(True)
        window._on_selection_changed()
        index = window.prop_status.findText("Normally Open")
        self.assertGreaterEqual(index, 0)
        window.prop_status.setCurrentIndex(index)
        self.assertEqual(switch.properties["status"], "Normally Open")
        self.assertTrue(is_open_switch(switch))
        window._saved_snapshot = window._layout_data()
        window.close()
        window.deleteLater()
        self.app.processEvents()

    def test_layouts_have_no_future_equipment_or_dangling_wires(self):
        for path in (Path(__file__).parent / "layouts").glob("*.json"):
            with self.subTest(layout=path.name):
                data = json.loads(path.read_text(encoding="utf-8"))
                ids = {component["id"] for component in data["components"]}
                for component in data["components"]:
                    self.assertNotIn("future", component["name"].lower())
                    self.assertNotIn("future", component["display_id"].lower())
                for wire in data["connections"]:
                    self.assertIn(wire[0], ids)
                    self.assertIn(wire[2], ids)
