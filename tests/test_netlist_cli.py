# Checks that exported netlists work with saved zones, that netlist import keeps
# the drawing's IDs, and that the command line writes results where it says.

"""Netlist IDs, netlist import round trips and the command-line calculator."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from gft_opto.logic.momentary_calc2 import evaluate_netlist, netlist_instance_ids
from gft_opto.logic.netlist_manager import build_netlist, export_netlist_json
from gft_opto.logic.protection_study import evaluate_zones

LAYOUTS = Path(__file__).parent / "layouts"
DEMO = LAYOUTS / "two_zone_demo.json"
APP = QApplication.instance() or QApplication(sys.argv)
CLI = [sys.executable, "-m", "gft_opto.logic.momentary_calc2"]


def open_window(path):
    from gft_opto.gui.substation_gui import SubstationGuiMockup

    window = SubstationGuiMockup()
    assert window.open_layout(str(path)), path
    return window


def close_window(window):
    window._saved_snapshot = window._layout_data()
    window.close()
    window.deleteLater()
    APP.processEvents()


class QuietDialogs(unittest.TestCase):
    def setUp(self):
        for name in ("warning", "critical", "information"):
            patcher = patch.object(QMessageBox, name)
            patcher.start()
            self.addCleanup(patcher.stop)


class ExportedNetlistMatchesZonesTests(QuietDialogs):
    def test_exported_ids_name_the_type_then_the_instance(self):
        window = open_window(DEMO)
        try:
            netlist = build_netlist(window.workspace_scene, "Demo")
            expected = sorted(f"{item.equip_type}:{item.instance_id}"
                              for item in window.workspace_scene.items()
                              if hasattr(item, "instance_id") and hasattr(item, "equip_type"))
            self.assertEqual(sorted(c["id"] for c in netlist["components"]), expected)
            self.assertEqual(sorted(netlist_instance_ids(netlist["components"]).values()),
                             sorted(i.split(":", 1)[1] for i in expected))
        finally:
            close_window(window)

    def test_evaluate_netlist_on_an_export_matches_the_gui_for_every_layout(self):
        for path in sorted(LAYOUTS.glob("*.json")):
            window = open_window(path)
            try:
                zones = copy.deepcopy(window.protection_zones)
                gui = evaluate_zones(window._protection_netlist(), zones)
                exported = build_netlist(window.workspace_scene, "Export")
                api = evaluate_netlist(exported, zones)
                with self.subTest(layout=path.name):
                    self.assertEqual(json.dumps(api, sort_keys=True), json.dumps(gui, sort_keys=True))
                    missing = [w for z in api["zones"] for w in z["warnings"] if "missing" in w.lower()]
                    self.assertEqual(missing, [])
            finally:
                close_window(window)

    def test_demo_export_gives_the_documented_currents(self):
        window = open_window(DEMO)
        try:
            report = evaluate_netlist(build_netlist(window.workspace_scene, "Demo"),
                                      window.protection_zones)
        finally:
            close_window(window)
        self.assertTrue(report["complete"])
        self.assertEqual({k: v["peak_current_A"] for k, v in report["supply_peaks"].items()},
                         {"DC 1": 25, "DC 2": 20})

    def test_id_mapping_strips_only_the_exact_type_prefix(self):
        components = [
            {"id": "breaker:breaker_1", "type": "breaker"},
            {"id": "ct:ct_2", "type": "ct"},
            {"id": "bus_1", "type": "bus"},                 # already plain
            {"id": "ct:odd", "type": "breaker"},            # prefix of another type
            {"id": "custom_relay:custom_relay_3", "type": "custom_relay"},
        ]
        self.assertEqual(netlist_instance_ids(components), {
            "breaker:breaker_1": "breaker_1", "ct:ct_2": "ct_2",
            "custom_relay:custom_relay_3": "custom_relay_3",
        })

    def test_id_mapping_is_skipped_when_it_would_merge_two_components(self):
        components = [{"id": "breaker:x", "type": "breaker"}, {"id": "x", "type": "ct"}]
        self.assertEqual(netlist_instance_ids(components), {})

    def test_plain_netlists_and_layouts_are_not_renamed(self):
        # Only build_netlist output (schema_version 1) has the type prefix.
        netlist = {"components": [{"id": "bus:1", "type": "bus", "properties": {}}],
                   "connections": []}
        report = evaluate_netlist(netlist, [])
        self.assertIn("bus:1", report["warnings"][0])


class NetlistImportRoundTripTests(QuietDialogs):
    def test_export_import_export_keeps_the_same_ids(self):
        window = open_window(DEMO)
        try:
            original = {item.instance_id for item in window.workspace_scene.items()
                        if hasattr(item, "instance_id")}
            first = build_netlist(window.workspace_scene, "Demo")
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "demo_netlist.json"
                export_netlist_json(first, path)
                with patch.object(window, "_confirm_discard", return_value=True), patch.object(
                        QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                    self.assertTrue(window.import_netlist_json(str(path)))
            imported = {item.instance_id for item in window.workspace_scene.items()
                        if hasattr(item, "instance_id")}
            self.assertEqual(imported, original)
            second = build_netlist(window.workspace_scene, "Demo")
            self.assertEqual(sorted(c["id"] for c in second["components"]),
                             sorted(c["id"] for c in first["components"]))
        finally:
            close_window(window)


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.layout = self.folder / "demo.json"
        self.layout.write_text(DEMO.read_text(encoding="utf-8"), encoding="utf-8")
        self.elsewhere = self.folder / "working"
        self.elsewhere.mkdir()

    def run_cli(self, *args):
        return subprocess.run([*CLI, *map(str, args)], cwd=self.elsewhere,
                              capture_output=True, text=True, timeout=120)

    def test_results_go_beside_the_input_not_into_the_working_folder(self):
        run = self.run_cli(self.layout)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("COMPLETE", run.stdout)
        self.assertIn("DC 1: 25 A", run.stdout)
        for suffix in (".json", ".csv"):
            result = self.folder / f"demo_results{suffix}"
            self.assertTrue(result.exists(), result)
            self.assertIn(f"Saved {result}", run.stdout)
        self.assertEqual(list(self.elsewhere.iterdir()), [])
        self.assertFalse((self.elsewhere / "momentary_results.json").exists())

    def test_a_second_run_says_it_replaced_the_results(self):
        self.assertEqual(self.run_cli(self.layout).returncode, 0)
        run = self.run_cli(self.layout)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn(f"Replaced {self.folder / 'demo_results.json'}", run.stdout)

    def test_different_inputs_do_not_overwrite_each_other(self):
        other = self.folder / "other.json"
        other.write_text(self.layout.read_text(encoding="utf-8"), encoding="utf-8")
        self.assertEqual(self.run_cli(self.layout).returncode, 0)
        self.assertEqual(self.run_cli(other).returncode, 0)
        self.assertTrue((self.folder / "demo_results.json").exists())
        self.assertTrue((self.folder / "other_results.json").exists())

    def test_output_folder_option(self):
        out = self.folder / "results" / "nested"
        run = self.run_cli(self.layout, "--output-dir", out)
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads((out / "demo_results.json").read_text(encoding="utf-8"))
        self.assertEqual(report["supply_peaks"]["DC 1"]["peak_current_A"], 25)

    def exported_netlist(self):
        window = None
        with patch.object(QMessageBox, "warning"), patch.object(QMessageBox, "critical"):
            try:
                window = open_window(self.layout)
                path = self.folder / "demo_netlist.json"
                export_netlist_json(build_netlist(window.workspace_scene, "Demo"), path)
            finally:
                if window is not None:
                    close_window(window)
        return path

    def test_netlist_without_zones_explains_how_to_continue(self):
        run = self.run_cli(self.exported_netlist())
        self.assertEqual(run.returncode, 1)
        self.assertIn("has no saved protection zones", run.stderr)
        self.assertIn("--zones", run.stderr)
        self.assertNotIn("Traceback", run.stderr)

    def test_netlist_with_zones_from_the_layout_matches_the_layout(self):
        netlist = self.exported_netlist()
        run = self.run_cli(netlist, "--zones", self.layout)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("DC 1: 25 A", run.stdout)
        self.assertIn("DC 2: 20 A", run.stdout)
        from_netlist = json.loads((self.folder / "demo_netlist_results.json").read_text(encoding="utf-8"))
        self.assertEqual(self.run_cli(self.layout).returncode, 0)
        from_layout = json.loads((self.folder / "demo_results.json").read_text(encoding="utf-8"))
        self.assertEqual(from_netlist, from_layout)

    def test_zones_file_without_zones_is_rejected(self):
        empty = self.folder / "empty.json"
        empty.write_text(json.dumps({"zones": []}), encoding="utf-8")
        run = self.run_cli(self.layout, "--zones", empty)
        self.assertEqual(run.returncode, 1)
        self.assertIn("has no saved protection zones", run.stderr)
        broken = self.folder / "broken.json"
        broken.write_text(json.dumps({"zones": "none"}), encoding="utf-8")
        run = self.run_cli(self.layout, "--zones", broken)
        self.assertEqual(run.returncode, 1)
        self.assertIn("must be a saved layout with protection zones", run.stderr)

    def test_bad_files_fail_with_a_message_not_a_traceback(self):
        bad = self.folder / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        for args in ((bad,), (self.folder / "missing.json",)):
            run = self.run_cli(*args)
            with self.subTest(args=args):
                self.assertEqual(run.returncode, 1)
                self.assertTrue(run.stderr.startswith("error: "), run.stderr)
                self.assertNotIn("Traceback", run.stderr)
        self.assertIn("is not a valid JSON file", self.run_cli(bad).stderr)

    def test_usage_text_describes_the_inputs_and_options(self):
        run = self.run_cli("--help")
        self.assertEqual(run.returncode, 0)
        for text in ("saved layout", "--zones", "--output-dir", "netlist"):
            self.assertIn(text, run.stdout)
        run = self.run_cli()
        self.assertEqual(run.returncode, 2)
        self.assertIn("usage: python -m gft_opto.logic.momentary_calc2", run.stderr)


if __name__ == "__main__":
    unittest.main()
