"""Public and command-line calculations must use explicit protection studies."""
import copy
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from gft_opto.logic import momentary_calc2 as calc
from gft_opto.logic.protection_study import evaluate_zones, configuration_signature, topology_signature
from test_protection_study import example, confirmed


class ConfiguredCalculator(unittest.TestCase):
    def configured(self):
        net = example()
        zone = confirmed(net)
        zone["actions"] += [
            dict(component="cb1", load=key, stage=stage, supply="DC 1", units=1)
            for key, stage in (("close_coil_a", "Reclose"), ("motor_inrush_a", "Motor inrush"),
                               ("motor_run_a", "Motor run"))]
        zone["configuration_signature"] = configuration_signature(zone)
        net["zones"] = [zone]
        return net

    def test_public_api_matches_gui_engine_and_does_not_modify_input(self):
        net = self.configured()
        net["components"].append(dict(id="pt", type="pt", properties=dict(motor_inrush_a=100)))
        net["zones"][0]["topology_signature"] = topology_signature(net)
        original = copy.deepcopy(net)
        report = calc.evaluate_netlist(net)
        self.assertEqual(report, evaluate_zones(net, net["zones"]))
        self.assertEqual(net, original)
        self.assertTrue(report["complete"])
        self.assertEqual(report["supply_peaks"]["DC 1"]["peak_current_A"], 30)
        self.assertEqual({s["stage"]: s["peak_current_A"] for s in report["zones"][0]["scenarios"]},
                         {"Primary trip": 30, "Reclose": 12, "Motor inrush": 15, "Motor run": 3})

    def test_missing_assignments_and_retired_all_load_helpers_fail_clearly(self):
        with self.assertRaisesRegex(ValueError, "zone assignments"):
            calc.evaluate_netlist(example())
        for helper in (calc.loads_from_component, calc.scenario_from_netlist,
                       calc.calculator_from_netlist, calc.calculator_from_netlist_file):
            with self.subTest(helper=helper.__name__), self.assertRaisesRegex(ValueError, "Automatic all-load"):
                helper({})

    def test_empty_configured_study_does_not_invent_actions(self):
        report = calc.evaluate_netlist(example(), zones=[])
        self.assertFalse(report["complete"])
        self.assertEqual(report["supply_peaks"], {})
        self.assertEqual(report["zones"], [])

    def test_saved_layout_and_exported_connections_use_same_study(self):
        net = self.configured()
        expected = calc.evaluate_netlist(net)
        layout = copy.deepcopy(net)
        for component in layout["components"]:
            component["symbol"] = component.pop("type")
        layout["connections"] = [[c["from_id"], c["from_port"], c["to_id"], c["to_port"]]
                                 for c in net["connections"]]
        self.assertEqual(calc.evaluate_netlist(layout), expected)
        exported = copy.deepcopy(net)
        exported["connections"] = [dict(**{"from": dict(component=c["from_id"], port=c["from_port"]),
                                           "to": dict(component=c["to_id"], port=c["to_port"])})
                                   for c in net["connections"]]
        self.assertEqual(calc.evaluate_netlist(exported), expected)

    def test_command_line_exports_stages_and_withholds_draft_supply_peaks(self):
        net = self.configured()
        net["zones"][0]["confirmed"] = False
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "study.json"
            path.write_text(json.dumps(net), encoding="utf-8")
            run = subprocess.run([sys.executable, "-m", "gft_opto.logic.momentary_calc2", str(path)],
                                 cwd=folder, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("INCOMPLETE", run.stdout)
            report = json.loads((Path(folder) / "study_results.json").read_text())
            self.assertFalse(report["complete"])
            self.assertEqual(report["supply_peaks"], {})
            with (Path(folder) / "study_results.csv").open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual({r["stage"] for r in rows}, {"Primary trip", "Reclose", "Motor inrush", "Motor run"})
            self.assertTrue(all(r["study_complete"] == "False" for r in rows))
