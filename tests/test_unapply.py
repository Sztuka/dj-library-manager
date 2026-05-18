"""Tests for djlib.unapply: WAL, find_move_entries, _build_unsorted_row, run_unapply."""
from __future__ import annotations

import csv
import json
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch

import pytest

from djlib.unapply import (
    UNAPPLY_COMMITTED,
    UNAPPLY_FAILED,
    UNAPPLY_HASH_OK,
    UNAPPLY_MOVED,
    UnapplyResult,
    UnapplyState,
    _build_unsorted_row,
    _resolve_restored_path,
    find_move_entries,
    run_unapply,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _write_moves_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["src", "dest", "track_id"])
        w.writeheader()
        w.writerows(rows)


def _lib_row(**kwargs) -> Dict[str, str]:
    defaults = {
        "track_id": "tid-1",
        "artist": "Artist",
        "title": "Title",
        "bpm": "124",
        "key": "7B",
        "file_hash": "",
        "old_full_path": "",
        "file_path": "",
        "final_path": "",
        "original_path": "",
        "live_location": "nas",
        "added_date": "2024-01-01",
        "year": "2024",
        "rating": "",
        "must_play": "",
        "occasion_tags": "",
        "notes": "",
        "playlists": "",
        "pop_playcount": "",
        "pop_listeners": "",
        "rekordbox_id": "",
        "traktor_id": "",
    }
    defaults.update(kwargs)
    return defaults


# ── UnapplyState (WAL) ────────────────────────────────────────────────────────


def test_wal_append_and_read():
    with tempfile.TemporaryDirectory() as td:
        wal = UnapplyState(Path(td) / "test.wal.jsonl")
        wal.append_event(UNAPPLY_HASH_OK, track_id="abc", path="/some/file.aiff")
        states = wal.get_track_states()
        assert states["abc"] == UNAPPLY_HASH_OK


def test_wal_last_event_wins():
    with tempfile.TemporaryDirectory() as td:
        wal = UnapplyState(Path(td) / "test.wal.jsonl")
        wal.append_event(UNAPPLY_HASH_OK, track_id="abc")
        wal.append_event(UNAPPLY_MOVED, track_id="abc")
        states = wal.get_track_states()
        assert states["abc"] == UNAPPLY_MOVED


def test_wal_is_committed_false_when_missing():
    with tempfile.TemporaryDirectory() as td:
        wal = UnapplyState(Path(td) / "missing.wal.jsonl")
        assert wal.is_committed() is False


def test_wal_is_committed_true():
    with tempfile.TemporaryDirectory() as td:
        wal = UnapplyState(Path(td) / "test.wal.jsonl")
        wal.append_event(UNAPPLY_HASH_OK, track_id="abc")
        wal.append_event(UNAPPLY_COMMITTED, moved=1)
        assert wal.is_committed() is True


# ── find_move_entries ─────────────────────────────────────────────────────────


def test_find_move_entries_last_run():
    with tempfile.TemporaryDirectory() as td:
        logs = Path(td)
        _write_moves_csv(logs / "moves-20240101-120000.csv", [
            {"src": "/old/a.aiff", "dest": "/lib/a.aiff", "track_id": "tid-1"},
            {"src": "/old/b.aiff", "dest": "/lib/b.aiff", "track_id": "tid-2"},
        ])
        entries = find_move_entries(logs, last_run=True)
    assert len(entries) == 2
    assert {e["track_id"] for e in entries} == {"tid-1", "tid-2"}


def test_find_move_entries_last_n():
    with tempfile.TemporaryDirectory() as td:
        logs = Path(td)
        _write_moves_csv(logs / "moves-20240101-100000.csv", [
            {"src": "/old/a.aiff", "dest": "/lib/a.aiff", "track_id": "tid-1"},
        ])
        _write_moves_csv(logs / "moves-20240101-110000.csv", [
            {"src": "/old/b.aiff", "dest": "/lib/b.aiff", "track_id": "tid-2"},
            {"src": "/old/c.aiff", "dest": "/lib/c.aiff", "track_id": "tid-3"},
        ])
        entries = find_move_entries(logs, last_n=2)
    # Should return the 2 most recent entries (newest log first)
    assert len(entries) == 2
    tids = {e["track_id"] for e in entries}
    assert "tid-3" in tids
    assert "tid-2" in tids


def test_find_move_entries_by_track_id():
    with tempfile.TemporaryDirectory() as td:
        logs = Path(td)
        _write_moves_csv(logs / "moves-20240101-120000.csv", [
            {"src": "/old/a.aiff", "dest": "/lib/a.aiff", "track_id": "tid-1"},
            {"src": "/old/b.aiff", "dest": "/lib/b.aiff", "track_id": "tid-2"},
        ])
        entries = find_move_entries(logs, track_ids=["tid-1"])
    assert len(entries) == 1
    assert entries[0]["track_id"] == "tid-1"


def test_find_move_entries_empty_logs():
    with tempfile.TemporaryDirectory() as td:
        entries = find_move_entries(Path(td), last_run=True)
    assert entries == []


# ── _build_unsorted_row ───────────────────────────────────────────────────────


def test_build_unsorted_row_maps_key():
    lib = _lib_row(key="8A", key_camelot="")
    restored = Path("/inbox/track.aiff")
    row = _build_unsorted_row(lib, restored)
    assert row["key_camelot"] == "8A"


def test_build_unsorted_row_resets_disposition():
    lib = _lib_row()
    row = _build_unsorted_row(lib, Path("/inbox/track.aiff"))
    assert row["disposition"] == ""
    assert row["near_duplicate_of"] == ""


def test_build_unsorted_row_file_path_is_restored():
    lib = _lib_row()
    restored = Path("/inbox/Artist - Title.aiff")
    row = _build_unsorted_row(lib, restored)
    assert row["file_path"] == str(restored)


def test_build_unsorted_row_preserves_added_date():
    lib = _lib_row(added_date="2023-06-15")
    row = _build_unsorted_row(lib, Path("/inbox/t.aiff"))
    assert row["added_date"] == "2023-06-15"


# ── _resolve_restored_path ────────────────────────────────────────────────────


def test_resolve_restored_path_uses_original_dir_when_exists():
    with tempfile.TemporaryDirectory() as td:
        src = str(Path(td) / "Artist - Title.aiff")
        result = _resolve_restored_path(src, Path("/inbox"), "Artist - Title.aiff")
        assert result == Path(src)


def test_resolve_restored_path_falls_back_to_inbox():
    result = _resolve_restored_path(
        "/nonexistent/dir/Artist - Title.aiff",
        Path("/inbox"),
        "Artist - Title.aiff",
    )
    assert result == Path("/inbox/Artist - Title.aiff")


# ── run_unapply ───────────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_dirs():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        yield {
            "root": root,
            "lib_dir": root / "lib",
            "inbox": root / "inbox",
            "logs": root / "logs",
        }


def _write_library_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    from djlib.library_schema import LIBRARY_FIELDNAMES
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LIBRARY_FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            # fill missing keys with ""
            full = {k: "" for k in LIBRARY_FIELDNAMES}
            full.update(row)
            w.writerow(full)


def _write_unsorted_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    from djlib.unsorted import UNSORTED_FIELDNAMES
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=UNSORTED_FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            full = {k: "" for k in UNSORTED_FIELDNAMES}
            full.update(row)
            w.writerow(full)


def test_run_unapply_moves_file_and_updates_csvs(tmp_dirs):
    root = tmp_dirs["root"]
    logs = tmp_dirs["logs"]
    inbox = tmp_dirs["inbox"]
    logs.mkdir(parents=True, exist_ok=True)
    inbox.mkdir(parents=True, exist_ok=True)

    # Create a fake library file
    lib_file = root / "lib" / "Artist - Title.aiff"
    lib_file.parent.mkdir(parents=True, exist_ok=True)
    lib_file.write_bytes(b"audio data")

    lib_csv = root / "library.csv"
    _write_library_csv(lib_csv, [
        _lib_row(track_id="tid-1", old_full_path=str(lib_file), live_location="nas"),
    ])

    unsorted_csv = root / "unsorted.csv"
    _write_unsorted_csv(unsorted_csv, [])

    entries = [{"src": "/old/Artist - Title.aiff", "dest": str(lib_file), "track_id": "tid-1", "log_file": ""}]

    result = run_unapply(
        entries=entries,
        unsorted_csv=unsorted_csv,
        library_csv_path=lib_csv,
        logs_dir=logs,
        inbox_dir=inbox,
    )

    assert result.moved == 1
    assert result.committed is True
    # File moved somewhere (original dir /old doesn't exist → inbox)
    assert (inbox / "Artist - Title.aiff").exists()
    # No longer in library
    from djlib.library_schema import load_library_csv
    lib_rows = load_library_csv(lib_csv)
    assert not any(r["track_id"] == "tid-1" for r in lib_rows)
    # Now in unsorted
    from djlib.unsorted import load_unsorted_rows
    us_rows = load_unsorted_rows(unsorted_csv)
    assert any(r["track_id"] == "tid-1" for r in us_rows)


def test_run_unapply_dry_run_does_not_move(tmp_dirs):
    root = tmp_dirs["root"]
    logs = tmp_dirs["logs"]
    inbox = tmp_dirs["inbox"]
    logs.mkdir(parents=True, exist_ok=True)
    inbox.mkdir(parents=True, exist_ok=True)

    lib_file = root / "lib" / "track.aiff"
    lib_file.parent.mkdir(parents=True, exist_ok=True)
    lib_file.write_bytes(b"audio")

    lib_csv = root / "library.csv"
    _write_library_csv(lib_csv, [
        _lib_row(track_id="tid-1", old_full_path=str(lib_file), live_location="nas"),
    ])

    unsorted_csv = root / "unsorted.csv"
    _write_unsorted_csv(unsorted_csv, [])

    entries = [{"src": "/old/track.aiff", "dest": str(lib_file), "track_id": "tid-1", "log_file": ""}]

    result = run_unapply(
        entries=entries,
        unsorted_csv=unsorted_csv,
        library_csv_path=lib_csv,
        logs_dir=logs,
        inbox_dir=inbox,
        dry_run=True,
    )

    assert result.committed is False
    assert result.moved == 1   # dry-run counts would-be moves
    assert lib_file.exists()  # not moved


def test_run_unapply_skips_active_gig_track(tmp_dirs):
    root = tmp_dirs["root"]
    logs = tmp_dirs["logs"]
    inbox = tmp_dirs["inbox"]
    logs.mkdir(parents=True, exist_ok=True)
    inbox.mkdir(parents=True, exist_ok=True)

    lib_file = root / "lib" / "track.aiff"
    lib_file.parent.mkdir(parents=True, exist_ok=True)
    lib_file.write_bytes(b"audio")

    lib_csv = root / "library.csv"
    _write_library_csv(lib_csv, [
        _lib_row(track_id="tid-1", old_full_path=str(lib_file), live_location="gig:friday-2026-05-15"),
    ])

    unsorted_csv = root / "unsorted.csv"
    _write_unsorted_csv(unsorted_csv, [])

    entries = [{"src": "/old/track.aiff", "dest": str(lib_file), "track_id": "tid-1", "log_file": ""}]

    result = run_unapply(
        entries=entries,
        unsorted_csv=unsorted_csv,
        library_csv_path=lib_csv,
        logs_dir=logs,
        inbox_dir=inbox,
    )

    assert result.skipped_wrong_location == 1
    assert result.moved == 0


def test_run_unapply_skips_missing_from_library(tmp_dirs):
    root = tmp_dirs["root"]
    logs = tmp_dirs["logs"]
    inbox = tmp_dirs["inbox"]
    logs.mkdir(parents=True, exist_ok=True)
    inbox.mkdir(parents=True, exist_ok=True)

    lib_csv = root / "library.csv"
    _write_library_csv(lib_csv, [])  # empty — tid-1 not present

    unsorted_csv = root / "unsorted.csv"
    _write_unsorted_csv(unsorted_csv, [])

    entries = [{"src": "/old/track.aiff", "dest": "/lib/track.aiff", "track_id": "tid-1", "log_file": ""}]

    result = run_unapply(
        entries=entries,
        unsorted_csv=unsorted_csv,
        library_csv_path=lib_csv,
        logs_dir=logs,
        inbox_dir=inbox,
    )

    assert result.skipped_not_in_library == 1
    assert result.moved == 0


def test_run_unapply_resume_empty_lib_row(tmp_dirs):
    """Resume after library.csv was already updated but COMMITTED not written.

    Scenario: Phase 1 done (file moved, WAL has UNAPPLY_MOVED with snapshot),
    Phase 2 completed (unsorted + library both written), crash before COMMITTED.
    On resume the track is not in library.csv — lib_row is empty.
    The snapshot stored in the WAL should reconstruct the unsorted row.
    """
    root = tmp_dirs["root"]
    logs = tmp_dirs["logs"]
    inbox = tmp_dirs["inbox"]
    logs.mkdir(parents=True, exist_ok=True)
    inbox.mkdir(parents=True, exist_ok=True)

    # Simulate the state after Phase 2 completed but before COMMITTED:
    # - File is already in inbox (moved during Phase 1)
    restored_file = inbox / "Artist - Title.aiff"
    restored_file.write_bytes(b"audio")

    # - Library.csv is empty (track already removed)
    lib_csv = root / "library.csv"
    _write_library_csv(lib_csv, [])

    # - Unsorted.csv already has the track (written during Phase 2)
    unsorted_csv = root / "unsorted.csv"
    _write_unsorted_csv(unsorted_csv, [
        {"track_id": "tid-1", "artist": "Artist", "title": "Title"},
    ])

    # - WAL has UNAPPLY_MOVED with snapshot but no COMMITTED
    wal_path = logs / "unapply-test.wal.jsonl"
    wal = UnapplyState(wal_path)
    wal.append_event(
        UNAPPLY_MOVED,
        track_id="tid-1",
        src="/old/Artist - Title.aiff",
        dest=str(restored_file),
        artist="Artist",
        title="Title",
        bpm="124",
        key="7B",
        added_date="2024-01-01",
        year="2024",
    )

    entries = [{"src": "/old/Artist - Title.aiff", "dest": str(restored_file), "track_id": "tid-1", "log_file": ""}]

    result = run_unapply(
        entries=entries,
        unsorted_csv=unsorted_csv,
        library_csv_path=lib_csv,
        logs_dir=logs,
        inbox_dir=inbox,
        resume=True,
        wal_path=wal_path,
    )

    assert result.committed is True
    assert result.skipped_wal_resumed == 1
    # Track should remain in unsorted (it was already there)
    from djlib.unsorted import load_unsorted_rows
    us_rows = load_unsorted_rows(unsorted_csv)
    assert any(r["track_id"] == "tid-1" for r in us_rows)


def test_run_unapply_resume_already_committed(tmp_dirs):
    root = tmp_dirs["root"]
    logs = tmp_dirs["logs"]
    inbox = tmp_dirs["inbox"]
    logs.mkdir(parents=True, exist_ok=True)
    inbox.mkdir(parents=True, exist_ok=True)

    wal_path = logs / "unapply-test.wal.jsonl"
    wal = UnapplyState(wal_path)
    wal.append_event(UNAPPLY_COMMITTED, moved=1)

    lib_csv = root / "library.csv"
    _write_library_csv(lib_csv, [])

    unsorted_csv = root / "unsorted.csv"
    _write_unsorted_csv(unsorted_csv, [])

    entries = [{"src": "/old/track.aiff", "dest": "/lib/track.aiff", "track_id": "tid-1", "log_file": ""}]

    result = run_unapply(
        entries=entries,
        unsorted_csv=unsorted_csv,
        library_csv_path=lib_csv,
        logs_dir=logs,
        inbox_dir=inbox,
        resume=True,
        wal_path=wal_path,
    )

    assert result.skipped_already_done == 1
    assert result.committed is True
