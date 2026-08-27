# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all


pyreadstat_datas, pyreadstat_binaries, pyreadstat_hiddenimports = collect_all("pyreadstat")
pandas_datas, pandas_binaries, pandas_hiddenimports = collect_all("pandas")

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=pyreadstat_binaries + pandas_binaries,
    datas=pyreadstat_datas + pandas_datas + [
        ("assets/SASDataViewer.ico", "assets"),
        (
            "clinical_data_viewer/codegen/sas/templates",
            "clinical_data_viewer/codegen/sas/templates",
        ),
        (
            "clinical_data_viewer/codegen/r/templates",
            "clinical_data_viewer/codegen/r/templates",
        ),
    ],
    hiddenimports=pyreadstat_hiddenimports + pandas_hiddenimports + ["scipy.stats"],
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
    [],
    exclude_binaries=True,
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

bundle = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SASDataViewer",
)
