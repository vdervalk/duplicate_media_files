"""Ondersteunde mediaformaten.

Extensies worden altijd kleingeschreven opgeslagen, inclusief de punt.
"""

from __future__ import annotations

IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {
        # gangbare bitmapformaten
        ".jpg",
        ".jpeg",
        ".jpe",
        ".jfif",
        ".png",
        ".gif",
        ".bmp",
        ".tif",
        ".tiff",
        ".webp",
        ".heic",
        ".heif",
        ".avif",
        ".ico",
        # camera-raw
        ".cr2",
        ".cr3",
        ".crw",
        ".nef",
        ".nrw",
        ".arw",
        ".srf",
        ".sr2",
        ".dng",
        ".orf",
        ".rw2",
        ".raf",
        ".pef",
        ".raw",
        ".3fr",
        ".erf",
        ".kdc",
        ".mrw",
        ".x3f",
    }
)

VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".mp4",
        ".m4v",
        ".mov",
        ".qt",
        ".avi",
        ".mkv",
        ".webm",
        ".wmv",
        ".asf",
        ".flv",
        ".f4v",
        ".mpg",
        ".mpeg",
        ".mpe",
        ".m2v",
        ".mts",
        ".m2ts",
        ".ts",
        ".vob",
        ".3gp",
        ".3g2",
        ".mxf",
        ".ogv",
        ".rm",
        ".rmvb",
        ".divx",
    }
)

MEDIA_EXTENSIONS: frozenset[str] = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

#: Extensies waarvoor Pillow (met pillow-heif) een thumbnail kan maken.
THUMBNAILABLE_IMAGES: frozenset[str] = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".jpe",
        ".jfif",
        ".png",
        ".gif",
        ".bmp",
        ".tif",
        ".tiff",
        ".webp",
        ".ico",
        ".heic",
        ".heif",
        ".avif",
    }
)


def normalise_extension(value: str) -> str:
    """Maak van gebruikersinvoer als ``JPG`` of ``*.jpg`` een nette ``.jpg``."""
    value = value.strip().lower().lstrip("*")
    if not value:
        return ""
    if not value.startswith("."):
        value = "." + value
    return value


def parse_extension_list(value: str) -> set[str]:
    """Splits een door komma's, spaties of puntkomma's gescheiden lijst."""
    parts = value.replace(";", ",").replace(" ", ",").split(",")
    return {ext for ext in (normalise_extension(p) for p in parts) if ext}


def kind_of(extension: str) -> str:
    """Geef ``image``, ``video`` of ``other`` terug voor een extensie."""
    extension = extension.lower()
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in VIDEO_EXTENSIONS:
        return "video"
    return "other"
