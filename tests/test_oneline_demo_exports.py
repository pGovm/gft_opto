"""The supplied layouts must demonstrate their saved currents through the GUI."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'windows' if os.name == 'nt' else 'offscreen')
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf
from PySide6.QtWidgets import QApplication, QMessageBox
from gft_opto.gui.substation_gui import SubstationGuiMockup


class OneLineDemoExportTests(unittest.TestCase):
    def test_supplied_layouts_export_assigned_currents_and_stage_totals(self):
        app = QApplication.instance() or QApplication([])
        stems = '''1M-1L01 1P-1L01-Model 1U-1L01_1 1U-1L01-Layout1
        1U-1L01-Model_1 1U-1L01-Model_5 A-089881-Layout1 A-89887-Layout1
        AA-69925-Layout1 AS-1-Layout1 AS-11-Layout1'''.split()
        load_keys = {'trip_coil_1_a', 'trip_coil_2_a', 'close_coil_a',
                     'motor_inrush_a', 'motor_run_a'}
        with tempfile.TemporaryDirectory() as folder, patch.object(
                QMessageBox, 'critical', side_effect=lambda *args: self.fail(str(args[2]))):
            for stem in stems:
                with self.subTest(layout=stem):
                    print('Checking demo export: ' + stem, flush=True)
                    source = Path(__file__).parent / 'layouts' / (stem + '.json')
                    original = source.read_bytes()
                    data = json.loads(original)
                    expected = {(c['id'], key): value for c in data['components']
                                for key, value in c.get('properties', {}).items()
                                if key in load_keys and value > 0}
                    w = SubstationGuiMockup()
                    try:
                        self.assertTrue(w.open_layout(str(source)))
                        w._on_run_evaluation_clicked()
                        self.assertIsNotNone(w.last_result, w.output_box.toPlainText())
                        loads = w.last_result['loads']
                        self.assertTrue(loads)
                        actual = {(row['component'], row['load']): row['amps_per_unit']
                                  for row in loads}
                        self.assertEqual(expected, actual)
                        self.assertFalse(w._zone_report['complete'])
                        self.assertFalse(w._zone_report['supply_peaks'])
                        for zone in w._zone_report['zones']:
                            for stage in zone['scenarios']:
                                self.assertAlmostEqual(stage['peak_current_A'],
                                    sum(row['total_amps'] for row in stage['loads']))
                                self.assertFalse({'motor_run_a', 'motor_inrush_a'} <=
                                                 {row['load'] for row in stage['loads']})
                        target = Path(folder) / (stem + '.pdf')
                        with patch('gft_opto.gui.substation_gui.QFileDialog.getSaveFileName',
                                   return_value=(str(target), '')):
                            w._on_save_results_clicked()
                        with pymupdf.open(target) as document:
                            text = '\n'.join(page.get_text() for page in document)
                        for row in loads:
                            self.assertIn(row['name'], text)
                            self.assertIn(str(row['amps_per_unit']), text)
                        self.assertIn('PROVISIONAL', text)
                        self.assertIn('DEMO', text)
                        self.assertIn('DEMONSTRATION', text)
                        self.assertIn('Motor inrush', text)
                        self.assertIn('Motor run', text)
                        self.assertEqual(source.read_bytes(), original)
                    finally:
                        w._saved_snapshot = w._layout_data()
                        w.close()
                        w.deleteLater()
                        app.processEvents()
