"""Meet hoe scherp een foto is.

De maat is de variantie van de Laplaciaan: een filter dat randen oplicht. Veel
sterke randen betekent veel detail op korte afstand, en dus een scherpe foto.
Een onscherpe foto heeft vloeiende overgangen en dus een lage variantie.

Twee dingen maken het bruikbaar op echte vakantiefoto's:

* De foto wordt in tegels verdeeld en de **scherpste tegel** telt. Zo wordt een
  portret met een bewust vage achtergrond niet als onscherp bestempeld.
* Naast scherpte wordt het contrast gemeten. Een egale lucht of witte muur
  scoort laag zonder onscherp te zijn; die krijgt het label "weinig detail".

De uitkomst is een rangschikking, geen oordeel: de gebruiker beslist aan de
hand van de miniatuur.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .formats import IMAGE_EXTENSIONS, THUMBNAILABLE_IMAGES

#: Langste zijde waarop gemeten wordt. Vast formaat maakt scores vergelijkbaar
#: tussen een 12-megapixelfoto en een 50-megapixelfoto.
ANALYSIS_SIZE = 1000
#: Aantal tegels per as; 4 x 4 = 16 tegels.
TILE_COUNT = 4
#: Standaarddrempel: hieronder heet een foto onscherp. In de interface te schuiven.
DEFAULT_BLUR_THRESHOLD = 100.0
#: Onder dit contrast (standaardafwijking van de grijswaarden) is er te weinig
#: detail om iets zinnigs over scherpte te zeggen.
LOW_DETAIL_STDDEV = 12.0

#: Formaten waarvan we de inhoud rechtstreeks kunnen openen.
ANALYSABLE_EXTENSIONS = THUMBNAILABLE_IMAGES
#: RAW-formaten: daarvoor gebruiken we de ingebedde voorbeeldafbeelding.
RAW_EXTENSIONS = IMAGE_EXTENSIONS - THUMBNAILABLE_IMAGES


@dataclass(frozen=True)
class SharpnessEntry:
    """Meetresultaat voor een enkele foto."""

    path: str
    size: int
    mtime_ns: int
    #: Variantie van de Laplaciaan op de scherpste tegel. Hoger is scherper.
    score: float
    #: Contrast van het hele beeld (standaardafwijking van de grijswaarden).
    detail: float
    width: int = 0
    height: int = 0

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def folder(self) -> str:
        return os.path.dirname(self.path)

    @property
    def low_detail(self) -> bool:
        """Te weinig contrast om over scherpte te oordelen."""
        return self.detail < LOW_DETAIL_STDDEV

    def is_blurry(self, threshold: float = DEFAULT_BLUR_THRESHOLD) -> bool:
        return self.score < threshold and not self.low_detail

    def label(self, threshold: float = DEFAULT_BLUR_THRESHOLD) -> str:
        if self.low_detail:
            return "weinig detail"
        if self.score < threshold / 4:
            return "zwaar onscherp"
        if self.score < threshold:
            return "onscherp"
        return "scherp"


def _numpy():
    try:
        import numpy  # type: ignore import-not-found
    except ImportError:  # pragma: no cover - afhankelijk van omgeving
        return None
    return numpy


def _open_for_analysis(path: Path):
    """Geef een verkleind grijswaardenbeeld terug, of ``None``.

    JPEG's worden met ``draft`` al tijdens het decoderen verkleind, wat een stuk
    sneller is dan de volledige foto uitpakken. Voor RAW-bestanden wordt de
    ingebedde voorbeeld-JPEG gebruikt; die is voor dit doel goed genoeg en
    scheelt het dure uitrekenen van de sensorgegevens.
    """
    from PIL import Image, ImageOps  # type: ignore import-not-found

    extension = path.suffix.lower()
    if extension in RAW_EXTENSIONS:
        data = _raw_preview_bytes(path)
        if data is None:
            return None
        import io

        image = Image.open(io.BytesIO(data))
    else:
        image = Image.open(path)

    try:
        # De afmetingen vastleggen voordat draft() het beeld verkleint.
        full_width, full_height = image.size
        try:
            image.draft("L", (ANALYSIS_SIZE, ANALYSIS_SIZE))
        except (AttributeError, ValueError):
            pass
        transposed = ImageOps.exif_transpose(image)
        gray = transposed.convert("L")
        if transposed is not image:
            transposed.close()
    finally:
        image.close()

    if max(gray.size) > ANALYSIS_SIZE:
        gray.thumbnail((ANALYSIS_SIZE, ANALYSIS_SIZE))
    return gray, full_width, full_height


def _raw_preview_bytes(path: Path) -> bytes | None:
    """Haal de voorbeeld-JPEG uit een RAW-bestand."""
    try:
        import rawpy  # type: ignore import-not-found
    except ImportError:
        return None
    try:
        with rawpy.imread(str(path)) as raw:
            thumb = raw.extract_thumb()
    except Exception:
        return None
    data = getattr(thumb, "data", None)
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    return None


def laplacian_variance(gray_array) -> float:
    """Variantie van de Laplaciaan over een tweedimensionale array."""
    numpy = _numpy()
    if numpy is None:  # pragma: no cover
        return 0.0
    padded = numpy.pad(gray_array, 1, mode="edge")
    # Kernel [[0,1,0],[1,-4,1],[0,1,0]], uitgeschreven zonder convolutiebibliotheek.
    response = (
        padded[:-2, 1:-1]
        + padded[2:, 1:-1]
        + padded[1:-1, :-2]
        + padded[1:-1, 2:]
        - 4.0 * padded[1:-1, 1:-1]
    )
    return float(response.var())


def measure_image(gray) -> tuple[float, float]:
    """Geef ``(scherpte van de scherpste tegel, contrast)`` voor een grijsbeeld."""
    numpy = _numpy()
    if numpy is None:  # pragma: no cover
        return (0.0, 0.0)
    array = numpy.asarray(gray, dtype=numpy.float32)
    if array.ndim != 2 or min(array.shape) < 8:
        return (0.0, float(array.std()) if array.size else 0.0)

    height, width = array.shape
    best = 0.0
    for ty in range(TILE_COUNT):
        for tx in range(TILE_COUNT):
            tile = array[
                ty * height // TILE_COUNT : (ty + 1) * height // TILE_COUNT,
                tx * width // TILE_COUNT : (tx + 1) * width // TILE_COUNT,
            ]
            if min(tile.shape) < 8:
                continue
            best = max(best, laplacian_variance(tile))
    return (best, float(array.std()))


def measure_file(path: str | os.PathLike[str]) -> SharpnessEntry | None:
    """Meet een bestand op schijf. Geeft ``None`` als het niet te openen is."""
    target = Path(path)
    try:
        stat = target.stat()
    except OSError:
        return None
    try:
        opened = _open_for_analysis(target)
    except Exception:
        return None
    if opened is None:
        return None
    gray, width, height = opened
    score, detail = measure_image(gray)
    return SharpnessEntry(
        path=os.path.normpath(str(target)),
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        score=score,
        detail=detail,
        width=width,
        height=height,
    )


def can_analyse(path: str | os.PathLike[str]) -> bool:
    """Of dit bestandstype gemeten kan worden."""
    extension = Path(path).suffix.lower()
    if extension in ANALYSABLE_EXTENSIONS:
        return True
    if extension in RAW_EXTENSIONS:
        return True
    return False
