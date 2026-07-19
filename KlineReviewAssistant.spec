# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all


rapid_datas, rapid_binaries, rapid_hidden = collect_all("rapidocr_onnxruntime")

a = Analysis(
    ["kline_recorder_gui.py"],
    pathex=[],
    binaries=rapid_binaries,
    datas=rapid_datas + [("config.default.yaml", "."), ("webui", "webui")],
    hiddenimports=rapid_hidden + ["win32com.client", "pythoncom", "pywintypes"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "pandas", "scipy", "pytest", "onnxruntime.quantization"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KlineReviewAssistant",
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
    icon="app_icon.ico",
    version="version_info.txt",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="KlineReviewAssistant",
)
