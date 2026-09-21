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
