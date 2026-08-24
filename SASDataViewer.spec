# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all


pyreadstat_datas, pyreadstat_binaries, pyreadstat_hiddenimports = collect_all("pyreadstat")

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=pyreadstat_binaries,
    datas=pyreadstat_datas + [("assets/SASDataViewer.ico", "assets")],
    hiddenimports=pyreadstat_hiddenimports + ["scipy.stats"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "notebook", "IPython"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SASDataViewer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["assets/SASDataViewer.ico"],
)
