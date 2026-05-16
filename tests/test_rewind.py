"""Tests for djlib/rewind.py — library WAV/FLAC → AIFF rewind to unsorted."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List
from unittest.mock import patch, call

import pytest

from djlib.rewind import (
    RewindState,
    RewindLock,
    RewindResult,
    run_rewind,
    REWIND_DONE,
    REWIND_FAILED,
    REWIND_HASHED,
    REWIND_VERIFIED,
    REWIND_CONVERTED,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_library_csv(tmp_path: Path, rows: List[Dict]) -> Path:
    from djlib.library_schema import save_library_csv
    csv_path = tmp_path / "library.csv"
    save_library_csv(csv_path, rows)
    return csv_path


def _lib_row(
    track_id: str = "t1",
    old_full_path: str = "",
    original_path: str = "",
    live_location: str = "nas",
) -> Dict:
    return {
        "track_id": track_id,
        "old_full_path": old_full_path,
        "original_path": original_path,
        "live_location": live_location,
    }


def _setup(tmp_path: Path, filename: str = "song.wav", live_location: str = "nas"):
    """Create a WAV file in a fake library dir and a library.csv pointing to it."""
    lib_dir = tmp_path / "library"
    lib_dir.mkdir()
    src = lib_dir / filename
    src.write_bytes(b"\x00" * 200)

    unsorted = tmp_path / "unsorted"
    unsorted.mkdir()
    logs = tmp_path / "LOGS"

    row = _lib_row(track_id="t1", old_full_path=str(src), live_location=live_location)
    csv_path = _make_library_csv(tmp_path, [row])
    return csv_path, src, unsorted, logs


# ── RewindState (WAL) ─────────────────────────────────────────────────────────


def test_wal_empty(tmp_path):
    wal = RewindState(tmp_path / "test.wal.jsonl")
    assert wal.get_track_states() == {}


def test_wal_records_events(tmp_path):
    wal = RewindState(tmp_path / "test.wal.jsonl")
    wal.append_event("t1", REWIND_HASHED, audio_sha256="abc")
    wal.append_event("t1", REWIND_DONE)
    wal.append_event("t2", REWIND_FAILED, reason="x")

    states = wal.get_track_states()
    assert states["t1"] == REWIND_DONE
    assert states["t2"] == REWIND_FAILED


def test_wal_tolerates_corrupt_line(tmp_path):
    p = tmp_path / "test.wal.jsonl"
    p.write_text('{"track_id":"t1","event":"done","ts":"x"}\nBAD_JSON\n')
    wal = RewindState(p)
    assert wal.get_track_states()["t1"] == "done"


# ── RewindLock ────────────────────────────────────────────────────────────────


def test_lock_acquire_release(tmp_path):
    lock = RewindLock(tmp_path / "rewind.lock")
    assert lock.acquire() is True
    lock.release()


def test_lock_blocks_second(tmp_path):
    l1 = RewindLock(tmp_path / "rewind.lock")
    l2 = RewindLock(tmp_path / "rewind.lock")
    assert l1.acquire() is True
    assert l2.acquire() is False
    l1.release()


# ── run_rewind: no candidates ─────────────────────────────────────────────────


def test_no_candidates(tmp_path):
    """Library with only MP3s — nothing to rewind."""
    lib = tmp_path / "library"
    lib.mkdir()
    mp3 = lib / "song.mp3"
    mp3.write_bytes(b"\x00" * 100)
    row = _lib_row(track_id="t1", old_full_path=str(mp3))
    csv_path = _make_library_csv(tmp_path, [row])

    with patch("djlib.rewind._check_ffmpeg_or_raise"):
        result = run_rewind(
            csv_path=csv_path,
            unsorted_dir=tmp_path / "unsorted",
            logs_dir=tmp_path / "LOGS",
        )
    assert result.rewound == 0


def test_skips_gig_tracks(tmp_path):
    """live_location=gig:xxx skips the track."""
    csv_path, src, unsorted, logs = _setup(tmp_path, live_location="gig:friday-2026-05-15")

    with patch("djlib.rewind._check_ffmpeg_or_raise"):
        result = run_rewind(csv_path=csv_path, unsorted_dir=unsorted, logs_dir=logs)

    assert result.rewound == 0
    assert src.exists()


def test_skips_missing_file(tmp_path):
    """Candidate path in library.csv but file deleted — skip."""
    row = _lib_row(track_id="t1", old_full_path=str(tmp_path / "library" / "ghost.wav"))
    csv_path = _make_library_csv(tmp_path, [row])

    with patch("djlib.rewind._check_ffmpeg_or_raise"):
        result = run_rewind(
            csv_path=csv_path,
            unsorted_dir=tmp_path / "unsorted",
            logs_dir=tmp_path / "LOGS",
        )
    assert result.rewound == 0


# ── run_rewind: happy path ────────────────────────────────────────────────────


def test_rewind_wav_happy_path(tmp_path):
    """WAV converted, verified, moved to unsorted; original in .originals/."""
    csv_path, src, unsorted, logs = _setup(tmp_path, "song.wav")
    audio_hash = "deadbeef" * 8  # 64-char fake SHA256

    with patch("djlib.rewind._check_ffmpeg_or_raise"), \
         patch("djlib.rewind.audio_sha256", return_value=audio_hash), \
         patch("djlib.rewind.convert_to_aiff") as mock_conv:

        tmp_aiff = tmp_path / "tmp.aiff"
        tmp_aiff.write_bytes(b"\xFF" * 200)
        mock_conv.return_value = tmp_aiff

        result = run_rewind(
            csv_path=csv_path,
            unsorted_dir=unsorted,
            logs_dir=logs,
            wal_path=logs / "test.wal.jsonl",
        )

    assert result.rewound == 1
    assert result.failed_hash_mismatch == 0
    assert result.failed_conversion == 0

    # AIFF in unsorted
    assert (unsorted / "song.aiff").exists()

    # Original moved to originals dir (sibling of unsorted)
    from djlib.rewind import _originals_dir
    assert (_originals_dir(unsorted) / "song.wav").exists()
    assert not src.exists()

    # Row removed from library.csv
    from djlib.library_schema import load_library_csv
    rows = load_library_csv(csv_path)
    assert not any(r["track_id"] == "t1" for r in rows)


def test_rewind_flac_happy_path(tmp_path):
    """FLAC file is also rewound correctly."""
    csv_path, src, unsorted, logs = _setup(tmp_path, "song.flac")
    audio_hash = "cafebabe" * 8

    with patch("djlib.rewind._check_ffmpeg_or_raise"), \
         patch("djlib.rewind.audio_sha256", return_value=audio_hash), \
         patch("djlib.rewind.convert_to_aiff") as mock_conv:

        tmp_aiff = tmp_path / "tmp.aiff"
        tmp_aiff.write_bytes(b"\xFF" * 200)
        mock_conv.return_value = tmp_aiff

        result = run_rewind(
            csv_path=csv_path,
            unsorted_dir=unsorted,
            logs_dir=logs,
            wal_path=logs / "test.wal.jsonl",
        )

    assert result.rewound == 1
    assert (unsorted / "song.aiff").exists()
    from djlib.rewind import _originals_dir
    assert (_originals_dir(unsorted) / "song.flac").exists()


def test_wal_written_on_success(tmp_path):
    """WAL records hashed → converted → verified → done."""
    csv_path, src, unsorted, logs = _setup(tmp_path)
    audio_hash = "aabb" * 16
    wal_path = logs / "test.wal.jsonl"

    with patch("djlib.rewind._check_ffmpeg_or_raise"), \
         patch("djlib.rewind.audio_sha256", return_value=audio_hash), \
         patch("djlib.rewind.convert_to_aiff") as mock_conv:

        tmp_aiff = tmp_path / "tmp.aiff"
        tmp_aiff.write_bytes(b"\xFF" * 100)
        mock_conv.return_value = tmp_aiff

        run_rewind(csv_path=csv_path, unsorted_dir=unsorted, logs_dir=logs, wal_path=wal_path)

    wal = RewindState(wal_path)
    assert wal.get_track_states()["t1"] == REWIND_DONE


# ── run_rewind: failure handling ──────────────────────────────────────────────


def test_hash_mismatch_aborts_file(tmp_path):
    """If audio hash doesn't match after conversion, file is NOT moved."""
    csv_path, src, unsorted, logs = _setup(tmp_path)

    call_count = 0
    def fake_hash(path):
        nonlocal call_count
        call_count += 1
        return "hash_src" if call_count == 1 else "hash_aiff_different"

    with patch("djlib.rewind._check_ffmpeg_or_raise"), \
         patch("djlib.rewind.audio_sha256", side_effect=fake_hash), \
         patch("djlib.rewind.convert_to_aiff") as mock_conv:

        tmp_aiff = tmp_path / "tmp.aiff"
        tmp_aiff.write_bytes(b"\xFF" * 100)
        mock_conv.return_value = tmp_aiff

        result = run_rewind(
            csv_path=csv_path,
            unsorted_dir=unsorted,
            logs_dir=logs,
            wal_path=logs / "test.wal.jsonl",
        )

    assert result.failed_hash_mismatch == 1
    assert result.rewound == 0
    assert src.exists()  # original untouched
    assert not (unsorted / "song.aiff").exists()

    # library.csv unchanged
    from djlib.library_schema import load_library_csv
    rows = load_library_csv(csv_path)
    assert any(r["track_id"] == "t1" for r in rows)


def test_conversion_failure_skips_file(tmp_path):
    """convert_to_aiff returns None → counted as failed, original untouched."""
    csv_path, src, unsorted, logs = _setup(tmp_path)

    with patch("djlib.rewind._check_ffmpeg_or_raise"), \
         patch("djlib.rewind.audio_sha256", return_value="abc" * 20), \
         patch("djlib.rewind.convert_to_aiff", return_value=None):

        result = run_rewind(
            csv_path=csv_path,
            unsorted_dir=unsorted,
            logs_dir=logs,
            wal_path=logs / "test.wal.jsonl",
        )

    assert result.failed_conversion == 1
    assert result.rewound == 0
    assert src.exists()


def test_hash_failure_skips_file(tmp_path):
    """audio_sha256 returns None for source → skip with failed_other."""
    csv_path, src, unsorted, logs = _setup(tmp_path)

    with patch("djlib.rewind._check_ffmpeg_or_raise"), \
         patch("djlib.rewind.audio_sha256", return_value=None):

        result = run_rewind(
            csv_path=csv_path,
            unsorted_dir=unsorted,
            logs_dir=logs,
            wal_path=logs / "test.wal.jsonl",
        )

    assert result.failed_other == 1
    assert result.rewound == 0


def test_ffmpeg_missing_raises(tmp_path):
    """Missing ffmpeg raises RuntimeError before touching anything."""
    csv_path, src, unsorted, logs = _setup(tmp_path)

    with patch("djlib.rewind._check_ffmpeg_or_raise", side_effect=RuntimeError("ffmpeg not found")):
        with pytest.raises(RuntimeError, match="ffmpeg not found"):
            run_rewind(csv_path=csv_path, unsorted_dir=unsorted, logs_dir=logs)


# ── run_rewind: dry-run ───────────────────────────────────────────────────────


def test_dry_run_no_writes(tmp_path):
    """dry_run=True: no files moved, no CSV changes, no WAL."""
    csv_path, src, unsorted, logs = _setup(tmp_path)

    with patch("djlib.rewind.convert_to_aiff") as mock_conv:
        result = run_rewind(
            csv_path=csv_path,
            unsorted_dir=unsorted,
            logs_dir=logs,
            dry_run=True,
        )

    mock_conv.assert_not_called()
    assert result.rewound == 0
    assert src.exists()
    assert not (unsorted / "song.aiff").exists()

    from djlib.library_schema import load_library_csv
    rows = load_library_csv(csv_path)
    assert any(r["track_id"] == "t1" for r in rows)


# ── run_rewind: resume ───────────────────────────────────────────────────────


def test_resume_skips_done(tmp_path):
    """With --resume, tracks with WAL event 'done' are skipped."""
    csv_path, src, unsorted, logs = _setup(tmp_path)
    logs.mkdir(parents=True, exist_ok=True)
    wal_path = logs / "test.wal.jsonl"

    # Pre-populate WAL
    wal = RewindState(wal_path)
    wal.append_event("t1", REWIND_DONE)

    # AIFF already in unsorted (consistent with done state)
    aiff = unsorted / "song.aiff"
    unsorted.mkdir(parents=True, exist_ok=True)
    aiff.write_bytes(b"\xFF" * 100)

    with patch("djlib.rewind._check_ffmpeg_or_raise"), \
         patch("djlib.rewind.convert_to_aiff") as mock_conv:

        result = run_rewind(
            csv_path=csv_path,
            unsorted_dir=unsorted,
            logs_dir=logs,
            resume=True,
            wal_path=wal_path,
        )

    mock_conv.assert_not_called()
    assert result.skipped_wal == 1
    assert result.rewound == 0

    # Row still removed from library.csv (was done)
    from djlib.library_schema import load_library_csv
    rows = load_library_csv(csv_path)
    assert not any(r["track_id"] == "t1" for r in rows)


# ── run_rewind: process lock ──────────────────────────────────────────────────


def test_concurrent_run_raises(tmp_path):
    """Second run_rewind while lock held raises RuntimeError."""
    csv_path, src, unsorted, logs = _setup(tmp_path)
    logs.mkdir(parents=True, exist_ok=True)

    external_lock = RewindLock(logs / "rewind.lock")
    assert external_lock.acquire() is True

    try:
        with pytest.raises(RuntimeError, match="Another rewind process"):
            run_rewind(csv_path=csv_path, unsorted_dir=unsorted, logs_dir=logs)
    finally:
        external_lock.release()


# ── run_rewind: multiple files + partial failure ──────────────────────────────


def test_partial_failure_still_rewounds_successes(tmp_path):
    """If one file fails verification, others still get rewound."""
    lib = tmp_path / "library"
    lib.mkdir()
    wav_ok  = lib / "good.wav"
    wav_bad = lib / "bad.wav"
    wav_ok.write_bytes(b"\x00" * 100)
    wav_bad.write_bytes(b"\x00" * 100)

    rows = [
        _lib_row(track_id="t1", old_full_path=str(wav_ok)),
        _lib_row(track_id="t2", old_full_path=str(wav_bad)),
    ]
    csv_path = _make_library_csv(tmp_path, rows)
    unsorted = tmp_path / "unsorted"
    logs = tmp_path / "LOGS"

    call_count = 0
    def fake_hash(path):
        nonlocal call_count
        call_count += 1
        # src hash for good: "aaa...", aiff hash: "aaa..." (match)
        # src hash for bad: "bbb...", aiff hash: "ccc..." (mismatch)
        if "bad" in str(path):
            return "bbb" * 21 + "b" if call_count % 2 == 1 else "ccc" * 21 + "c"
        return "aaa" * 21 + "a"

    tmp_aiff = tmp_path / "tmp.aiff"

    def fake_convert(src):
        tmp_aiff.write_bytes(b"\xFF" * 100)
        return tmp_aiff

    with patch("djlib.rewind._check_ffmpeg_or_raise"), \
         patch("djlib.rewind.audio_sha256", side_effect=fake_hash), \
         patch("djlib.rewind.convert_to_aiff", side_effect=fake_convert):

        result = run_rewind(
            csv_path=csv_path,
            unsorted_dir=unsorted,
            logs_dir=logs,
            wal_path=logs / "test.wal.jsonl",
        )

    assert result.rewound == 1
    assert result.failed_hash_mismatch == 1

    from djlib.library_schema import load_library_csv
    lib_rows = {r["track_id"]: r for r in load_library_csv(csv_path)}
    assert "t1" not in lib_rows       # rewound, removed
    assert "t2" in lib_rows           # failed, kept


# ── run_rewind: filename collision in unsorted ────────────────────────────────


def test_collision_in_unsorted_renames(tmp_path):
    """If AIFF already exists in unsorted, the new one gets a (2) suffix."""
    csv_path, src, unsorted, logs = _setup(tmp_path, "song.wav")
    unsorted.mkdir(parents=True, exist_ok=True)
    # Pre-existing AIFF at the destination
    existing = unsorted / "song.aiff"
    existing.write_bytes(b"\xAA" * 100)

    audio_hash = "deadbeef" * 8

    with patch("djlib.rewind._check_ffmpeg_or_raise"), \
         patch("djlib.rewind.audio_sha256", return_value=audio_hash), \
         patch("djlib.rewind.convert_to_aiff") as mock_conv:

        tmp_aiff = tmp_path / "tmp.aiff"
        tmp_aiff.write_bytes(b"\xFF" * 200)
        mock_conv.return_value = tmp_aiff

        result = run_rewind(
            csv_path=csv_path,
            unsorted_dir=unsorted,
            logs_dir=logs,
            wal_path=logs / "test.wal.jsonl",
        )

    assert result.rewound == 1
    # Original AIFF untouched, new one renamed
    assert existing.exists()
    assert (unsorted / "song (2).aiff").exists()


# ── live_location edge case ───────────────────────────────────────────────────


def test_empty_live_location_treated_as_nas(tmp_path):
    """Empty live_location string (legacy row) is treated as on NAS → eligible."""
    csv_path, src, unsorted, logs = _setup(tmp_path, live_location="")
    audio_hash = "ff" * 32

    with patch("djlib.rewind._check_ffmpeg_or_raise"), \
         patch("djlib.rewind.audio_sha256", return_value=audio_hash), \
         patch("djlib.rewind.convert_to_aiff") as mock_conv:

        tmp_aiff = tmp_path / "tmp.aiff"
        tmp_aiff.write_bytes(b"\xFF" * 100)
        mock_conv.return_value = tmp_aiff

        result = run_rewind(
            csv_path=csv_path,
            unsorted_dir=unsorted,
            logs_dir=logs,
            wal_path=logs / "test.wal.jsonl",
        )

    assert result.rewound == 1


# ── fallback path fields ──────────────────────────────────────────────────────


def test_fallback_to_original_path(tmp_path):
    """If old_full_path is empty but original_path has a WAV, use that."""
    lib = tmp_path / "library"
    lib.mkdir()
    wav = lib / "song.wav"
    wav.write_bytes(b"\x00" * 100)

    row = {
        "track_id": "t1",
        "old_full_path": "",
        "original_path": str(wav),
        "live_location": "nas",
    }
    csv_path = _make_library_csv(tmp_path, [row])
    unsorted = tmp_path / "unsorted"
    logs = tmp_path / "LOGS"
    audio_hash = "ee" * 32

    with patch("djlib.rewind._check_ffmpeg_or_raise"), \
         patch("djlib.rewind.audio_sha256", return_value=audio_hash), \
         patch("djlib.rewind.convert_to_aiff") as mock_conv:

        tmp_aiff = tmp_path / "tmp.aiff"
        tmp_aiff.write_bytes(b"\xFF" * 100)
        mock_conv.return_value = tmp_aiff

        result = run_rewind(
            csv_path=csv_path,
            unsorted_dir=unsorted,
            logs_dir=logs,
            wal_path=logs / "test.wal.jsonl",
        )

    assert result.rewound == 1
