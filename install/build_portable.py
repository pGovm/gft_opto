"""Build a portable Windows folder and ZIP using the current checkout."""
from datetime import datetime, timezone
from importlib.metadata import distribution
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
LAYOUTS = (
    "1M-1L01", "1P-1L01-Model", "1U-1L01_1", "1U-1L01-Layout1",
    "1U-1L01-Model_1", "1U-1L01-Model_5", "A-089881-Layout1",
    "A-89887-Layout1", "AA-69925-Layout1", "AS-1-Layout1", "AS-11-Layout1",
    "mvec_reference_one_line_draft",
)


def source_build_info(root):
    """Preserve provenance when rebuilding an extracted source archive without Git."""
    try:
        checkout = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
        if Path(checkout).resolve() != root.resolve():
            raise ValueError("Source directory is not the checkout root")
        return {
            "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
            "includes_uncommitted_changes": bool(subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=root, text=True).strip()),
        }
    except (OSError, subprocess.CalledProcessError, ValueError):
        provenance = root / "SOURCE BUILD INFO.json"
        if provenance.is_file():
            data = json.loads(provenance.read_text(encoding="utf-8"))
            return {key: data.get(key) for key in ("commit", "includes_uncommitted_changes")}
        return {"commit": None, "includes_uncommitted_changes": None}


def write_source_archive(target, root=ROOT):
    """Include every repository input needed by the portable build script."""
    paths = [p for p in (root / "src" / "gft_opto").rglob("*")
             if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
    paths += [root / name for name in (
        "pyproject.toml", "uv.lock", "README.md", "LICENSE", "gft-opto-portable.spec")]
    paths += [root / "install" / name for name in ("build_portable.py", "PORTABLE_README.txt")]
    paths += [root / "tests" / "layouts" / f"{stem}.json" for stem in LAYOUTS]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            archive.write(path, path.relative_to(root))
        archive.writestr("SOURCE BUILD INFO.json", json.dumps(source_build_info(root), indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true", help="Package an already built folder")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Build the Windows application on Windows.")
    if not args.skip_build:
        # Keep unrelated DLLs from developer tools out of the portable bundle.
        build_env = os.environ.copy()
        windows = Path(os.environ.get("SystemRoot", "C:/Windows"))
        build_env["PATH"] = os.pathsep.join(map(str, (
            Path(sys.executable).parent, Path(sys.base_prefix),
            windows / "System32", windows,
        )))
        subprocess.run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
                        "gft-opto-portable.spec"], cwd=ROOT, env=build_env, check=True)
    bundle = ROOT / "dist" / "GFT_Analysis_Tool_Portable"
    if not (bundle / "GFT_Analysis_Tool.exe").is_file():
        raise FileNotFoundError("Build the portable application first.")
    layouts = bundle / "One-Line Layouts"
    layouts.mkdir(exist_ok=True)
    for stem in LAYOUTS:
        source = ROOT / "tests" / "layouts" / f"{stem}.json"
        data = json.loads(source.read_bytes())
        if data.get("pdf"):
            raise ValueError(f"{stem} references an external PDF; make its background portable first.")
        shutil.copy2(source, layouts / source.name)
    shutil.copy2(ROOT / "install" / "PORTABLE_README.txt", bundle / "START HERE.txt")
    shutil.copy2(ROOT / "LICENSE", bundle / "LICENSE")
    packages = ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "PyMuPDF")
    notices = []
    for name in packages:
        package = distribution(name)
        notices.append(f"{name} {package.version}\n"
                       f"License: {package.metadata.get('License-Expression') or package.metadata.get('License', 'See package metadata')}\n"
                       f"Project: {package.metadata.get('Home-page', '')}\n")
        target = bundle / "Third-Party Notices" / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "METADATA.txt").write_text(package.read_text("METADATA") or "", encoding="utf-8")
        for entry in package.files or []:
            if any(part in str(entry).lower() for part in ("license", "copying", "notice")):
                source = Path(package.locate_file(entry))
                if source.is_file():
                    shutil.copy2(source, target / source.name)
    (bundle / "Third-Party Notices" / "OVERVIEW.txt").write_text("\n".join(notices), encoding="utf-8")
    write_source_archive(bundle / "Application Source.zip")
    info = {
        "built_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(), "architecture": platform.machine(),
        **source_build_info(ROOT),
        "packages": {name: distribution(name).version for name in packages},
        "layouts": len(LAYOUTS), "signed": False,
    }
    (bundle / "BUILD INFO.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    output = Path(shutil.make_archive(str(ROOT / "dist" / "GFT_Analysis_Tool-Windows-Portable"),
                                     "zip", root_dir=bundle.parent, base_dir=bundle.name))
    with output.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    output.with_suffix(".zip.sha256").write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    print(output)
    print(f"SHA256: {digest}")


if __name__ == "__main__":
    main()
