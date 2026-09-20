"""Thumbnails voor foto's en video's, met schijfcache.

Dit bestand is bewust onafhankelijk van Qt: het levert PNG-bytes op, zodat het
ook zonder GUI te testen is.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import app_data_dir
from .formats import THUMBNAILABLE_IMAGES, VIDEO_EXTENSIONS

_HEIF_REGISTERED = False


def _ensure_pillow():
    """Importeer Pillow en registreer HEIC-ondersteuning indien aanwezig."""
    global _HEIF_REGISTERED
    try:
        from PIL import Image, ImageOps  # type: ignore import-not-found
    except ImportError:
        return None, None
    if not _HEIF_REGISTERED:
        _HEIF_REGISTERED = True
        try:
            import pillow_heif  # type: ignore import-not-found

            pillow_heif.register_heif_opener()
        except Exception:
            pass  # HEIC blijft dan zonder voorbeeld; geen reden om te stoppen
    return Image, ImageOps


def thumbnail_cache_dir() -> Path:
    path = app_data_dir() / "thumbnails"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key(path: Path, size: int, mtime_ns: int, max_dimension: int) -> str:
    raw = f"{os.path.normcase(str(path))}|{size}|{mtime_ns}|{max_dimension}"
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()


def clear_thumbnail_cache() -> None:
    shutil.rmtree(thumbnail_cache_dir(), ignore_errors=True)


# ---------------------------------------------------------------------------
# Video: een frame ophalen
# ---------------------------------------------------------------------------


def _ffmpeg_executable() -> str | None:
    try:
        import imageio_ffmpeg  # type: ignore import-not-found

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    return shutil.which("ffmpeg")


def _video_frame_png(path: Path, max_dimension: int) -> bytes | None:
    """Pak een frame uit een video, eerst via OpenCV, anders via ffmpeg."""
    try:
        import cv2  # type: ignore import-not-found

        capture = cv2.VideoCapture(str(path))
        try:
            if capture.isOpened():
                frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                if frame_count > 20:
                    capture.set(cv2.CAP_PROP_POS_FRAMES, min(frame_count // 10, frame_count - 1))
                ok, frame = capture.read()
                if ok and frame is not None:
                    ok, buffer = cv2.imencode(".png", frame)
                    if ok:
                        return _resize_png(bytes(buffer), max_dimension)
        finally:
            capture.release()
    except Exception:
        pass

    ffmpeg = _ffmpeg_executable()
    if not ffmpeg:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "frame.png"
        command = [
            ffmpeg,
            "-v", "error",
            "-ss", "1",
            "-i", str(path),
            "-frames:v", "1",
            "-vf", f"scale='min({max_dimension},iw)':-1",
            "-y", str(out),
        ]
        creationflags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        try:
            subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                creationflags=creationflags,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if out.exists() and out.stat().st_size:
            return out.read_bytes()
    return None


# ---------------------------------------------------------------------------
# Afbeeldingen
# ---------------------------------------------------------------------------


def _resize_png(data: bytes, max_dimension: int) -> bytes | None:
    Image, ImageOps = _ensure_pillow()
    if Image is None:
        return data
    try:
        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((max_dimension, max_dimension))
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="PNG", optimize=True)
            return buffer.getvalue()
    except Exception:
        return None


def _image_png(path: Path, max_dimension: int) -> bytes | None:
    Image, ImageOps = _ensure_pillow()
    if Image is None:
        return None
    try:
        with Image.open(path) as image:
            image.draft("RGB", (max_dimension * 2, max_dimension * 2))
            image = ImageOps.exif_transpose(image)
            image.thumbnail((max_dimension, max_dimension))
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="PNG", optimize=True)
            return buffer.getvalue()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Publieke API
# ---------------------------------------------------------------------------


def supports_thumbnail(path: str | os.PathLike[str]) -> bool:
    extension = Path(path).suffix.lower()
    if extension in THUMBNAILABLE_IMAGES:
        return True
    if extension in VIDEO_EXTENSIONS:
        return True
    # RAW-formaten: Pillow kan de meeste niet openen, maar proberen mag.
    return True


def thumbnail_png(
    path: str | os.PathLike[str],
    max_dimension: int = 256,
    use_cache: bool = True,
) -> bytes | None:
    """Geef PNG-bytes van een voorbeeldweergave, of ``None`` als dat niet lukt."""
    target = Path(path)
    try:
        stat = target.stat()
    except OSError:
        return None

    cache_file: Path | None = None
    if use_cache:
        key = _cache_key(target, stat.st_size, stat.st_mtime_ns, max_dimension)
        cache_file = thumbnail_cache_dir() / f"{key}.png"
        try:
            if cache_file.exists():
                data = cache_file.read_bytes()
                return data or None
        except OSError:
            cache_file = None

    extension = target.suffix.lower()
    if extension in VIDEO_EXTENSIONS:
        data = _video_frame_png(target, max_dimension)
    else:
        data = _image_png(target, max_dimension)

    if cache_file is not None:
        try:
            # Ook een mislukking bewaren we (als leeg bestand) zodat we niet
            # telkens opnieuw een trage poging doen.
            cache_file.write_bytes(data or b"")
        except OSError:
            pass
    return data
