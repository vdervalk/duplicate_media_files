import pytest

from dupefinder.sharpness import (
    DEFAULT_BLUR_THRESHOLD,
    LOW_DETAIL_STDDEV,
    SharpnessEntry,
    can_analyse,
    measure_file,
)

pytest.importorskip("numpy")
pytest.importorskip("PIL")


def measure(photos, name):
    entry = measure_file(photos / name)
    assert entry is not None, name
    return entry


def test_sharp_photo_scores_far_above_blurred(photo_tree):
    sharp = measure(photo_tree, "scherp.jpg")
    vague = measure(photo_tree, "vaag.jpg")
    very_vague = measure(photo_tree, "heel_vaag.jpg")

    assert sharp.score > vague.score > very_vague.score
    # De afstand moet groot zijn, anders is een drempel niet te kiezen.
    assert sharp.score > vague.score * 10


def test_labels_follow_the_threshold(photo_tree):
    sharp = measure(photo_tree, "scherp.jpg")
    vague = measure(photo_tree, "vaag.jpg")

    assert not sharp.is_blurry(DEFAULT_BLUR_THRESHOLD)
    assert sharp.label(DEFAULT_BLUR_THRESHOLD) == "scherp"
    assert vague.is_blurry(DEFAULT_BLUR_THRESHOLD)
    assert vague.label(DEFAULT_BLUR_THRESHOLD) in {"onscherp", "zwaar onscherp"}


def test_flat_image_is_low_detail_not_blurry(photo_tree):
    flat = measure(photo_tree, "lucht.jpg")

    assert flat.detail < LOW_DETAIL_STDDEV
    assert flat.low_detail
    assert not flat.is_blurry()  # geen onterechte beschuldiging
    assert flat.label() == "weinig detail"


def test_bokeh_is_judged_on_its_sharpest_part(photo_tree):
    bokeh = measure(photo_tree, "bokeh.jpg")
    very_vague = measure(photo_tree, "heel_vaag.jpg")

    # Een scherp onderwerp op een vage achtergrond mag niet als onscherp gelden.
    assert bokeh.score > very_vague.score * 10
    assert not bokeh.is_blurry(DEFAULT_BLUR_THRESHOLD)


def test_dimensions_are_reported(photo_tree):
    sharp = measure(photo_tree, "scherp.jpg")
    assert (sharp.width, sharp.height) == (1200, 900)


def test_unreadable_files_give_none(photo_tree, tmp_path):
    assert measure_file(photo_tree / "clip.mp4") is None
    assert measure_file(tmp_path / "bestaat-niet.jpg") is None


def test_threshold_is_adjustable():
    entry = SharpnessEntry(path="/x.jpg", size=1, mtime_ns=1, score=250.0, detail=40.0)
    assert not entry.is_blurry(100)
    assert entry.is_blurry(400)


@pytest.mark.parametrize(
    "name,expected",
    [("foto.jpg", True), ("foto.HEIC", True), ("foto.cr3", True), ("film.mp4", False)],
)
def test_can_analyse(name, expected):
    assert can_analyse(name) is expected
