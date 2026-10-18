"""Scant mappen op onscherpe foto's.

Hergebruikt het doorlopen van mappen en de voortgangsmeldingen van de
duplicatenscan, maar meet per foto de scherpte in plaats van een hash.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace

from .cache import HashCache
from .config import ScanConfig, cache_path
from .scanner import ProgressCallback, ScanProgress, iter_files
from .formats import normalise_extension
from .sharpness import (
    ANALYSABLE_EXTENSIONS,
    DEFAULT_BLUR_THRESHOLD,
    RAW_EXTENSIONS,
    SharpnessEntry,
    can_analyse,
    measure_file,
)

PHASE_LABELS = {
    "walk": "Foto's verzamelen",
    "measure": "Scherpte meten",
    "done": "Klaar",
}


@dataclass
class SharpnessStats:
    files_seen: int = 0
    measured: int = 0
    cache_hits: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0


@dataclass
class SharpnessResult:
    entries: list[SharpnessEntry] = field(default_factory=list)
    stats: SharpnessStats = field(default_factory=SharpnessStats)
    errors: list[tuple[str, str]] = field(default_factory=list)
    roots: list[str] = field(default_factory=list)
    cancelled: bool = False
    scanned_at: float = field(default_factory=time.time)

    def sorted_entries(self) -> list[SharpnessEntry]:
        """Meest verdachte foto bovenaan."""
        return sorted(self.entries, key=lambda e: (e.score, e.path.lower()))

    def blurry(self, threshold: float = DEFAULT_BLUR_THRESHOLD) -> list[SharpnessEntry]:
        return [entry for entry in self.sorted_entries() if entry.is_blurry(threshold)]

    def low_detail(self) -> list[SharpnessEntry]:
        return [entry for entry in self.sorted_entries() if entry.low_detail]


def photo_config(config: ScanConfig) -> ScanConfig:
    """Dezelfde instellingen, maar alleen foto's: video's kunnen niet onscherp heten."""
    usable = ANALYSABLE_EXTENSIONS | RAW_EXTENSIONS
    photos = replace(config, include_videos=False)
    photos.only_extensions = [
        ext for ext in photos.only_extensions if normalise_extension(ext) in usable
    ]
    photos.extra_extensions = [
        ext for ext in photos.extra_extensions if normalise_extension(ext) in usable
    ]
    return photos


class SharpnessScanner:
    """Meet de scherpte van alle foto's onder de ingestelde mappen."""

    def __init__(
        self,
        config: ScanConfig,
        on_progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        cache: HashCache | None = None,
    ) -> None:
        self.config = photo_config(config)
        self.on_progress = on_progress
        self.cancel_event = cancel_event or threading.Event()
        self._owns_cache = cache is None
        self.cache = cache or HashCache(cache_path(), enabled=config.use_cache)
        self.result = SharpnessResult(roots=[str(r) for r in self.config.root_paths()])
        self._lock = threading.Lock()

    # -- hulpjes -------------------------------------------------------------

    def _cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def _report(self, phase: str, current: int = 0, total: int = 0, message: str = "") -> None:
        if self.on_progress is None:
            return
        self.on_progress(
            ScanProgress(
                phase=phase,
                message=message or PHASE_LABELS.get(phase, phase),
                current=current,
                total=total,
            )
        )

    def _record_error(self, path: str, message: str) -> None:
        with self._lock:
            if len(self.result.errors) < 1000:
                self.result.errors.append((path, message))

    # -- stappen -------------------------------------------------------------

    def _collect(self) -> list:
        candidates = []
        last_report = 0.0
        for entry in iter_files(
            self.config,
            on_error=self._record_error,
            should_cancel=self._cancelled,
        ):
            if not can_analyse(entry.path):
                self.result.stats.skipped += 1
                continue
            candidates.append(entry)
            self.result.stats.files_seen += 1
            now = time.monotonic()
            if now - last_report > 0.15:
                last_report = now
                self._report(
                    "walk",
                    current=len(candidates),
                    message=f"Foto's verzamelen: {len(candidates)} gevonden",
                )
        self._report("walk", current=len(candidates), message=f"{len(candidates)} foto's gevonden")
        return candidates

    def _measure_all(self, candidates: list) -> list[SharpnessEntry]:
        measured: list[SharpnessEntry] = []
        total = len(candidates)
        done = 0
        last_report = 0.0

        def worker(file_entry):
            if self._cancelled():
                return None
            cached = self.cache.get_sharpness(
                file_entry.path, file_entry.size, file_entry.mtime_ns
            )
            if cached is not None:
                score, detail, width, height = cached
                with self._lock:
                    self.result.stats.cache_hits += 1
                return SharpnessEntry(
                    path=file_entry.path,
                    size=file_entry.size,
                    mtime_ns=file_entry.mtime_ns,
                    score=score,
                    detail=detail,
                    width=width,
                    height=height,
                )
            entry = measure_file(file_entry.path)
            if entry is None:
                self._record_error(file_entry.path, "kon de foto niet openen")
                with self._lock:
                    self.result.stats.skipped += 1
                return None
            self.cache.put_sharpness(
                entry.path,
                entry.size,
                entry.mtime_ns,
                entry.score,
                entry.detail,
                entry.width,
                entry.height,
            )
            with self._lock:
                self.result.stats.measured += 1
            return entry

        threads = max(1, min(32, self.config.worker_threads))
        chunk_size = max(threads * 8, 64)
        with ThreadPoolExecutor(max_workers=threads) as pool:
            for start in range(0, total, chunk_size):
                if self._cancelled():
                    break
                for entry in pool.map(worker, candidates[start : start + chunk_size]):
                    done += 1
                    if entry is not None:
                        measured.append(entry)
                    now = time.monotonic()
                    if now - last_report > 0.15:
                        last_report = now
                        self._report("measure", current=done, total=total)
        self._report("measure", current=done, total=total)
        self.cache.commit()
        return measured

    # -- publieke API --------------------------------------------------------

    def run(self) -> SharpnessResult:
        started = time.monotonic()
        try:
            candidates = self._collect()
            if not self._cancelled() and candidates:
                self.result.entries = self._measure_all(candidates)
            self.result.stats.duration_seconds = time.monotonic() - started
            self.result.cancelled = self._cancelled()
            self.result.scanned_at = time.time()
            self._report("done", message="Klaar")
            return self.result
        finally:
            if self._owns_cache:
                self.cache.close()


def scan_sharpness(
    config: ScanConfig,
    on_progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> SharpnessResult:
    """Gemaksfunctie voor scripts en tests."""
    return SharpnessScanner(config, on_progress=on_progress, cancel_event=cancel_event).run()
