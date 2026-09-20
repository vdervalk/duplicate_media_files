"""Doorzoekt mappen en groepeert byte-identieke mediabestanden."""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator

from .cache import HashCache
from .config import ScanConfig, cache_path
from .formats import kind_of
from .hashing import HashCancelled, full_hash, partial_hash

# ---------------------------------------------------------------------------
# Datamodel
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileEntry:
    """Een bestand zoals aangetroffen tijdens het scannen."""

    path: str
    size: int
    mtime_ns: int

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def folder(self) -> str:
        return os.path.dirname(self.path)

    @property
    def extension(self) -> str:
        return os.path.splitext(self.path)[1].lower()

    @property
    def kind(self) -> str:
        return kind_of(self.extension)

    @property
    def mtime(self) -> float:
        return self.mtime_ns / 1_000_000_000


@dataclass
class DuplicateGroup:
    """Bestanden met een identieke SHA-256 en bestandsgrootte."""

    digest: str
    size: int
    files: list[FileEntry]

    @property
    def count(self) -> int:
        return len(self.files)

    @property
    def wasted_bytes(self) -> int:
        """Ruimte die vrijkomt als er van deze groep er een overblijft."""
        return self.size * max(0, self.count - 1)

    def sorted_files(self) -> list[FileEntry]:
        """Oudste bestand eerst; dat is doorgaans het origineel."""
        return sorted(self.files, key=lambda f: (f.mtime_ns, f.path.lower()))


@dataclass
class ScanStats:
    files_seen: int = 0
    files_considered: int = 0
    bytes_considered: int = 0
    files_hashed: int = 0
    bytes_hashed: int = 0
    cache_hits: int = 0
    duration_seconds: float = 0.0

    @property
    def cache_hit_ratio(self) -> float:
        total = self.files_hashed + self.cache_hits
        return self.cache_hits / total if total else 0.0


@dataclass
class ScanResult:
    groups: list[DuplicateGroup] = field(default_factory=list)
    stats: ScanStats = field(default_factory=ScanStats)
    errors: list[tuple[str, str]] = field(default_factory=list)
    roots: list[str] = field(default_factory=list)
    cancelled: bool = False
    scanned_at: float = field(default_factory=time.time)

    @property
    def duplicate_files(self) -> int:
        """Aantal bestanden dat weg kan (per groep blijft er een staan)."""
        return sum(g.count - 1 for g in self.groups)

    @property
    def reclaimable_bytes(self) -> int:
        return sum(g.wasted_bytes for g in self.groups)

    def sorted_groups(self) -> list[DuplicateGroup]:
        """Grootste besparing bovenaan."""
        return sorted(self.groups, key=lambda g: (-g.wasted_bytes, -g.count, g.digest))


@dataclass
class ScanProgress:
    """Voortgangsbericht voor de gebruikersinterface."""

    phase: str  # walk | size | partial | full | done
    message: str = ""
    current: int = 0
    total: int = 0

    @property
    def fraction(self) -> float:
        return (self.current / self.total) if self.total else 0.0


ProgressCallback = Callable[[ScanProgress], None]

PHASE_LABELS = {
    "walk": "Mappen doorzoeken",
    "size": "Groeperen op bestandsgrootte",
    "partial": "Snelle vergelijking (kop/staart)",
    "full": "Volledige vergelijking (SHA-256)",
    "done": "Klaar",
}


# ---------------------------------------------------------------------------
# Bestanden verzamelen
# ---------------------------------------------------------------------------


def _is_hidden(entry_path: str, name: str) -> bool:
    if name.startswith("."):
        return True
    if os.name == "nt":
        try:
            attrs = os.stat(entry_path, follow_symlinks=False).st_file_attributes  # type: ignore[attr-defined]
        except (OSError, AttributeError):
            return False
        hidden = 0x2
        system = 0x4
        return bool(attrs & (hidden | system))
    return False


def iter_files(
    config: ScanConfig,
    on_error: Callable[[str, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_seen: Callable[[str], None] | None = None,
) -> Iterator[FileEntry]:
    """Loop recursief door alle ingestelde mappen en lever passende bestanden."""
    extensions = config.active_extensions()
    excluded_names = config.excluded_name_set()
    excluded_paths = config.excluded_path_list()
    min_size = max(0, config.min_size_bytes)
    max_size = config.max_size_bytes or 0
    visited_dirs: set[tuple[int, int]] = set()
    seen_files: set[str] = set()

    def is_excluded_dir(path: Path) -> bool:
        if path.name.lower() in excluded_names:
            return True
        for excluded in excluded_paths:
            try:
                if path == excluded or excluded in path.parents:
                    return True
            except OSError:
                continue
        return False

    for root in config.root_paths():
        if should_cancel and should_cancel():
            return
        stack: list[Path] = [root]
        while stack:
            if should_cancel and should_cancel():
                return
            directory = stack.pop()
            try:
                scandir_iter = os.scandir(directory)
            except OSError as exc:
                if on_error:
                    on_error(str(directory), str(exc))
                continue
            with scandir_iter as entries:
                while True:
                    try:
                        entry = next(entries)
                    except StopIteration:
                        break
                    except OSError as exc:
                        if on_error:
                            on_error(str(directory), str(exc))
                        break
                    if should_cancel and should_cancel():
                        return
                    try:
                        if entry.is_dir(follow_symlinks=config.follow_symlinks):
                            child = Path(entry.path)
                            if is_excluded_dir(child):
                                continue
                            if config.skip_hidden and _is_hidden(entry.path, entry.name):
                                continue
                            if config.follow_symlinks:
                                # Voorkom oneindige lussen via symlinks/junctions.
                                stat = entry.stat()
                                key = (stat.st_dev, stat.st_ino)
                                if key in visited_dirs:
                                    continue
                                visited_dirs.add(key)
                            stack.append(child)
                            continue
                        if not entry.is_file(follow_symlinks=config.follow_symlinks):
                            continue
                        if on_seen:
                            on_seen(entry.path)
                        extension = os.path.splitext(entry.name)[1].lower()
                        if extension not in extensions:
                            continue
                        if config.skip_hidden and _is_hidden(entry.path, entry.name):
                            continue
                        stat = entry.stat(follow_symlinks=config.follow_symlinks)
                        if stat.st_size < min_size:
                            continue
                        if max_size and stat.st_size > max_size:
                            continue
                        # Dezelfde map twee keer opgegeven mag geen "duplicaat" opleveren.
                        real = os.path.realpath(entry.path)
                        key = os.path.normcase(real)
                        if key in seen_files:
                            continue
                        seen_files.add(key)
                        yield FileEntry(
                            path=os.path.normpath(entry.path),
                            size=stat.st_size,
                            mtime_ns=stat.st_mtime_ns,
                        )
                    except OSError as exc:
                        if on_error:
                            on_error(entry.path, str(exc))
                        continue


# ---------------------------------------------------------------------------
# De scan zelf
# ---------------------------------------------------------------------------


class DuplicateScanner:
    """Voert een scan uit in drie trappen en rapporteert voortgang.

    De scanner is bedoeld om vanuit een achtergrondthread te draaien; annuleren
    gebeurt via een ``threading.Event``.
    """

    def __init__(
        self,
        config: ScanConfig,
        on_progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        cache: HashCache | None = None,
    ) -> None:
        self.config = config
        self.on_progress = on_progress
        self.cancel_event = cancel_event or threading.Event()
        self._owns_cache = cache is None
        self.cache = cache or HashCache(cache_path(), enabled=config.use_cache)
        self.result = ScanResult(roots=[str(r) for r in config.root_paths()])
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

    def _bump_seen(self) -> None:
        self.result.stats.files_seen += 1

    def _record_error(self, path: str, message: str) -> None:
        with self._lock:
            if len(self.result.errors) < 1000:
                self.result.errors.append((path, message))

    # -- trappen -------------------------------------------------------------

    def _collect(self) -> dict[int, list[FileEntry]]:
        by_size: dict[int, list[FileEntry]] = defaultdict(list)
        stats = self.result.stats
        last_report = 0.0
        for entry in iter_files(
            self.config,
            on_error=self._record_error,
            should_cancel=self._cancelled,
            on_seen=lambda _p: self._bump_seen(),
        ):
            stats.files_considered += 1
            stats.bytes_considered += entry.size
            by_size[entry.size].append(entry)
            now = time.monotonic()
            if now - last_report > 0.15:
                last_report = now
                self._report(
                    "walk",
                    current=stats.files_considered,
                    message=f"Mappen doorzoeken: {stats.files_considered} mediabestanden gevonden",
                )
        self._report(
            "walk",
            current=stats.files_considered,
            message=f"{stats.files_considered} mediabestanden gevonden",
        )
        return by_size

    def _hash_stage(
        self,
        candidates: list[FileEntry],
        stage: str,
    ) -> dict[str, list[FileEntry]]:
        """Hash een lijst bestanden parallel en groepeer op de uitkomst."""
        buckets: dict[str, list[FileEntry]] = defaultdict(list)
        total = len(candidates)
        done = 0
        last_report = 0.0
        use_full = stage == "full"

        def worker(entry: FileEntry) -> tuple[FileEntry, str | None]:
            if self._cancelled():
                return (entry, None)
            cached_partial, cached_full = self.cache.get(entry.path, entry.size, entry.mtime_ns)
            cached = cached_full if use_full else cached_partial
            if cached:
                with self._lock:
                    self.result.stats.cache_hits += 1
                return (entry, cached)
            try:
                path = Path(entry.path)
                if use_full:
                    digest = full_hash(path, should_cancel=self._cancelled)
                    self.cache.put(entry.path, entry.size, entry.mtime_ns, full=digest)
                else:
                    digest = partial_hash(path, entry.size)
                    self.cache.put(entry.path, entry.size, entry.mtime_ns, partial=digest)
                with self._lock:
                    self.result.stats.files_hashed += 1
                    self.result.stats.bytes_hashed += entry.size
                return (entry, digest)
            except HashCancelled:
                return (entry, None)
            except (OSError, ValueError) as exc:
                self._record_error(entry.path, str(exc))
                return (entry, None)

        threads = max(1, min(32, self.config.worker_threads))
        # In blokken werken houdt het geheugengebruik laag bij honderdduizenden
        # bestanden: er staan nooit meer dan CHUNK opdrachten tegelijk klaar.
        chunk_size = max(threads * 32, 256)
        with ThreadPoolExecutor(max_workers=threads) as pool:
            for start in range(0, total, chunk_size):
                if self._cancelled():
                    break
                for entry, digest in pool.map(worker, candidates[start : start + chunk_size]):
                    done += 1
                    if digest is not None:
                        buckets[digest].append(entry)
                    now = time.monotonic()
                    if now - last_report > 0.15:
                        last_report = now
                        self._report(stage, current=done, total=total)
        self._report(stage, current=done, total=total)
        self.cache.commit()
        return buckets

    # -- publieke API --------------------------------------------------------

    def run(self) -> ScanResult:
        started = time.monotonic()
        try:
            by_size = self._collect()
            if self._cancelled():
                return self._finish(started)

            self._report("size", message="Groeperen op bestandsgrootte")
            size_candidates = [
                entry for entries in by_size.values() if len(entries) > 1 for entry in entries
            ]
            if not size_candidates:
                return self._finish(started)

            partial_buckets = self._hash_stage(size_candidates, "partial")
            if self._cancelled():
                return self._finish(started)

            full_candidates = [
                entry for entries in partial_buckets.values() if len(entries) > 1 for entry in entries
            ]
            if not full_candidates:
                return self._finish(started)

            full_buckets = self._hash_stage(full_candidates, "full")
            groups = [
                DuplicateGroup(digest=digest, size=entries[0].size, files=list(entries))
                for digest, entries in full_buckets.items()
                if len(entries) > 1
            ]
            for group in groups:
                group.files = group.sorted_files()
            self.result.groups = groups
            return self._finish(started)
        finally:
            if self._owns_cache:
                self.cache.close()

    def _finish(self, started: float) -> ScanResult:
        self.result.stats.duration_seconds = time.monotonic() - started
        self.result.cancelled = self._cancelled()
        self.result.scanned_at = time.time()
        self._report("done", message="Klaar")
        return self.result


def scan(
    config: ScanConfig,
    on_progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> ScanResult:
    """Gemaksfunctie voor scripts en tests."""
    return DuplicateScanner(config, on_progress=on_progress, cancel_event=cancel_event).run()


def format_bytes(value: float) -> str:
    """Menselijk leesbare bestandsgrootte (1 KB = 1024 bytes)."""
    units: Iterable[str] = ("B", "KB", "MB", "GB", "TB", "PB")
    step = float(value)
    for unit in units:
        if abs(step) < 1024.0 or unit == "PB":
            if unit == "B":
                return f"{int(step)} B"
            return f"{step:.1f} {unit}"
        step /= 1024.0
    return f"{step:.1f} PB"
