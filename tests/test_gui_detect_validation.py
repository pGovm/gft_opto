"""Real Qt regression for the proposed v2 ZoneDialog.detect() guard.

Requires the application's PySide6 environment. No skip-on-import fallback.
"""
import os
import unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication, QMessageBox
from gft_opto.gui.zone_dialog import ZoneDialog
from gft_opto.logic.protection_study import propose_zones

class GuiDetectValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_detect_with_blank_name_warns_and_preserves_the_draft(self):
        netlist={'components':[{'id':'bus1','type':'bus','properties':{}},
                               {'id':'bus2','type':'bus','properties':{}}],
                 'connections':[]}
        zones=propose_zones(netlist)[:1]
        zones[0]['review_note']='Keep this note'
        dialog=ZoneDialog(netlist,zones)
        try:
            dialog.name.setText('')
            dialog.name.textEdited.emit('')
            with patch.object(QMessageBox,'warning') as warning:
                dialog.detect()
                warning.assert_called_once()
                self.assertIn('zone name',warning.call_args.args[2])
            self.assertEqual(len(dialog.zones),1)
            self.assertEqual(dialog.zones[0]['name'],'')
            self.assertEqual(dialog.zones[0]['review_note'],'Keep this note')
        finally:
            dialog.reject()
            dialog.deleteLater()
            self.app.processEvents()

    def test_shared_bus_region_is_explicitly_flagged_in_zone_editor(self):
        netlist = {
            'components': [
                {'id': 'bus1', 'type': 'bus', 'label': 'Bus 1', 'properties': {}},
                {'id': 'bus2', 'type': 'bus', 'label': 'Bus 2', 'properties': {}},
                {'id': 'ct1', 'type': 'ct', 'properties': {}},
                {'id': 'ct2', 'type': 'ct', 'properties': {}},
                {'id': 'cb1', 'type': 'breaker', 'properties': {'trip_coil_1_a': 10}},
                {'id': 'cb2', 'type': 'breaker', 'properties': {'trip_coil_1_a': 20}},
                {'id': 'g1', 'type': 'ground', 'properties': {}},
                {'id': 'g2', 'type': 'ground', 'properties': {}},
            ],
            'connections': [
                ['bus1', 'right', 'bus2', 'left'],
                ['bus1', 'left', 'ct1', 'left'],
                ['ct1', 'right', 'cb1', 'right'],
                ['cb1', 'left', 'g1', 'node'],
                ['bus2', 'right', 'ct2', 'left'],
                ['ct2', 'right', 'cb2', 'right'],
                ['cb2', 'left', 'g2', 'node'],
            ],
        }
        dialog=ZoneDialog(netlist, propose_zones(netlist))
        try:
            self.assertEqual(dialog.zone_list.count(), 1)
            self.assertIn('multiple buses share this detected region',
                          dialog.guidance.text().lower())
            self.assertIn('Bus 1', dialog.guidance.text())
            self.assertIn('Bus 2', dialog.guidance.text())
            self.assertIn('suggested nearby breakers (not assigned)',
                          dialog.guidance.text().lower())
            self.assertEqual(dialog.actions.rowCount(), 0)
        finally:
            dialog.reject()
            dialog.deleteLater()
            self.app.processEvents()

if __name__=='__main__':
    unittest.main(verbosity=2)
