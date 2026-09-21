from dupefinder.config import DEFAULT_EXCLUDED_NAMES, ScanConfig
from dupefinder.formats import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, parse_extension_list


def test_default_extensions_cover_images_and_videos():
    extensions = ScanConfig().active_extensions()
    assert ".jpg" in extensions
    assert ".mp4" in extensions
    assert ".heic" in extensions
    assert ".cr3" in extensions
    assert extensions == IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


def test_only_extensions_wins():
    config = ScanConfig(only_extensions=["JPG", "*.mov"])
    assert config.active_extensions() == {".jpg", ".mov"}


def test_extra_extensions_are_added():
    config = ScanConfig(include_videos=False, extra_extensions=["psd"])
    extensions = config.active_extensions()
    assert ".psd" in extensions
    assert ".mp4" not in extensions


def test_validate_reports_missing_root(tmp_path):
    assert "Kies minstens een map om te scannen." in ScanConfig().validate()
    config = ScanConfig(roots=[str(tmp_path / "nergens")])
    assert any("bestaat niet" in problem for problem in config.validate())
    assert ScanConfig(roots=[str(tmp_path)]).validate() == []


def test_validate_rejects_empty_type_selection(tmp_path):
    config = ScanConfig(roots=[str(tmp_path)], include_images=False, include_videos=False)
    assert "Er zijn geen bestandstypes geselecteerd." in config.validate()


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    config = ScanConfig(roots=[str(tmp_path)], min_size_bytes=2048, worker_threads=3)
    config.save(path)

    loaded = ScanConfig.load(path)
    assert loaded.roots == [str(tmp_path)]
    assert loaded.min_size_bytes == 2048
    assert loaded.worker_threads == 3


def test_load_falls_back_on_broken_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("dit is geen json", encoding="utf-8")
    assert ScanConfig.load(path).excluded_names == list(DEFAULT_EXCLUDED_NAMES)


def test_parse_extension_list_normalises():
    assert parse_extension_list("JPG; *.PNG, .mp4") == {".jpg", ".png", ".mp4"}
