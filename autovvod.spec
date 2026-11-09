# -*- mode: python ; coding: utf-8 -*-
"""Сборка Windows-приложения «АвтоВвод» одним каталогом (AutoVvod.exe + _internal)."""

import os

from PyInstaller.utils.hooks import collect_all

datas = [
    ("static", "static"),
    ("demo", "demo"),
]
binaries = []
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "clr_loader",
    "pythonnet",
]

for package in ("uvicorn", "fastapi", "starlette", "pydantic", "openpyxl", "multipart", "webview"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    except Exception:  # noqa: BLE001 — пакета может не быть на не-Windows
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# Playwright кладём целиком: node-драйвер + скачанный Chromium (.local-browsers).
try:
    import playwright

    PLAYWRIGHT_DIR = os.path.dirname(playwright.__file__)
    datas.append((PLAYWRIGHT_DIR, "playwright"))
except Exception:  # noqa: BLE001
    pass

a = Analysis(
    ["desktop.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "numpy", "pandas", "matplotlib", "PIL", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AutoVvod",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AutoVvod",
)
