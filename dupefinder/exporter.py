"""Scanresultaten opslaan, teruglezen en exporteren."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path

from . import __version__
from .scanner import DuplicateGroup, FileEntry, ScanResult, ScanStats, format_bytes

FILE_FORMAT = "duplicate-media-finder-scan"
FILE_FORMAT_VERSION = 1


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")


def result_to_dict(result: ScanResult) -> dict:
    return {
        "format": FILE_FORMAT,
        "format_version": FILE_FORMAT_VERSION,
        "app_version": __version__,
        "scanned_at": result.scanned_at,
        "scanned_at_iso": _iso(result.scanned_at),
        "roots": result.roots,
        "cancelled": result.cancelled,
        "stats": {
            "files_seen": result.stats.files_seen,
            "files_considered": result.stats.files_considered,
            "bytes_considered": result.stats.bytes_considered,
            "files_hashed": result.stats.files_hashed,
            "bytes_hashed": result.stats.bytes_hashed,
            "cache_hits": result.stats.cache_hits,
            "duration_seconds": result.stats.duration_seconds,
        },
        "summary": {
            "groups": len(result.groups),
            "duplicate_files": result.duplicate_files,
            "reclaimable_bytes": result.reclaimable_bytes,
            "reclaimable_human": format_bytes(result.reclaimable_bytes),
        },
        "errors": [{"path": path, "message": message} for path, message in result.errors],
        "groups": [
            {
                "digest": group.digest,
                "size": group.size,
                "files": [
                    {"path": f.path, "size": f.size, "mtime_ns": f.mtime_ns} for f in group.files
                ],
            }
            for group in result.sorted_groups()
        ],
    }


def export_json(result: ScanResult, path: str | os.PathLike[str]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result_to_dict(result), indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load_json(path: str | os.PathLike[str]) -> ScanResult:
    """Lees een eerder opgeslagen scan terug.

    Er wordt niet gecontroleerd of de bestanden nog bestaan; dat doet de GUI
    zodat ontbrekende bestanden zichtbaar gemarkeerd kunnen worden.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("format") != FILE_FORMAT:
        raise ValueError("Dit is geen scanbestand van Duplicate Media Finder.")

    stats_data = data.get("stats") or {}
    stats = ScanStats(
        files_seen=int(stats_data.get("files_seen", 0)),
        files_considered=int(stats_data.get("files_considered", 0)),
        bytes_considered=int(stats_data.get("bytes_considered", 0)),
        files_hashed=int(stats_data.get("files_hashed", 0)),
        bytes_hashed=int(stats_data.get("bytes_hashed", 0)),
        cache_hits=int(stats_data.get("cache_hits", 0)),
        duration_seconds=float(stats_data.get("duration_seconds", 0.0)),
    )

    groups: list[DuplicateGroup] = []
    for raw_group in data.get("groups", []):
        files = [
            FileEntry(
                path=str(raw_file["path"]),
                size=int(raw_file.get("size", raw_group.get("size", 0))),
                mtime_ns=int(raw_file.get("mtime_ns", 0)),
            )
            for raw_file in raw_group.get("files", [])
        ]
        if len(files) > 1:
            groups.append(
                DuplicateGroup(
                    digest=str(raw_group.get("digest", "")),
                    size=int(raw_group.get("size", files[0].size)),
                    files=files,
                )
            )

    return ScanResult(
        groups=groups,
        stats=stats,
        errors=[
            (str(e.get("path", "")), str(e.get("message", ""))) for e in data.get("errors", [])
        ],
        roots=[str(r) for r in data.get("roots", [])],
        cancelled=bool(data.get("cancelled", False)),
        scanned_at=float(data.get("scanned_at", 0.0)),
    )


CSV_COLUMNS = [
    "groep",
    "hash",
    "rol",
    "bestand",
    "map",
    "volledig_pad",
    "grootte_bytes",
    "grootte",
    "gewijzigd",
    "type",
]


def export_csv(result: ScanResult, path: str | os.PathLike[str]) -> None:
    """Schrijf een CSV die direct in Excel te openen is (BOM + puntkomma)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(CSV_COLUMNS)
        for index, group in enumerate(result.sorted_groups(), start=1):
            for position, entry in enumerate(group.files):
                writer.writerow(
                    [
                        index,
                        group.digest,
                        "origineel" if position == 0 else "duplicaat",
                        entry.name,
                        entry.folder,
                        entry.path,
                        entry.size,
                        format_bytes(entry.size),
                        _iso(entry.mtime) if entry.mtime_ns else "",
                        entry.kind,
                    ]
                )
