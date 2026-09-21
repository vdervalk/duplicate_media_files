"""Hashfuncties voor byte-identieke vergelijking.

De scan werkt in drie trappen, van goedkoop naar duur:

1. groeperen op bestandsgrootte (geen I/O);
2. een *partiele* hash over kop en staart van het bestand (64 KiB elk);
3. een volledige SHA-256 over het hele bestand.

Alleen bestanden die trap 3 overleven zijn echt byte-identiek.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

#: Aantal bytes dat aan begin en einde wordt gelezen voor de partiele hash.
PARTIAL_CHUNK = 64 * 1024
#: Bestanden tot deze grootte worden meteen volledig gehasht.
PARTIAL_THRESHOLD = 2 * PARTIAL_CHUNK
#: Leesblok voor de volledige hash.
READ_BLOCK = 1024 * 1024

HASH_ALGORITHM = "sha256"

CancelCheck = Callable[[], bool]


class HashCancelled(Exception):
    """Wordt gegooid wanneer het hashen halverwege wordt afgebroken."""


def _new_hash():
    return hashlib.new(HASH_ALGORITHM)


def partial_hash(path: Path, size: int | None = None) -> str:
    """Hash van de eerste en laatste 64 KiB, plus de bestandsgrootte.

    Bestanden kleiner dan 128 KiB worden in hun geheel gehasht; de uitkomst is
    dan gelijkwaardig aan een volledige hash maar wordt toch apart bewaard.
    """
    if size is None:
        size = path.stat().st_size
    digest = _new_hash()
    digest.update(str(size).encode("ascii"))
    with path.open("rb") as handle:
        if size <= PARTIAL_THRESHOLD:
            while True:
                block = handle.read(READ_BLOCK)
                if not block:
                    break
                digest.update(block)
        else:
            digest.update(handle.read(PARTIAL_CHUNK))
            handle.seek(-PARTIAL_CHUNK, 2)
            digest.update(handle.read(PARTIAL_CHUNK))
    return digest.hexdigest()


def full_hash(
    path: Path,
    on_bytes: Callable[[int], None] | None = None,
    should_cancel: CancelCheck | None = None,
) -> str:
    """SHA-256 over de volledige inhoud van het bestand.

    ``on_bytes`` wordt na elk gelezen blok aangeroepen met het aantal bytes,
    zodat de GUI voortgang kan tonen bij grote videobestanden.
    """
    digest = _new_hash()
    with path.open("rb") as handle:
        while True:
            if should_cancel is not None and should_cancel():
                raise HashCancelled(str(path))
            block = handle.read(READ_BLOCK)
            if not block:
                break
            digest.update(block)
            if on_bytes is not None:
                on_bytes(len(block))
    return digest.hexdigest()


def files_are_identical(first: Path, second: Path) -> bool:
    """Byte-voor-byte vergelijking, als laatste controle voor het verwijderen."""
    try:
        if first.stat().st_size != second.stat().st_size:
            return False
    except OSError:
        return False
    with first.open("rb") as a, second.open("rb") as b:
        while True:
            block_a = a.read(READ_BLOCK)
            block_b = b.read(READ_BLOCK)
            if block_a != block_b:
                return False
            if not block_a:
                return True
