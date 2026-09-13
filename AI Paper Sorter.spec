# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

# Maintained package hooks collect CustomTkinter fonts/themes, messagebox
# images, Word templates, and only this platform's TkDnD libraries. collect_all
# also pulled in SDK tests, notebook tools, and resources for other platforms.
datas = [('assets/Icon.ico', 'assets'), ('assets/Icon.png', 'assets')]
datas += [(str(path), 'assets/icons') for path in Path('assets/icons').glob('*.png')]

a = Analysis(
    ['src\\main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'IPython', 'pytest'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    # Keeping dependencies beside the executable avoids unpacking the complete
    # runtime into a new temporary directory on every GUI and watcher launch.
    exclude_binaries=True,
    name='AI Paper Sorter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    version='version_info.txt',
    entitlements_file=None,
    icon=['assets\\Icon.ico'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='AI Paper Sorter',
)
