"""Tests for djlib.cli._ensure_unique_path.

Fixes the WF0 rename-to-ASCII step: when two different mojibake filenames
sanitize to the same clean name, the second one used to be silently skipped
(printed "⚠️  A file with that name already exists — will skip this one.").
Now it gets a ``(2)`` suffix so nothing is lost — content-level dupes are a
separate concern handled by `djlib.cli dupes`.
"""
from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from djlib.cli import _ensure_unique_path


def _touch(p: Path) -> None:
    p.write_bytes(b"\x00")


def test_free_path_returned_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "clean.mp3"
    assert _ensure_unique_path(target) == target


def test_collision_gets_2_suffix(tmp_path: Path) -> None:
    existing = tmp_path / "Artist - Title.mp3"
    _touch(existing)
    result = _ensure_unique_path(existing)
    assert result == tmp_path / "Artist - Title (2).mp3"


def test_multiple_collisions_increment(tmp_path: Path) -> None:
    _touch(tmp_path / "x.mp3")
    _touch(tmp_path / "x (2).mp3")
    _touch(tmp_path / "x (3).mp3")
    result = _ensure_unique_path(tmp_path / "x.mp3")
    assert result == tmp_path / "x (4).mp3"


def test_extension_preserved(tmp_path: Path) -> None:
    _touch(tmp_path / "track.flac")
    result = _ensure_unique_path(tmp_path / "track.flac")
    assert result.suffix == ".flac"
    assert result.name == "track (2).flac"


def test_no_extension_handled(tmp_path: Path) -> None:
    _touch(tmp_path / "README")
    result = _ensure_unique_path(tmp_path / "README")
    assert result.name == "README (2)"


def test_nfd_on_disk_detected_as_collision(tmp_path: Path) -> None:
    """macOS APFS stores filenames in NFD; a caller's NFC-composed Path
    must still see the NFD file on disk as a collision."""
    nfd_name = unicodedata.normalize("NFD", "café.mp3")
    nfc_name = unicodedata.normalize("NFC", "café.mp3")
    assert nfd_name != nfc_name
    _touch(tmp_path / nfd_name)

    # Query with NFC form. On case-sensitive filesystems .exists() might
    # return False, but the directory-listing NFC check still catches it.
    result = _ensure_unique_path(tmp_path / nfc_name)
    assert result.name != nfc_name
    assert result.name.startswith("café") or result.name.startswith(
        unicodedata.normalize("NFC", "café")
    )
    assert "(2)" in result.name


def test_simulates_two_mojibake_files_sanitizing_to_same_name(tmp_path: Path) -> None:
    """The real bug: two curly-quote files both sanitize to the same plain
    name. First rename wins; second must not be silently dropped."""
    # First file already landed under the clean name
    clean_target = tmp_path / "foo bar.mp3"
    _touch(clean_target)

    # Second file wants the same clean name
    second = _ensure_unique_path(clean_target)
    assert second != clean_target
    assert second.name == "foo bar (2).mp3"
    assert not second.exists()  # the allocation call doesn't create the file
