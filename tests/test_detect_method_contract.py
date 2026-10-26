"""Narrow Python-handler tests, not Qt runtime tests.

Extracts the exact detect() body, injecting only a warning recorder in place
of QMessageBox. Uses the real production add_missing_proposals function.
Set ZONE_DIALOG_SOURCE to select the source file under test.
"""
import ast
import copy
import os
from pathlib import Path
import unittest
from gft_opto.logic import protection_study as study

SOURCE=Path(os.environ.get('ZONE_DIALOG_SOURCE', str(Path(study.__file__).resolve().parents[1]/'gui/zone_dialog.py')))

class Context:
    def __init__(self, blank=False):
        self.netlist={'components':[{'id':'bus1','type':'bus','properties':{}},
                                    {'id':'bus2','type':'bus','properties':{}}],
                      'connections':[]}
        self.zones=study.propose_zones(self.netlist)[:1]
        self.zones[0]['review_note']='Preserve the draft'
        self.blank=blank
        self.refreshed=False
    def capture(self):
        if self.blank:
            self.zones[0]['name']=''
    def refresh_list(self):
        self.refreshed=True

class DetectMethodContract(unittest.TestCase):
    def setUp(self):
        self.warnings=[]
        warnings=self.warnings
        class MessageRecorder:
            @staticmethod
            def warning(parent,title,text):
                warnings.append((title,text))
        tree=ast.parse(SOURCE.read_text())
        method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='detect')
        namespace={'add_missing_proposals':study.add_missing_proposals, 'QMessageBox':MessageRecorder}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method],type_ignores=[])),str(SOURCE),'exec'),namespace)
        self.detect=namespace['detect']

    def test_blank_name_reports_a_warning_without_an_escaping_exception(self):
        context=Context(blank=True)
        self.detect(context)
        self.assertEqual(len(self.warnings),1)
        self.assertIn('zone name',self.warnings[0][1])
        self.assertFalse(context.refreshed)

    def test_failed_detection_keeps_the_captured_draft(self):
        context=Context(blank=True)
        context.capture()
        before=copy.deepcopy(context.zones)
        self.detect(context)
        self.assertEqual(context.zones,before)
        self.assertFalse(context.refreshed)
        self.assertEqual(len(self.warnings),1)

    def test_valid_draft_adds_missing_proposals_and_refreshes(self):
        context=Context()
        first=copy.deepcopy(context.zones[0])
        self.detect(context)
        self.assertEqual(context.zones[0],first)
        self.assertEqual(len(context.zones),2)
        self.assertTrue(context.refreshed)
        self.assertFalse(self.warnings)

if __name__=='__main__':
    unittest.main(verbosity=2)
