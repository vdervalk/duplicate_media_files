"""Bestanden die meegebakken worden in de .exe terugvinden."""

from __future__ import annotations

import sys
from pathlib import Path


def resource_path(relative: str) -> Path:
    """Pad naar een meegeleverd bestand, zowel vanuit broncode als vanuit PyInstaller."""
    bundled = getattr(sys, "_MEIPASS", None)
    base = Path(bundled) if bundled else Path(__file__).resolve().parents[1]
    return base / relative


def icon_path() -> Path | None:
    for name in ("assets/icon.ico", "assets/icon.png"):
        candidate = resource_path(name)
        if candidate.exists():
            return candidate
    return None
