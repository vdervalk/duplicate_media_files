"""Scaninstellingen en het bewaren daarvan tussen sessies."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import __app_name__
from .formats import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, normalise_extension

#: Mappen die standaard worden overgeslagen (hoofdletterongevoelig, exacte mapnaam).
DEFAULT_EXCLUDED_NAMES: tuple[str, ...] = (
    "$RECYCLE.BIN",
    "System Volume Information",
    ".git",
    "#recycle",
    "@eaDir",
    "Thumbs",
)


def app_data_dir() -> Path:
    """Map voor instellingen en cache, per platform."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    path = base / __app_name__.replace(" ", "")
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def cache_path() -> Path:
    return app_data_dir() / "hashcache.sqlite3"


@dataclass
class ScanConfig:
    """Alles wat bepaalt welke bestanden meedoen in een scan."""

    roots: list[str] = field(default_factory=list)
    include_images: bool = True
    include_videos: bool = True
    #: Extra extensies die de gebruiker zelf toevoegt (bijv. ``.psd``).
    extra_extensions: list[str] = field(default_factory=list)
    #: Alleen deze extensies scannen; leeg betekent "gebruik de categorieen hierboven".
    only_extensions: list[str] = field(default_factory=list)
    excluded_names: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED_NAMES))
    #: Volledige paden die (met submappen) worden overgeslagen.
    excluded_paths: list[str] = field(default_factory=list)
    min_size_bytes: int = 1
    max_size_bytes: int = 0  # 0 = geen bovengrens
    skip_hidden: bool = True
    follow_symlinks: bool = False
    use_cache: bool = True
    worker_threads: int = 8

    # -- afgeleide informatie -------------------------------------------------

    def active_extensions(self) -> frozenset[str]:
        """De extensieset waar de scan op filtert."""
        if self.only_extensions:
            return frozenset(
                ext for ext in (normalise_extension(e) for e in self.only_extensions) if ext
            )
        extensions: set[str] = set()
        if self.include_images:
            extensions |= IMAGE_EXTENSIONS
        if self.include_videos:
            extensions |= VIDEO_EXTENSIONS
        extensions |= {
            ext for ext in (normalise_extension(e) for e in self.extra_extensions) if ext
        }
        return frozenset(extensions)

    def excluded_name_set(self) -> frozenset[str]:
        return frozenset(name.strip().lower() for name in self.excluded_names if name.strip())

    def excluded_path_list(self) -> list[Path]:
        paths: list[Path] = []
        for raw in self.excluded_paths:
            raw = raw.strip()
            if not raw:
                continue
            try:
                paths.append(Path(raw).resolve())
            except OSError:
                continue
        return paths

    def root_paths(self) -> list[Path]:
        return [Path(r) for r in self.roots if str(r).strip()]

    def validate(self) -> list[str]:
        """Geef een lijst met problemen terug; leeg betekent klaar om te scannen."""
        problems: list[str] = []
        roots = self.root_paths()
        if not roots:
            problems.append("Kies minstens een map om te scannen.")
        for root in roots:
            if not root.is_dir():
                problems.append(f"Map bestaat niet of is geen map: {root}")
        if not self.active_extensions():
            problems.append("Er zijn geen bestandstypes geselecteerd.")
        if self.max_size_bytes and self.max_size_bytes < self.min_size_bytes:
            problems.append("Maximale bestandsgrootte is kleiner dan de minimale.")
        return problems

    # -- opslag ---------------------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ScanConfig":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path | None = None) -> None:
        target = path or settings_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)

    @classmethod
    def load(cls, path: Path | None = None) -> "ScanConfig":
        target = path or settings_path()
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        try:
            return cls.from_dict(data)
        except TypeError:
            return cls()
