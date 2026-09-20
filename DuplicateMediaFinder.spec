# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-recept voor een losse Windows-executable.

Bouwen:  pyinstaller --noconfirm --clean DuplicateMediaFinder.spec
Resultaat: dist/DuplicateMediaFinder.exe
"""

from PyInstaller.utils.hooks import collect_dynamic_libs

binaries = []
try:
    # ffmpeg meebakken zodat videominiaturen ook zonder installatie werken.
    binaries += collect_dynamic_libs("imageio_ffmpeg")
    import imageio_ffmpeg
    binaries += [(imageio_ffmpeg.get_ffmpeg_exe(), "imageio_ffmpeg/binaries")]
except Exception:
    pass

analysis = Analysis(
    ["run.py"],
    pathex=[],
    binaries=binaries,
    datas=[("assets/icon.ico", "assets"), ("assets/icon.png", "assets")],
    hiddenimports=["send2trash"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "pytest",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuick3D",
        "PySide6.Qt3DCore",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtMultimedia",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
    ],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="DuplicateMediaFinder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # geen zwart venster op de achtergrond
    disable_windowed_traceback=False,
    icon="assets/icon.ico",
)
