import os

import pytest

from dupefinder.hashing import (
    PARTIAL_THRESHOLD,
    files_are_identical,
    full_hash,
    partial_hash,
)


def test_identical_files_share_hashes(tmp_path):
    payload = os.urandom(PARTIAL_THRESHOLD * 3)
    first = tmp_path / "a.bin"
    second = tmp_path / "b.bin"
    first.write_bytes(payload)
    second.write_bytes(payload)

    assert partial_hash(first) == partial_hash(second)
    assert full_hash(first) == full_hash(second)
    assert files_are_identical(first, second)


def test_middle_difference_is_caught_by_full_hash_only(tmp_path):
    payload = bytearray(os.urandom(PARTIAL_THRESHOLD * 4))
    first = tmp_path / "a.bin"
    first.write_bytes(bytes(payload))

    middle = len(payload) // 2
    payload[middle] ^= 0xFF
    second = tmp_path / "b.bin"
    second.write_bytes(bytes(payload))

    # Kop en staart zijn gelijk, dus de snelle trap ziet geen verschil ...
    assert partial_hash(first) == partial_hash(second)
    # ... maar de volledige hash wel.
    assert full_hash(first) != full_hash(second)
    assert not files_are_identical(first, second)


def test_same_content_different_size_differs(tmp_path):
    first = tmp_path / "a.bin"
    second = tmp_path / "b.bin"
    first.write_bytes(b"foto")
    second.write_bytes(b"foto!")

    assert partial_hash(first) != partial_hash(second)
    assert not files_are_identical(first, second)


def test_small_file_partial_equals_content(tmp_path):
    path = tmp_path / "klein.jpg"
    path.write_bytes(b"x" * 1024)
    assert partial_hash(path) == partial_hash(path)


@pytest.mark.parametrize("size", [0, 1, 1024, PARTIAL_THRESHOLD + 1])
def test_hashing_handles_edge_sizes(tmp_path, size):
    path = tmp_path / f"f{size}.bin"
    path.write_bytes(os.urandom(size))
    assert len(partial_hash(path)) == 64
    assert len(full_hash(path)) == 64
