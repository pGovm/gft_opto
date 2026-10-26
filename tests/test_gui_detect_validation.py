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

if __name__=='__main__':
    unittest.main(verbosity=2)
