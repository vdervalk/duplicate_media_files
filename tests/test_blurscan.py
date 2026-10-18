import threading

import pytest

from dupefinder.blurscan import photo_config, scan_sharpness
from dupefinder.config import ScanConfig
from dupefinder.sharpness import DEFAULT_BLUR_THRESHOLD

pytest.importorskip("numpy")


def config_for(path, **overrides) -> ScanConfig:
    config = ScanConfig(roots=[str(path)], use_cache=False, worker_threads=4)
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def names(entries):
    return sorted(entry.name for entry in entries)


def test_measures_every_photo_and_skips_video(photo_tree):
    result = scan_sharpness(config_for(photo_tree))

    assert names(result.entries) == [
        "bokeh.jpg",
        "heel_vaag.jpg",
        "lucht.jpg",
        "scherp.jpg",
        "vaag.jpg",
    ]
    assert result.stats.measured == 5


def test_entries_are_sorted_worst_first(photo_tree):
    result = scan_sharpness(config_for(photo_tree))
    scores = [entry.score for entry in result.sorted_entries()]
    assert scores == sorted(scores)
    assert result.sorted_entries()[0].name == "lucht.jpg"


def test_blurry_and_low_detail_are_separated(photo_tree):
    result = scan_sharpness(config_for(photo_tree))

    blurry = names(result.blurry(DEFAULT_BLUR_THRESHOLD))
    assert "vaag.jpg" in blurry
    assert "heel_vaag.jpg" in blurry
    assert "lucht.jpg" not in blurry  # weinig detail, geen onscherpte
    assert "scherp.jpg" not in blurry
    assert names(result.low_detail()) == ["lucht.jpg"]


def test_threshold_changes_the_selection(photo_tree):
    result = scan_sharpness(config_for(photo_tree))

    assert len(result.blurry(1.0)) < len(result.blurry(DEFAULT_BLUR_THRESHOLD))
    assert len(result.blurry(100_000)) >= len(result.blurry(DEFAULT_BLUR_THRESHOLD))


def test_second_scan_uses_the_cache(photo_tree, tmp_path, monkeypatch):
    cache_file = tmp_path / "cache.sqlite3"
    monkeypatch.setattr("dupefinder.blurscan.cache_path", lambda: cache_file)
    config = config_for(photo_tree, use_cache=True)

    first = scan_sharpness(config)
    assert first.stats.measured == 5
    assert first.stats.cache_hits == 0

    second = scan_sharpness(config)
    assert second.stats.measured == 0
    assert second.stats.cache_hits == 5
    assert names(second.entries) == names(first.entries)
    assert [round(e.score, 3) for e in second.sorted_entries()] == [
        round(e.score, 3) for e in first.sorted_entries()
    ]


def test_cancelled_scan_returns_empty(photo_tree):
    cancel = threading.Event()
    cancel.set()
    result = scan_sharpness(config_for(photo_tree), cancel_event=cancel)

    assert result.cancelled is True
    assert result.entries == []


def test_progress_reaches_the_measure_phase(photo_tree):
    phases = []
    scan_sharpness(config_for(photo_tree), on_progress=lambda p: phases.append(p.phase))
    assert "walk" in phases
    assert "measure" in phases
    assert phases[-1] == "done"


def test_photo_config_drops_videos():
    config = ScanConfig(roots=["/x"], extra_extensions=[".psd", ".mp4"])
    photos = photo_config(config)

    assert photos.include_videos is False
    assert ".mp4" not in photos.active_extensions()
    assert ".jpg" in photos.active_extensions()


def test_photo_config_keeps_only_analysable_extensions():
    config = ScanConfig(roots=["/x"], only_extensions=["jpg", "mp4", "cr3"])
    assert photo_config(config).active_extensions() == {".jpg", ".cr3"}


def test_empty_folder(tmp_path):
    result = scan_sharpness(config_for(tmp_path))
    assert result.entries == []
    assert result.blurry() == []
