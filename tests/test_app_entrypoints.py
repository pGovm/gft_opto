"""Keep the installed command, install scripts and Windows build aligned with the GUI module."""
import ast
import importlib
import importlib.util
from importlib.metadata import EntryPoint, distribution
from pathlib import Path
import re
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AppEntrypoints(unittest.TestCase):
    def setUp(self):
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.target = config["project"]["scripts"]["gft-opto"]
        self.entry = EntryPoint(name="gft-opto", value=self.target, group="console_scripts")

    def test_configured_command_imports_a_callable(self):
        self.assertTrue(callable(self.entry.load()))

    def test_installed_launcher_matches_the_project_configuration(self):
        installed = [e for e in distribution("gft-opto").entry_points
                     if e.group == "console_scripts" and e.name == "gft-opto"]
        self.assertEqual(len(installed), 1)
        self.assertEqual(installed[0].value, self.target)
        self.assertIs(installed[0].load(), self.entry.load())

    def test_windows_build_targets_the_same_existing_gui_module(self):
        tree = ast.parse((ROOT / "gft-opto.spec").read_text(encoding="utf-8"))
        analysis = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Name) and n.func.id == "Analysis")
        scripts = ast.literal_eval(analysis.args[0])
        self.assertEqual(len(scripts), 1)
        module = importlib.import_module(self.target.split(":", 1)[0])
        self.assertTrue((ROOT / scripts[0]).is_file())
        self.assertEqual((ROOT / scripts[0]).resolve(), Path(module.__file__).resolve())

    def test_install_scripts_and_shortcut_use_the_same_gui_module(self):
        module = self.target.split(":", 1)[0]
        install = ROOT / "install"
        found = {name: re.findall(r"gft_opto(?:\.\w+)+", (install / name).read_text(encoding="utf-8"))
                 for name in ("install.sh", "install.ps1")}
        self.assertTrue(found["install.sh"], "install.sh no longer checks that the app imports")
        self.assertGreaterEqual(len(found["install.ps1"]), 2, "install.ps1 import check or shortcut missing")
        # Every module the installers import or launch must exist and be the
        # one the gft-opto command starts.
        for name, modules in found.items():
            for referenced in modules:
                with self.subTest(script=name, module=referenced):
                    self.assertIsNotNone(importlib.util.find_spec(referenced), f"{referenced} does not exist")
                    self.assertEqual(referenced, module)
        self.assertIn(f"-m {module}", (install / "install.ps1").read_text(encoding="utf-8"))

    def test_windows_installer_runs_a_script_that_exists(self):
        text = (ROOT / "install" / "install.bat").read_text(encoding="utf-8")
        scripts = re.findall(r"%~dp0([\w.-]+\.ps1)", text)
        self.assertEqual(len(scripts), 1, "install.bat should run exactly one PowerShell script")
        self.assertTrue((ROOT / "install" / scripts[0]).is_file(), f"{scripts[0]} is missing")


if __name__ == "__main__":
    unittest.main()
