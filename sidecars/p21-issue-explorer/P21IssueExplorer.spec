# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

sidecar_root = Path(SPECPATH).resolve()
repository_root = sidecar_root.parents[1]

a = Analysis(
    [str(sidecar_root / "run_p21.py")],
    pathex=[str(sidecar_root / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
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
    name="P21IssueExplorer",
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
    icon=[str(repository_root / "assets" / "p21-issue-explorer.ico")],
)

bundle = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="P21IssueExplorer",
)
