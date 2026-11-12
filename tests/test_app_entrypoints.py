"""Keep the installed command and Windows build aligned with the GUI module."""
import ast
import importlib
from importlib.metadata import EntryPoint, distribution
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
