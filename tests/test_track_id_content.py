from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from djlib.djlib_tags import generate_track_id


def _make_file(path: Path, content: bytes) -> None:
    path.write_bytes(content)


def test_same_content_same_id_across_rename(tmp_path: Path) -> None:
    data = os.urandom(2 * 1024 * 1024)
    a = tmp_path / "track.mp3"
    b = tmp_path / "renamed_completely_different.mp3"
    _make_file(a, data)
    id_a = generate_track_id(a)
    shutil.move(str(a), str(b))
    id_b = generate_track_id(b)
    assert id_a == id_b


def test_same_content_same_id_across_directories(tmp_path: Path) -> None:
    data = os.urandom(2 * 1024 * 1024)
    dir1 = tmp_path / "dir1"
    dir2 = tmp_path / "nested" / "deeper"
    dir1.mkdir()
    dir2.mkdir(parents=True)
    a = dir1 / "a.mp3"
    b = dir2 / "b.mp3"
    _make_file(a, data)
    _make_file(b, data)
    assert generate_track_id(a) == generate_track_id(b)


def test_different_content_different_id(tmp_path: Path) -> None:
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.mp3"
    _make_file(a, os.urandom(2 * 1024 * 1024))
    _make_file(b, os.urandom(2 * 1024 * 1024))
    assert generate_track_id(a) != generate_track_id(b)


def test_stable_across_head_tag_edit_same_size(tmp_path: Path) -> None:
    """Tag edits at the file head that preserve total size must not change the ID.

    Realistic ID3v2/Vorbis tag edits via mutagen preserve padding and keep file
    size constant. We hash the tail, so any head mutation within the padded
    region is invisible as long as total size is unchanged.
    """
    body = os.urandom(3 * 1024 * 1024)
    header_size = 4096
    a = tmp_path / "a.mp3"
    _make_file(a, b"\x00" * header_size + body)
    id_before = generate_track_id(a)
    # Simulate in-place tag edit: same total size, different head bytes
    _make_file(a, b"TAG_EDITED" + b"\x00" * (header_size - 10) + body)
    id_after = generate_track_id(a)
    assert id_before == id_after


def test_stable_across_id3v1_footer_change(tmp_path: Path) -> None:
    """ID3v1 footer (last 128 B) must not affect the ID."""
    body = os.urandom(3 * 1024 * 1024)
    footer_a = b"TAG" + b"A" * 125
    footer_b = b"TAG" + b"B" * 125
    a = tmp_path / "a.mp3"
    _make_file(a, body + footer_a)
    id_a = generate_track_id(a)
    _make_file(a, body + footer_b)
    id_b = generate_track_id(a)
    assert id_a == id_b


def test_small_file_supported(tmp_path: Path) -> None:
    a = tmp_path / "tiny.mp3"
    _make_file(a, os.urandom(10 * 1024))
    # Should not raise; deterministic
    id1 = generate_track_id(a)
    id2 = generate_track_id(a)
    assert id1 == id2
    assert len(id1) == 36


def test_nonexistent_file_fallback(tmp_path: Path) -> None:
    fake = tmp_path / "does_not_exist.mp3"
    id1 = generate_track_id(fake, artist="Artist", title="Title")
    id2 = generate_track_id(fake, artist="Artist", title="Title")
    assert id1 == id2
    assert len(id1) == 36


def test_ignores_artist_title_for_existing_file(tmp_path: Path) -> None:
    """artist/title must not influence the ID when the file exists."""
    data = os.urandom(2 * 1024 * 1024)
    a = tmp_path / "a.mp3"
    _make_file(a, data)
    id1 = generate_track_id(a, artist="X", title="Y")
    id2 = generate_track_id(a, artist="DIFFERENT", title="ALSO_DIFFERENT")
    assert id1 == id2


def test_deterministic_uuid_format(tmp_path: Path) -> None:
    a = tmp_path / "a.mp3"
    _make_file(a, os.urandom(2 * 1024 * 1024))
    tid = generate_track_id(a)
    import uuid as _uuid
    parsed = _uuid.UUID(tid)
    assert parsed.version == 5
