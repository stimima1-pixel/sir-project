# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['build/qtcore_stage_flat.py'],
    pathex=['R:/data/servo tunning tool/servo tunning tool/build/qt_clean'],
    binaries=[],
    datas=[],
    hiddenimports=['PySide6.QtCore'],
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
    name='QtCoreStageFlat',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='QtCoreStageFlat',
)
