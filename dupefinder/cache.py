"""SQLite-cache voor berekende hashes.

De cache maakt een tweede scan van dezelfde schijf vrijwel gratis: een bestand
wordt alleen opnieuw gehasht wanneer pad, grootte of wijzigingsdatum verandert.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS file_hashes (
    path        TEXT PRIMARY KEY,
    size        INTEGER NOT NULL,
    mtime_ns    INTEGER NOT NULL,
    partial     TEXT,
    full        TEXT
);
"""


class HashCache:
    """Draadveilige, optionele hashcache.

    Wordt de cache uitgeschakeld (``enabled=False``), dan gedragen alle
    methodes zich als een lege cache zonder schijftoegang.
    """

    def __init__(self, path: Path | None, enabled: bool = True) -> None:
        self.enabled = enabled and path is not None
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        if not self.enabled:
            return
        assert path is not None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        except sqlite3.Error:
            # Een kapotte of onbereikbare cache mag een scan nooit blokkeren.
            self._conn = None
            self.enabled = False

    # -- lezen ---------------------------------------------------------------

    def get(self, path: str, size: int, mtime_ns: int) -> tuple[str | None, str | None]:
        """Geef ``(partial, full)`` terug voor een ongewijzigd bestand."""
        if not self._conn:
            return (None, None)
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT partial, full FROM file_hashes"
                    " WHERE path = ? AND size = ? AND mtime_ns = ?",
                    (path, size, mtime_ns),
                ).fetchone()
            except sqlite3.Error:
                return (None, None)
        return (row[0], row[1]) if row else (None, None)

    # -- schrijven -----------------------------------------------------------

    def put(
        self,
        path: str,
        size: int,
        mtime_ns: int,
        partial: str | None = None,
        full: str | None = None,
    ) -> None:
        if not self._conn:
            return
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO file_hashes (path, size, mtime_ns, partial, full)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        size = excluded.size,
                        mtime_ns = excluded.mtime_ns,
                        partial = COALESCE(excluded.partial, file_hashes.partial),
                        full = COALESCE(excluded.full, file_hashes.full)
                    """,
                    (path, size, mtime_ns, partial, full),
                )
            except sqlite3.Error:
                pass

    def forget(self, path: str) -> None:
        """Verwijder een pad uit de cache (na verwijderen of verplaatsen)."""
        if not self._conn:
            return
        with self._lock:
            try:
                self._conn.execute("DELETE FROM file_hashes WHERE path = ?", (path,))
            except sqlite3.Error:
                pass

    def commit(self) -> None:
        if not self._conn:
            return
        with self._lock:
            try:
                self._conn.commit()
            except sqlite3.Error:
                pass

    def clear(self) -> None:
        if not self._conn:
            return
        with self._lock:
            try:
                self._conn.execute("DELETE FROM file_hashes")
                self._conn.commit()
            except sqlite3.Error:
                pass

    def count(self) -> int:
        if not self._conn:
            return 0
        with self._lock:
            try:
                return int(self._conn.execute("SELECT COUNT(*) FROM file_hashes").fetchone()[0])
            except sqlite3.Error:
                return 0

    def close(self) -> None:
        if not self._conn:
            return
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            except sqlite3.Error:
                pass
            finally:
                self._conn = None
                self.enabled = False

    def __enter__(self) -> "HashCache":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
