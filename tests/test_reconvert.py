"""Tests for djlib/reconvert.py — in-place WAV/FLAC → AIFF batch conversion."""
from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch, MagicMock

import pytest

from djlib.reconvert import (
    ReconvertState,
    ReconvertResult,
    ReconvertLock,
    run_reconvert,
    RECONVERT_DONE,
    RECONVERT_FAILED,
    RECONVERT_SKIPPED,
    RECONVERT_START,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_library_csv(tmp_path: Path, rows: List[Dict]) -> Path:
    from djlib.library_schema import LIBRARY_FIELDNAMES, save_library_csv

    csv_path = tmp_path / "library.csv"
    save_library_csv(csv_path, rows)
    return csv_path


def _lib_row(
    track_id: str = "tid-1",
    old_full_path: str = "",
    live_location: str = "nas",
) -> Dict:
    return {
        "track_id": track_id,
        "old_full_path": old_full_path,
        "live_location": live_location,
    }


# ── ReconvertState (WAL) ──────────────────────────────────────────────────────


def test_wal_empty_returns_empty(tmp_path):
    wal = ReconvertState(tmp_path / "test.state")
    assert wal.get_track_states() == {}


def test_wal_append_and_replay(tmp_path):
    wal = ReconvertState(tmp_path / "test.state")
    wal.append_event("t1", RECONVERT_START, src="/a.wav", dest="/a.aiff")
    wal.append_event("t1", RECONVERT_DONE,  src="/a.wav", dest="/a.aiff")
    wal.append_event("t2", RECONVERT_FAILED, src="/b.flac", reason="ffmpeg_error")

    states = wal.get_track_states()
    assert states["t1"] == RECONVERT_DONE
    assert states["t2"] == RECONVERT_FAILED


def test_wal_last_event_wins(tmp_path):
    wal = ReconvertState(tmp_path / "test.state")
    wal.append_event("t1", RECONVERT_START)
    wal.append_event("t1", RECONVERT_DONE)
    wal.append_event("t1", RECONVERT_SKIPPED)  # shouldn't normally happen, but test LWW

    assert wal.get_track_states()["t1"] == RECONVERT_SKIPPED


def test_wal_tolerates_corrupt_line(tmp_path):
    state_path = tmp_path / "test.state"
    state_path.write_text('{"track_id":"t1","event":"converted","ts":"x"}\nNOT_JSON\n')
    wal = ReconvertState(state_path)
    states = wal.get_track_states()
    assert states.get("t1") == "converted"


# ── ReconvertLock ─────────────────────────────────────────────────────────────


def test_lock_acquire_and_release(tmp_path):
    lock = ReconvertLock(tmp_path / "reconvert.lock")
    assert lock.acquire() is True
    lock.release()


def test_lock_blocks_second_acquire(tmp_path):
    lock1 = ReconvertLock(tmp_path / "reconvert.lock")
    lock2 = ReconvertLock(tmp_path / "reconvert.lock")
    assert lock1.acquire() is True
    assert lock2.acquire() is False
    lock1.release()


# ── run_reconvert: no candidates ──────────────────────────────────────────────


def test_no_candidates_returns_empty_result(tmp_path):
    """Library with only MP3s — nothing to convert."""
    row = _lib_row(track_id="t1", old_full_path=str(tmp_path / "song.mp3"))
    csv_path = _make_library_csv(tmp_path, [row])
    # Create the MP3 file (just needs to exist)
    (tmp_path / "song.mp3").write_bytes(b"\x00" * 100)

    with patch("djlib.reconvert._check_ffmpeg", return_value=True):
        result = run_reconvert(csv_path=csv_path, logs_dir=tmp_path / "LOGS")

    assert result.converted == 0
    assert result.failed == 0


def test_skips_tracks_not_on_nas(tmp_path):
    """Tracks with live_location=gig:xxx are skipped."""
    wav = tmp_path / "song.wav"
    wav.write_bytes(b"\x00" * 100)
    row = _lib_row(track_id="t1", old_full_path=str(wav), live_location="gig:friday-2026-05-15")
    csv_path = _make_library_csv(tmp_path, [row])

    with patch("djlib.reconvert._check_ffmpeg", return_value=True):
        result = run_reconvert(csv_path=csv_path, logs_dir=tmp_path / "LOGS")

    assert result.converted == 0


def test_skips_tracks_with_missing_file(tmp_path):
    """old_full_path points to non-existent file — skip silently."""
    row = _lib_row(track_id="t1", old_full_path=str(tmp_path / "missing.wav"))
    csv_path = _make_library_csv(tmp_path, [row])

    with patch("djlib.reconvert._check_ffmpeg", return_value=True):
        result = run_reconvert(csv_path=csv_path, logs_dir=tmp_path / "LOGS")

    assert result.converted == 0


# ── run_reconvert: successful conversion ──────────────────────────────────────


def test_converts_wav_to_aiff(tmp_path):
    """Happy path: WAV file converted → old_full_path updated, moves log written."""
    wav = tmp_path / "song.wav"
    wav.write_bytes(b"\x00" * 100)
    row = _lib_row(track_id="t1", old_full_path=str(wav))
    csv_path = _make_library_csv(tmp_path, [row])
    logs_dir = tmp_path / "LOGS"

    aiff_tmp = tmp_path / "tmp_converted.aiff"
    aiff_tmp.write_bytes(b"\xFF" * 200)

    with patch("djlib.reconvert._check_ffmpeg", return_value=True), \
         patch("djlib.reconvert.convert_to_aiff", return_value=aiff_tmp) as mock_conv:

        state_path = logs_dir / "test.state"
        result = run_reconvert(
            csv_path=csv_path,
            logs_dir=logs_dir,
            state_path=state_path,
        )

    assert result.converted == 1
    assert result.failed == 0
    assert result.skipped_already_aiff == 0

    # AIFF should exist at new path
    expected_aiff = wav.with_suffix(".aiff")
    assert expected_aiff.exists()

    # original WAV should still exist (never auto-deleted)
    assert wav.exists()
    assert str(wav) in result.originals_to_delete

    # library.csv should have updated old_full_path
    from djlib.library_schema import load_library_csv
    rows = load_library_csv(csv_path)
    assert rows[0]["old_full_path"] == str(expected_aiff)

    # moves log should exist
    move_logs = list(logs_dir.glob("moves-*.csv"))
    assert len(move_logs) == 1
    with move_logs[0].open() as f:
        reader = csv.DictReader(f)
        move_rows = list(reader)
    assert len(move_rows) == 1
    assert move_rows[0]["src"] == str(wav)
    assert move_rows[0]["dest"] == str(expected_aiff)
    assert move_rows[0]["track_id"] == "t1"


def test_converts_flac_to_aiff(tmp_path):
    """FLAC files are also converted."""
    flac = tmp_path / "song.flac"
    flac.write_bytes(b"\x00" * 100)
    row = _lib_row(track_id="t1", old_full_path=str(flac))
    csv_path = _make_library_csv(tmp_path, [row])
    logs_dir = tmp_path / "LOGS"

    aiff_tmp = tmp_path / "tmp_converted.aiff"
    aiff_tmp.write_bytes(b"\xFF" * 200)

    with patch("djlib.reconvert._check_ffmpeg", return_value=True), \
         patch("djlib.reconvert.convert_to_aiff", return_value=aiff_tmp):

        result = run_reconvert(
            csv_path=csv_path,
            logs_dir=logs_dir,
            state_path=logs_dir / "test.state",
        )

    assert result.converted == 1
    expected_aiff = flac.with_suffix(".aiff")
    assert expected_aiff.exists()

    from djlib.library_schema import load_library_csv
    rows = load_library_csv(csv_path)
    assert rows[0]["old_full_path"] == str(expected_aiff)


def test_wal_written_on_success(tmp_path):
    """WAL records convert_start then converted events."""
    wav = tmp_path / "song.wav"
    wav.write_bytes(b"\x00" * 100)
    row = _lib_row(track_id="t1", old_full_path=str(wav))
    csv_path = _make_library_csv(tmp_path, [row])
    logs_dir = tmp_path / "LOGS"
    state_path = logs_dir / "test.state"

    aiff_tmp = tmp_path / "tmp.aiff"
    aiff_tmp.write_bytes(b"\xFF" * 100)

    with patch("djlib.reconvert._check_ffmpeg", return_value=True), \
         patch("djlib.reconvert.convert_to_aiff", return_value=aiff_tmp):
        run_reconvert(csv_path=csv_path, logs_dir=logs_dir, state_path=state_path)

    wal = ReconvertState(state_path)
    states = wal.get_track_states()
    assert states["t1"] == RECONVERT_DONE


# ── run_reconvert: idempotency ────────────────────────────────────────────────


def test_skips_if_aiff_already_exists(tmp_path):
    """If AIFF already exists (non-empty), skip conversion."""
    wav = tmp_path / "song.wav"
    wav.write_bytes(b"\x00" * 100)
    aiff = tmp_path / "song.aiff"
    aiff.write_bytes(b"\xFF" * 200)

    row = _lib_row(track_id="t1", old_full_path=str(wav))
    csv_path = _make_library_csv(tmp_path, [row])
    logs_dir = tmp_path / "LOGS"

    with patch("djlib.reconvert._check_ffmpeg", return_value=True), \
         patch("djlib.reconvert.convert_to_aiff") as mock_conv:

        result = run_reconvert(
            csv_path=csv_path,
            logs_dir=logs_dir,
            state_path=logs_dir / "test.state",
        )

    mock_conv.assert_not_called()
    assert result.skipped_already_aiff == 1
    assert result.converted == 0

    # old_full_path should be updated to point at AIFF even when skipped
    from djlib.library_schema import load_library_csv
    rows = load_library_csv(csv_path)
    assert rows[0]["old_full_path"] == str(aiff)


def test_empty_aiff_is_not_skipped(tmp_path):
    """A zero-byte AIFF (crashed mid-write) is not treated as complete."""
    wav = tmp_path / "song.wav"
    wav.write_bytes(b"\x00" * 100)
    aiff = tmp_path / "song.aiff"
    aiff.write_bytes(b"")  # 0 bytes

    row = _lib_row(track_id="t1", old_full_path=str(wav))
    csv_path = _make_library_csv(tmp_path, [row])
    logs_dir = tmp_path / "LOGS"

    aiff_tmp = tmp_path / "tmp.aiff"
    aiff_tmp.write_bytes(b"\xFF" * 100)

    with patch("djlib.reconvert._check_ffmpeg", return_value=True), \
         patch("djlib.reconvert.convert_to_aiff", return_value=aiff_tmp):

        result = run_reconvert(
            csv_path=csv_path,
            logs_dir=logs_dir,
            state_path=logs_dir / "test.state",
        )

    assert result.converted == 1


# ── run_reconvert: resume ─────────────────────────────────────────────────────


def test_resume_skips_already_converted(tmp_path):
    """With --resume, tracks already in WAL as 'converted' are skipped."""
    wav = tmp_path / "song.wav"
    wav.write_bytes(b"\x00" * 100)
    aiff = tmp_path / "song.aiff"
    aiff.write_bytes(b"\xFF" * 200)

    row = _lib_row(track_id="t1", old_full_path=str(wav))
    csv_path = _make_library_csv(tmp_path, [row])
    logs_dir = tmp_path / "LOGS"
    logs_dir.mkdir(parents=True, exist_ok=True)
    state_path = logs_dir / "test.state"

    # Pre-populate WAL with a completed conversion
    wal = ReconvertState(state_path)
    wal.append_event("t1", RECONVERT_DONE, src=str(wav), dest=str(aiff))

    with patch("djlib.reconvert._check_ffmpeg", return_value=True), \
         patch("djlib.reconvert.convert_to_aiff") as mock_conv:

        result = run_reconvert(
            csv_path=csv_path,
            logs_dir=logs_dir,
            resume=True,
            state_path=state_path,
        )

    mock_conv.assert_not_called()
    assert result.skipped_wal == 1
    assert result.converted == 0


# ── run_reconvert: failure handling ──────────────────────────────────────────


def test_ffmpeg_failure_increments_failed(tmp_path):
    """If convert_to_aiff returns None, track is counted as failed."""
    wav = tmp_path / "song.wav"
    wav.write_bytes(b"\x00" * 100)
    row = _lib_row(track_id="t1", old_full_path=str(wav))
    csv_path = _make_library_csv(tmp_path, [row])
    logs_dir = tmp_path / "LOGS"

    with patch("djlib.reconvert._check_ffmpeg", return_value=True), \
         patch("djlib.reconvert.convert_to_aiff", return_value=None):

        result = run_reconvert(
            csv_path=csv_path,
            logs_dir=logs_dir,
            state_path=logs_dir / "test.state",
        )

    assert result.failed == 1
    assert result.converted == 0
    assert result.originals_to_delete == []

    # library.csv must NOT be updated for failed tracks
    from djlib.library_schema import load_library_csv
    rows = load_library_csv(csv_path)
    assert rows[0]["old_full_path"] == str(wav)


def test_wal_records_failed_event(tmp_path):
    """Failed conversion is recorded in WAL."""
    wav = tmp_path / "song.wav"
    wav.write_bytes(b"\x00" * 100)
    row = _lib_row(track_id="t1", old_full_path=str(wav))
    csv_path = _make_library_csv(tmp_path, [row])
    logs_dir = tmp_path / "LOGS"
    state_path = logs_dir / "test.state"

    with patch("djlib.reconvert._check_ffmpeg", return_value=True), \
         patch("djlib.reconvert.convert_to_aiff", return_value=None):
        run_reconvert(csv_path=csv_path, logs_dir=logs_dir, state_path=state_path)

    wal = ReconvertState(state_path)
    assert wal.get_track_states()["t1"] == RECONVERT_FAILED


def test_ffmpeg_not_found_raises_runtime_error(tmp_path):
    """Missing ffmpeg raises RuntimeError before touching any files."""
    wav = tmp_path / "song.wav"
    wav.write_bytes(b"\x00" * 100)
    row = _lib_row(track_id="t1", old_full_path=str(wav))
    csv_path = _make_library_csv(tmp_path, [row])

    with patch("djlib.reconvert._check_ffmpeg", return_value=False):
        with pytest.raises(RuntimeError, match="ffmpeg not found"):
            run_reconvert(csv_path=csv_path, logs_dir=tmp_path / "LOGS")


# ── run_reconvert: dry-run ─────────────────────────────────────────────────────


def test_dry_run_no_writes(tmp_path):
    """dry_run=True: no files created, no CSV changes, no WAL."""
    wav = tmp_path / "song.wav"
    wav.write_bytes(b"\x00" * 100)
    row = _lib_row(track_id="t1", old_full_path=str(wav))
    csv_path = _make_library_csv(tmp_path, [row])
    logs_dir = tmp_path / "LOGS"

    with patch("djlib.reconvert._check_ffmpeg", return_value=True), \
         patch("djlib.reconvert.convert_to_aiff") as mock_conv:

        result = run_reconvert(
            csv_path=csv_path,
            logs_dir=logs_dir,
            dry_run=True,
        )

    mock_conv.assert_not_called()
    assert result.converted == 0
    assert not (wav.with_suffix(".aiff")).exists()
    assert not list(logs_dir.glob("*.state")) if logs_dir.exists() else True

    from djlib.library_schema import load_library_csv
    rows = load_library_csv(csv_path)
    assert rows[0]["old_full_path"] == str(wav)


# ── run_reconvert: process lock ───────────────────────────────────────────────


def test_concurrent_run_raises(tmp_path):
    """Second run_reconvert call while lock is held raises RuntimeError."""
    wav = tmp_path / "song.wav"
    wav.write_bytes(b"\x00" * 100)
    row = _lib_row(track_id="t1", old_full_path=str(wav))
    csv_path = _make_library_csv(tmp_path, [row])
    logs_dir = tmp_path / "LOGS"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Hold the lock externally
    external_lock = ReconvertLock(logs_dir / "reconvert.lock")
    assert external_lock.acquire() is True

    try:
        with pytest.raises(RuntimeError, match="Another reconvert process"):
            run_reconvert(csv_path=csv_path, logs_dir=logs_dir)
    finally:
        external_lock.release()


# ── run_reconvert: multiple files ─────────────────────────────────────────────


def test_converts_multiple_files(tmp_path):
    """Multiple WAV/FLAC files in library — all converted."""
    files = [
        ("t1", tmp_path / "a.wav"),
        ("t2", tmp_path / "b.flac"),
        ("t3", tmp_path / "c.wav"),
    ]
    for tid, p in files:
        p.write_bytes(b"\x00" * 100)

    rows = [_lib_row(track_id=tid, old_full_path=str(p)) for tid, p in files]
    csv_path = _make_library_csv(tmp_path, rows)
    logs_dir = tmp_path / "LOGS"

    call_count = 0

    def fake_convert(src):
        nonlocal call_count
        call_count += 1
        tmp = tmp_path / f"tmp_{call_count}.aiff"
        tmp.write_bytes(b"\xFF" * 100)
        return tmp

    with patch("djlib.reconvert._check_ffmpeg", return_value=True), \
         patch("djlib.reconvert.convert_to_aiff", side_effect=fake_convert):

        result = run_reconvert(
            csv_path=csv_path,
            logs_dir=logs_dir,
            state_path=logs_dir / "test.state",
        )

    assert result.converted == 3
    assert result.failed == 0
    assert len(result.originals_to_delete) == 3

    from djlib.library_schema import load_library_csv
    lib_rows = load_library_csv(csv_path)
    for lib_row in lib_rows:
        assert lib_row["old_full_path"].endswith(".aiff")


def test_partial_failure_still_updates_successes(tmp_path):
    """If one file fails, others still get their paths updated in library.csv."""
    wav_ok  = tmp_path / "good.wav"
    wav_bad = tmp_path / "bad.wav"
    wav_ok.write_bytes(b"\x00" * 100)
    wav_bad.write_bytes(b"\x00" * 100)

    rows = [
        _lib_row(track_id="t1", old_full_path=str(wav_ok)),
        _lib_row(track_id="t2", old_full_path=str(wav_bad)),
    ]
    csv_path = _make_library_csv(tmp_path, rows)
    logs_dir = tmp_path / "LOGS"

    aiff_tmp = tmp_path / "tmp.aiff"
    aiff_tmp.write_bytes(b"\xFF" * 100)

    def fake_convert(src):
        if "bad" in str(src):
            return None
        aiff_tmp.write_bytes(b"\xFF" * 100)
        return aiff_tmp

    with patch("djlib.reconvert._check_ffmpeg", return_value=True), \
         patch("djlib.reconvert.convert_to_aiff", side_effect=fake_convert):

        result = run_reconvert(
            csv_path=csv_path,
            logs_dir=logs_dir,
            state_path=logs_dir / "test.state",
        )

    assert result.converted == 1
    assert result.failed == 1

    from djlib.library_schema import load_library_csv
    lib_rows = {r["track_id"]: r for r in load_library_csv(csv_path)}
    # Good track path updated
    assert lib_rows["t1"]["old_full_path"].endswith(".aiff")
    # Bad track path unchanged
    assert lib_rows["t2"]["old_full_path"] == str(wav_bad)


# ── live_location edge cases ──────────────────────────────────────────────────


def test_empty_live_location_treated_as_nas(tmp_path):
    """Legacy rows with empty live_location string are treated as on NAS."""
    wav = tmp_path / "legacy.wav"
    wav.write_bytes(b"\x00" * 100)
    row = _lib_row(track_id="t1", old_full_path=str(wav), live_location="")
    csv_path = _make_library_csv(tmp_path, [row])
    logs_dir = tmp_path / "LOGS"

    aiff_tmp = tmp_path / "tmp.aiff"
    aiff_tmp.write_bytes(b"\xFF" * 100)

    with patch("djlib.reconvert._check_ffmpeg", return_value=True), \
         patch("djlib.reconvert.convert_to_aiff", return_value=aiff_tmp):

        result = run_reconvert(
            csv_path=csv_path,
            logs_dir=logs_dir,
            state_path=logs_dir / "test.state",
        )

    assert result.converted == 1


def test_preparing_state_is_skipped(tmp_path):
    """Tracks with live_location=gig:xxx:preparing are skipped."""
    wav = tmp_path / "song.wav"
    wav.write_bytes(b"\x00" * 100)
    row = _lib_row(
        track_id="t1",
        old_full_path=str(wav),
        live_location="gig:friday-2026-05-15:preparing",
    )
    csv_path = _make_library_csv(tmp_path, [row])

    with patch("djlib.reconvert._check_ffmpeg", return_value=True), \
         patch("djlib.reconvert.convert_to_aiff") as mock_conv:

        result = run_reconvert(csv_path=csv_path, logs_dir=tmp_path / "LOGS")

    mock_conv.assert_not_called()
    assert result.converted == 0
