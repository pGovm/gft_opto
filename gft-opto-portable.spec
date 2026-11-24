# Windows folder bundle: Python and dependencies ship beside the application.
a = Analysis(
    ['src/gft_opto/gui/substation_gui.py'],
    pathex=['src'],
    binaries=[],
    datas=[('src/gft_opto/assets', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GFT OPTO',
    icon='src/gft_opto/assets/gft_icon.ico',
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='GFT OPTO Portable',
)
