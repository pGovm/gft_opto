#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv..."
    curl -fsSL https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

uv sync --locked
uv run --locked python -c "import PySide6, pymupdf, gft_opto.gui.subscription_gui"
echo "Setup complete. Start the app with: uv run gft-opto"
