import os
from pathlib import Path

from dupefinder.config import ScanConfig
from dupefinder.scanner import format_bytes, scan


def config_for(tmp_path: Path, **overrides) -> ScanConfig:
    config = ScanConfig(roots=[str(tmp_path)], use_cache=False, worker_threads=4)
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def group_names(result):
    return sorted(sorted(os.path.basename(f.path) for f in g.files) for g in result.groups)


def test_finds_exact_duplicates(media_tree):
    result = scan(config_for(media_tree))

    assert group_names(result) == [
        ["film.mp4", "film.mp4"],
        ["strand.jpg", "strand2.jpg", "strand_kopie.jpg"],
    ]
    assert result.duplicate_files == 3
    assert result.reclaimable_bytes == 300_000 * 2 + 1_200_000


def test_non_media_files_are_ignored(media_tree):
    result = scan(config_for(media_tree))
    all_paths = [f.path for group in result.groups for f in group.files]
    assert not any(path.endswith(".txt") for path in all_paths)


def test_unique_file_is_not_reported(media_tree):
    result = scan(config_for(media_tree))
    all_names = {os.path.basename(f.path) for group in result.groups for f in group.files}
    assert "uniek.png" not in all_names


def test_only_videos(media_tree):
    result = scan(config_for(media_tree, include_images=False))
    assert group_names(result) == [["film.mp4", "film.mp4"]]


def test_only_extensions_overrides_categories(media_tree):
    result = scan(config_for(media_tree, only_extensions=[".jpg"]))
    assert group_names(result) == [["strand.jpg", "strand2.jpg", "strand_kopie.jpg"]]


def test_minimum_size_filter(media_tree):
    result = scan(config_for(media_tree, min_size_bytes=1_000_000))
    assert group_names(result) == [["film.mp4", "film.mp4"]]


def test_maximum_size_filter(media_tree):
    result = scan(config_for(media_tree, max_size_bytes=500_000))
    assert group_names(result) == [["strand.jpg", "strand2.jpg", "strand_kopie.jpg"]]


def test_excluded_folder_name(media_tree):
    result = scan(config_for(media_tree, excluded_names=["backup"]))
    assert result.groups == []


def test_excluded_path(media_tree):
    result = scan(config_for(media_tree, excluded_paths=[str(media_tree / "backup" / "nested")]))
    assert group_names(result) == [
        ["film.mp4", "film.mp4"],
        ["strand.jpg", "strand_kopie.jpg"],
    ]


def test_same_root_twice_does_not_create_duplicates(media_tree):
    config = config_for(media_tree)
    config.roots = [str(media_tree), str(media_tree)]
    result = scan(config)
    assert group_names(result) == [
        ["film.mp4", "film.mp4"],
        ["strand.jpg", "strand2.jpg", "strand_kopie.jpg"],
    ]


def test_overlapping_roots_do_not_create_duplicates(media_tree):
    config = config_for(media_tree)
    config.roots = [str(media_tree), str(media_tree / "backup")]
    result = scan(config)
    paths = [f.path for group in result.groups for f in group.files]
    assert len(paths) == len(set(os.path.normcase(p) for p in paths))


def test_same_size_different_content_is_not_a_duplicate(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"A" * 200_000)
    (tmp_path / "b.jpg").write_bytes(b"B" * 200_000)
    result = scan(config_for(tmp_path))
    assert result.groups == []


def test_progress_reports_all_phases(media_tree):
    seen = []
    scan(config_for(media_tree), on_progress=lambda p: seen.append(p.phase))
    assert "walk" in seen
    assert "full" in seen
    assert seen[-1] == "done"


def test_cache_speeds_up_second_scan(media_tree, tmp_path, monkeypatch):
    cache_file = tmp_path / "cache.sqlite3"
    monkeypatch.setattr("dupefinder.scanner.cache_path", lambda: cache_file)
    config = config_for(media_tree, use_cache=True)

    first = scan(config)
    assert first.stats.cache_hits == 0
    assert first.stats.files_hashed > 0

    second = scan(config)
    assert second.stats.cache_hits > 0
    assert second.stats.files_hashed == 0
    assert group_names(second) == group_names(first)


def test_unreadable_folder_is_reported_not_fatal(media_tree):
    blocked = media_tree / "geenrechten"
    blocked.mkdir()
    (blocked / "foto.jpg").write_bytes(b"x" * 1000)
    os.chmod(blocked, 0o000)
    try:
        result = scan(config_for(media_tree))
        assert result.groups  # de rest is gewoon gescand
    finally:
        os.chmod(blocked, 0o755)


def test_format_bytes():
    assert format_bytes(0) == "0 B"
    assert format_bytes(1023) == "1023 B"
    assert format_bytes(1024) == "1.0 KB"
    assert format_bytes(1024 ** 3 * 2) == "2.0 GB"


def test_cancelled_scan_stops_early(media_tree):
    import threading

    cancel = threading.Event()
    cancel.set()
    result = scan(config_for(media_tree), cancel_event=cancel)

    assert result.cancelled is True
    assert result.groups == []


def test_scan_of_empty_folder(tmp_path):
    result = scan(config_for(tmp_path))
    assert result.groups == []
    assert result.duplicate_files == 0
    assert result.reclaimable_bytes == 0


def test_zero_byte_files_are_skipped_by_default(tmp_path):
    (tmp_path / "leeg1.jpg").touch()
    (tmp_path / "leeg2.jpg").touch()
    result = scan(config_for(tmp_path))
    assert result.groups == []


def test_zero_byte_files_can_be_included(tmp_path):
    (tmp_path / "leeg1.jpg").touch()
    (tmp_path / "leeg2.jpg").touch()
    result = scan(config_for(tmp_path, min_size_bytes=0))
    assert len(result.groups) == 1
