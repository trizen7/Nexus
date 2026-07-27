# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path(SPECPATH)
web_root = project_root / "nexus_gateway" / "web"

a = Analysis(
    [str(project_root / "nexus_gateway" / "fnos_launcher.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(web_root / "index.html"), "nexus_gateway/web"),
        (str(web_root / "app.js"), "nexus_gateway/web"),
        (str(web_root / "styles.css"), "nexus_gateway/web"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="nexus-gateway",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory="_internal",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="nexus-gateway",
)
