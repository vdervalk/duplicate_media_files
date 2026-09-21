import csv
import json

import pytest

from dupefinder.config import ScanConfig
from dupefinder.exporter import export_csv, export_json, load_json
from dupefinder.scanner import scan


@pytest.fixture()
def result(media_tree):
    return scan(ScanConfig(roots=[str(media_tree)], use_cache=False))


def test_json_roundtrip(result, tmp_path):
    target = tmp_path / "scan.json"
    export_json(result, target)

    loaded = load_json(target)
    assert len(loaded.groups) == len(result.groups)
    assert loaded.reclaimable_bytes == result.reclaimable_bytes
    assert loaded.duplicate_files == result.duplicate_files
    assert {g.digest for g in loaded.groups} == {g.digest for g in result.groups}


def test_json_contains_summary(result, tmp_path):
    target = tmp_path / "scan.json"
    export_json(result, target)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["summary"]["groups"] == len(result.groups)
    assert data["summary"]["duplicate_files"] == result.duplicate_files
    assert data["roots"]


def test_load_rejects_foreign_json(tmp_path):
    target = tmp_path / "iets.json"
    target.write_text('{"hello": "world"}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_json(target)


def test_csv_has_one_row_per_file(result, tmp_path):
    target = tmp_path / "scan.csv"
    export_csv(result, target)

    with target.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle, delimiter=";"))

    header, *data_rows = rows
    assert header[0] == "groep"
    assert len(data_rows) == sum(g.count for g in result.groups)
    roles = [row[2] for row in data_rows]
    assert roles.count("origineel") == len(result.groups)
