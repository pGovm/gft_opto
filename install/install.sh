#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
root="$(pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}"

if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv..."
    curl -fsSL https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

uv sync --locked
uv run --locked python -c "import PySide6, pymupdf, gft_opto.gui.substation_gui"

# Creating a shortcut icon
QT_QPA_PLATFORM=offscreen uv run --locked python - "$data/icons/hicolor" << 'EOF'

import sys
from pathlib import Path
from PySide6.QtGui import QImageReader

reader = QImageReader("src/gft_opto/assets/gft_icon.ico")

for index in range(reader.imageCount()):
    reader.jumpToImage(index)
    image = reader.read()
    folder = Path(sys.argv[1]) / f"{image.width()}x{image.height()}" / "apps"
    folder.mkdir(parents=True, exist_ok=True)
    image.save(str(folder / "gft-opto.png"))
EOF

mkdir -p "$data/applications"
desktop_file="$data/applications/gft-opto.desktop"
cat > "$desktop_file" <<EOF

[Desktop Entry]
Type=Application
Name=GFT_OPTO
Comment=Substation Momentary DC Load Calculator
Exec="$root/.venv/bin/gft-opto"
Path=$root
Icon=gft-opto
Terminal=false
Categories=Engineering;Science;
StartupWMClass=gft-opto
EOF

chmod +x "$desktop_file"
update-desktop-database "$data/applications" 2>/dev/null || true
gtk-update-icon-cache -q -t "$data/icons/hicolor" 2>/dev/null || true

# Desktop Launcher
desktop_dir="$(xdg-user-dir DESKTOP 2>/dev/null || printf '%s\n' "$HOME/Desktop")"
if [ -d "$desktop_dir" ]; then
    cp "$desktop_file" "$desktop_dir/gft-opto.desktop"
    chmod +x "$desktop_dir/gft-opto.desktop"
    gio set "$desktop_dir/gft-opto.desktop" metadata::trusted true 2>/dev/null || true
fi

echo "Setup complete. Start GFT_OPTO from the applications menu or desktop, or run: uv run gft-opto"
