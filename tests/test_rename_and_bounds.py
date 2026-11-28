# Checks that renaming the project keeps calculated results (with the new name)
# and that layouts with absurd sheet or background coordinates are refused.

"""Project rename keeps results; sheet, background and position limits."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from gft_opto.gui.layout_io import MAX_SHEET_COORDINATE, build_scene

APP = QApplication.instance() or QApplication([])
DEMO = Path(__file__).parent / "layouts" / "two_zone_demo.json"


class WindowTest(unittest.TestCase):
    def setUp(self):
        from gft_opto.gui.substation_gui import SubstationGuiMockup

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.window = SubstationGuiMockup()
        with patch.object(QMessageBox, "warning"), patch.object(QMessageBox, "critical"):
            self.assertTrue(self.window.open_layout(str(DEMO)))

    def tearDown(self):
        self.window._saved_snapshot = self.window._layout_data()
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()


class RenameKeepsResultsTests(WindowTest):
    def evaluate(self):
        self.window._on_run_evaluation_clicked()
        self.assertIsNotNone(self.window.last_result)

    def rename(self, name):
        self.window.project_box.setText(name)
        self.window.project_box.editingFinished.emit()

    def test_rename_keeps_results_and_shows_the_new_name(self):
        self.evaluate()
        before = copy.deepcopy(self.window.last_result["protection_analysis"])
        self.rename("Renamed Substation")
        w = self.window
        self.assertIsNotNone(w.last_result)
        self.assertTrue(w.export_results_action.isEnabled())
        self.assertTrue(w.save_annotated_one_line_pdf_action.isEnabled())
        self.assertEqual(w.last_result["protection_analysis"], before)
        self.assertEqual(w.last_result["scenario_name"], "Renamed Substation")
        self.assertEqual(w.last_netlist["system_name"], "Renamed Substation")
        self.assertTrue(w.output_box.toPlainText().startswith(
            "Momentary DC protection-zone study: Renamed Substation\n"))
        self.assertIn("Renamed Substation *", w.windowTitle())

    def test_results_still_clear_after_a_drawing_edit(self):
        self.evaluate()
        self.rename("Renamed Substation")
        breaker = next(i for i in self.window.workspace_scene.items()
                       if getattr(i, "equip_type", None) == "breaker")
        self.window.workspace_scene.clearSelection()
        breaker.setSelected(True)
        self.window.prop_trip_coil_1.setText("9")
        self.window.prop_trip_coil_1.editingFinished.emit()
        self.assertIsNone(self.window.last_result)
        self.assertFalse(self.window.export_results_action.isEnabled())

    def test_exported_results_use_the_new_name(self):
        self.evaluate()
        self.rename("Renamed Substation")
        path = Path(self.temp.name) / "results.pdf"
        with patch("gft_opto.gui.pdf_export.QFileDialog.getSaveFileName", return_value=(str(path), "")), \
                patch.object(QMessageBox, "information") as notice:
            self.window._on_save_results_clicked()
        notice.assert_not_called()  # "Run an evaluation first" would mean results were lost
        import pymupdf
        with pymupdf.open(path) as document:
            text = "".join(page.get_text() for page in document)
        self.assertIn("Renamed Substation", text)
        self.assertNotIn("Two-zone momentary load demo", text)


class CoordinateLimitTests(WindowTest):
    def layout(self, **changes):
        data = json.loads(DEMO.read_text(encoding="utf-8"))
        data.update(changes)
        return data

    def test_reasonable_values_still_open(self):
        big = MAX_SHEET_COORDINATE / 2
        build_scene(self.layout(scene_rect=[-big, -big, big, big], pdf_bounds=[0, 0, 10000, 7500]))

    def test_absurd_values_are_refused(self):
        cases = {
            "pdf_bounds": [0, 0, 1e300, 10], "pdf_bounds ": [1e300, 0, 10, 10],
            "scene_rect": [0, 0, 1e300, 1e300], "scene_rect ": [-2e6, 0, 100, 100],
        }
        for key, value in cases.items():
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                build_scene(self.layout(**{key.strip(): value}))

    def test_absurd_component_position_is_refused(self):
        data = self.layout()
        data["components"][0]["position"] = [1e300, 0]
        with self.assertRaises(ValueError):
            build_scene(data)

    def test_refused_file_leaves_the_current_drawing_alone(self):
        before = self.window._layout_data()
        path = Path(self.temp.name) / "absurd.json"
        path.write_text(json.dumps(self.layout(pdf_bounds=[0, 0, 1e300, 1e300])), encoding="utf-8")
        with patch.object(QMessageBox, "critical") as error:
            self.assertFalse(self.window.open_layout(str(path)))
        error.assert_called_once()
        self.assertIn("1,000,000", error.call_args.args[2])
        self.assertEqual(self.window._layout_data(), before)


if __name__ == "__main__":
    unittest.main()
