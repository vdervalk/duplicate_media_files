import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def media_tree(tmp_path: Path) -> Path:
    """Een mapstructuur met bekende duplicaten.

    - vakantie/strand.jpg == backup/strand_kopie.jpg == backup/nested/strand2.jpg
    - vakantie/film.mp4  == backup/film.mp4
    - vakantie/uniek.png staat alleen
    - notities.txt is geen media en telt niet mee
    """
    payload_image = os.urandom(300_000)
    payload_video = os.urandom(1_200_000)

    (tmp_path / "vakantie").mkdir()
    (tmp_path / "backup" / "nested").mkdir(parents=True)

    (tmp_path / "vakantie" / "strand.jpg").write_bytes(payload_image)
    (tmp_path / "backup" / "strand_kopie.jpg").write_bytes(payload_image)
    (tmp_path / "backup" / "nested" / "strand2.jpg").write_bytes(payload_image)

    (tmp_path / "vakantie" / "film.mp4").write_bytes(payload_video)
    (tmp_path / "backup" / "film.mp4").write_bytes(payload_video)

    (tmp_path / "vakantie" / "uniek.png").write_bytes(os.urandom(120_000))
    (tmp_path / "notities.txt").write_bytes(payload_image)  # zelfde bytes, ander type
    return tmp_path


@pytest.fixture()
def photo_tree(tmp_path: Path) -> Path:
    """Mapje met een scherpe foto, twee onscherpe en een egaal vlak.

    - scherp.jpg      veel fijne randen
    - vaag.jpg        dezelfde foto, flink vervaagd
    - heel_vaag.jpg   nog verder vervaagd
    - lucht.jpg       egale kleur, scherp maar zonder detail
    - bokeh.jpg       vage achtergrond met een scherp middenstuk
    - clip.mp4        geen foto, moet overgeslagen worden
    """
    from PIL import Image, ImageDraw, ImageFilter

    sharp = Image.new("RGB", (1200, 900), (150, 170, 200))
    draw = ImageDraw.Draw(sharp)
    for i in range(24):
        draw.rectangle([50 * i, 80, 50 * i + 16, 820], fill=(40, 60, 40))
        draw.line([(0, 40 * i), (1200, 40 * i)], fill=(230, 230, 210), width=2)

    photos = tmp_path / "fotos"
    photos.mkdir()
    sharp.save(photos / "scherp.jpg", quality=92)
    sharp.filter(ImageFilter.GaussianBlur(5)).save(photos / "vaag.jpg", quality=92)
    sharp.filter(ImageFilter.GaussianBlur(12)).save(photos / "heel_vaag.jpg", quality=92)
    Image.new("RGB", (800, 600), (190, 195, 205)).save(photos / "lucht.jpg", quality=92)

    bokeh = sharp.filter(ImageFilter.GaussianBlur(10)).copy()
    bokeh.paste(sharp.crop((400, 300, 800, 700)), (400, 300))
    bokeh.save(photos / "bokeh.jpg", quality=92)

    (photos / "clip.mp4").write_bytes(b"\x00" * 4096)
    return photos
