# install.ps1 - sets up GFT_OPTO on Windows

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

function Assert-LastExit($step) {
    if ($LASTEXITCODE -ne 0) { throw "$step failed (exit code $LASTEXITCODE)." }
}

Write-Host "== GFT_OPTO setup ==" -Foreground Cyan

# 1. uv: the package manager. It also downloads Python if none is installed.
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..."
    powershell -NoProfile -ExecutionPolicy Bypass -Command `
        "[Net.ServicePointManager]::SecurityProtocol = 'Tls12'; irm https://astral.sh/uv/install.ps1 | iex"
    Assert-LastExit "uv install"
    # The installer updates PATH for new windows only; add it to this one too.
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "uv was installed but is not on PATH. Close this window and run the installer again."
    }
}
Write-Host "Using $(uv --version)"

# 2. Dependencies: creates .venv with the exact version from uv.lock
Write-Host "Installing Python and dependencies (the first run can take a few minutes)..."
uv sync --locked
Assert-LastExit "Dependency install"

#3. Check that the app and its libraries import.
uv run --locked python -c "import PySide6, pymupdf, gft_opto.gui.substation_gui"
Assert-LastExit "Import check"

#4. Desktop shortcut. pythonw.exe starts the app without a console window
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'GFT_OPTO.lnk'))
$shortcut.TargetPath        = Join-Path $root '.venv\Scripts\pythonw.exe'
$shortcut.Arguments         = '-m gft_opto.gui.substation_gui'
$shortcut.WorkingDirectory  = $root
$shortcut.Description       = 'Substation Momentary DC Load Calculator'
$shortcut.IconLocation      = Join-Path $root 'src\gft_opto\assets\gft_icon.ico'
$shortcut.Save()

Write-Host ""
Write-Host "Setup Complete!" -ForegroundColor Green
Write-Host "Start the app from the 'GFT_OPTO' desktop shortcut, or run: uv run gft-opto"