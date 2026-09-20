"""Achtergrondtaken voor de GUI: scannen, verwijderen en miniaturen laden."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal, Slot

from ..actions import DeletionOutcome, delete_paths
from ..config import ScanConfig
from ..scanner import DuplicateScanner, ScanProgress, ScanResult
from ..thumbnails import thumbnail_png


class ScanWorker(QThread):
    """Voert een scan uit zonder de interface te blokkeren."""

    progressed = Signal(object)  # ScanProgress
    completed = Signal(object)  # ScanResult
    failed = Signal(str)

    def __init__(self, config: ScanConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:  # pragma: no cover - draait in een echte thread
        try:
            scanner = DuplicateScanner(
                self.config,
                on_progress=self._emit_progress,
                cancel_event=self.cancel_event,
            )
            result: ScanResult = scanner.run()
        except Exception as exc:  # noqa: BLE001 - alles melden aan de GUI
            self.failed.emit(str(exc))
            return
        self.completed.emit(result)

    def _emit_progress(self, progress: ScanProgress) -> None:
        self.progressed.emit(progress)


class DeleteWorker(QThread):
    """Verplaatst bestanden naar de prullenbak op de achtergrond."""

    progressed = Signal(int, int, str)
    completed = Signal(object)  # DeletionOutcome

    def __init__(self, paths: list[str], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.paths = paths
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # pragma: no cover - draait in een echte thread
        outcome: DeletionOutcome = delete_paths(
            self.paths,
            on_progress=lambda done, total, path: self.progressed.emit(done, total, path),
            should_cancel=self._cancel.is_set,
        )
        self.completed.emit(outcome)


class _ThumbnailSignals(QObject):
    ready = Signal(str, object)  # pad, PNG-bytes of None


class _ThumbnailTask(QRunnable):
    def __init__(self, path: str, size: int, signals: _ThumbnailSignals) -> None:
        super().__init__()
        self.path = path
        self.size = size
        self.signals = signals

    @Slot()
    def run(self) -> None:  # pragma: no cover - draait in een threadpool
        try:
            data = thumbnail_png(self.path, self.size)
        except Exception:
            data = None
        self.signals.ready.emit(self.path, data)


class ThumbnailService(QObject):
    """Laadt miniaturen parallel en voorkomt dubbele opdrachten."""

    ready = Signal(str, object)

    def __init__(self, max_threads: int = 4, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(1, max_threads))
        self._signals = _ThumbnailSignals()
        self._signals.ready.connect(self._on_ready)
        self._pending: set[tuple[str, int]] = set()

    def request(self, path: str, size: int = 256) -> None:
        key = (path, size)
        if key in self._pending:
            return
        self._pending.add(key)
        self._pool.start(_ThumbnailTask(path, size, self._signals))

    def clear_pending(self) -> None:
        self._pending.clear()

    def shutdown(self) -> None:
        self._pool.clear()
        self._pool.waitForDone(2000)

    def _on_ready(self, path: str, data: object) -> None:
        self._pending = {key for key in self._pending if key[0] != path}
        self.ready.emit(path, data)
