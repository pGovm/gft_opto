"""The source shipped with the portable application can be rebuilt outside Git."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("portable_builder", ROOT / "install/build_portable.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class PortableSource(unittest.TestCase):
    def test_source_archive_contains_rebuild_inputs_and_can_be_rearchived_without_git(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            target = folder / "source.zip"
            builder.write_source_archive(target)
            with zipfile.ZipFile(target) as archive:
                self.assertIsNone(archive.testzip())
                archive.extractall(folder / "extracted")
            extracted = folder / "extracted"
            for path in ("LICENSE", "pyproject.toml", "uv.lock", "README.md", "gft-opto-portable.spec",
                         "install/build_portable.py", "install/PORTABLE_README.txt",
                         "src/gft_opto/assets/gft_icon.ico", "src/gft_opto/gui/substation_gui.py"):
                self.assertTrue((extracted / path).is_file(), path)
            for stem in builder.LAYOUTS:
                path = Path("tests/layouts") / (stem + ".json")
                self.assertEqual((extracted / path).read_bytes(), (ROOT / path).read_bytes())
            provenance = json.loads((extracted / "SOURCE BUILD INFO.json").read_text())
            with patch.object(builder.subprocess, "check_output", side_effect=FileNotFoundError):
                self.assertEqual(builder.source_build_info(extracted), provenance)
                builder.write_source_archive(folder / "rebuilt.zip", root=extracted)
            self.assertTrue((folder / "rebuilt.zip").is_file())

    def test_plain_source_without_git_or_provenance_is_supported(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
                builder.subprocess, "check_output", side_effect=FileNotFoundError):
            self.assertEqual(builder.source_build_info(Path(folder)),
                             dict(commit=None, includes_uncommitted_changes=None))
