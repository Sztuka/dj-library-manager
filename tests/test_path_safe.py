from __future__ import annotations

import os
import unicodedata
from pathlib import Path

import pytest

from djlib.cli import _check_path_is_safe


def _make(p: Path) -> None:
    p.write_bytes(b"\x00" * 16)


def test_clean_ascii_file_is_safe(tmp_path: Path) -> None:
    f = tmp_path / "Artist - Title.mp3"
    _make(f)
    ok, why = _check_path_is_safe(f)
    assert ok, why


def test_nonexistent_file_is_unsafe(tmp_path: Path) -> None:
    f = tmp_path / "does_not_exist.mp3"
    ok, why = _check_path_is_safe(f)
    assert not ok
    assert "does not exist" in why


def test_control_char_in_name_is_unsafe(tmp_path: Path) -> None:
    """Double-encoded UTF-8 mojibake from typographic quotes.

    Real example that crashed WF0: "Give It To Me (Bohemian Foolish Timeless Edit).wav".
    U+0093 and U+0094 are C1 control chars — fingerprint of Latin-1-reinterpreted
    UTF-8 curly quotes.
    """
    bad_name = "evil" + chr(0x0093) + "name.mp3"
    f = tmp_path / bad_name
    try:
        _make(f)
    except OSError:
        pytest.skip("filesystem rejects control char in name")
    ok, why = _check_path_is_safe(f)
    assert not ok
    assert "control char" in why
    assert "U+0093" in why


def test_unicode_nfc_filename_is_safe(tmp_path: Path) -> None:
    """Regular Unicode filenames (é, á, Å) pass the safety check."""
    f = tmp_path / "Beyoncé - Déjà Vu.mp3"
    _make(f)
    ok, why = _check_path_is_safe(f)
    assert ok, why


def test_nfc_vs_nfd_roundtrip_matches(tmp_path: Path) -> None:
    """macOS stores NFD; a Path constructed in NFC form must still match.

    We always compare via NFC-normalized directory listing, so either form on
    input should resolve to the same logical file.
    """
    nfc_name = unicodedata.normalize("NFC", "café.mp3")
    nfd_name = unicodedata.normalize("NFD", "café.mp3")
    assert nfc_name != nfd_name  # sanity: the two forms differ byte-wise

    f = tmp_path / nfc_name
    _make(f)

    # Query via NFD form; on APFS this resolves to the same file. On other
    # filesystems it might not, but our NFC-normalized comparison makes it
    # safe either way.
    as_nfd = Path(str(tmp_path / nfd_name))
    ok, why = _check_path_is_safe(as_nfd)
    # On Linux ext4 NFD != NFC on disk; skip if exists() fails there.
    if not as_nfd.exists():
        pytest.skip("non-APFS filesystem: NFD query doesn't resolve NFC file")
    assert ok, why


def test_cache_avoids_repeated_listdir(tmp_path: Path, monkeypatch) -> None:
    """Parent-listing cache is consulted on repeat calls."""
    import djlib.cli as cli

    f1 = tmp_path / "a.mp3"
    f2 = tmp_path / "b.mp3"
    _make(f1)
    _make(f2)

    calls: list[Path] = []
    real = cli._parent_nfc_names_uncached

    def spy(parent: Path) -> frozenset[str]:
        calls.append(parent)
        return real(parent)

    monkeypatch.setattr(cli, "_parent_nfc_names_uncached", spy)

    cache: dict[str, frozenset[str]] = {}
    ok1, _ = _check_path_is_safe(f1, parent_names_cache=cache)
    ok2, _ = _check_path_is_safe(f2, parent_names_cache=cache)
    assert ok1 and ok2
    # Same parent → only one listdir.
    assert len(calls) == 1


def test_exception_does_not_crash(tmp_path: Path) -> None:
    """Safety check must never raise — always returns (False, reason)."""

    class BadPath:
        @property
        def name(self):  # type: ignore[override]
            raise RuntimeError("boom")

    # Intentionally pass a non-Path duck-typed object to trigger the except.
    ok, why = _check_path_is_safe(BadPath())  # type: ignore[arg-type]
    assert not ok
    assert "exception" in why
