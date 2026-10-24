# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: single-file GUI exe that asks for administrator rights."""

from PyInstaller.utils.hooks import collect_dynamic_libs

binaries = collect_dynamic_libs("pyaudiowpatch")

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=binaries,
    datas=[("assets/icon.ico", "assets")],
    hiddenimports=["_portaudiowpatch"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PIL", "numpy", "pandas", "matplotlib", "pytest", "tests"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="WasapiRecorder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon="assets/icon.ico",
    uac_admin=True,
)
