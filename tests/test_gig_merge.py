"""Tests for djlib.gig — gig-merge orchestrator (Phase 3)."""
from __future__ import annotations

import csv as csv_mod
import hashlib
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from djlib.gig import (
    GigDir,
    GigMergeResult,
    MERGE_FILE_COPIED,
    MERGE_ROW_MERGED,
    MERGE_SHA_MISMATCH,
    MERGE_NAS_MISSING,
    MergeState,
    run_gig_merge,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


LIBRARY_FIELDS = [
    "track_id", "live_location", "live_path", "old_full_path",
    "artist", "title", "bpm", "key", "rating", "play_count",
    "cue_points_rb", "cue_count",
]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_library_csv(tmp_path: Path, rows: list) -> Path:
    csv_path = tmp_path / "data" / "library.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = LIBRARY_FIELDS
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv_mod.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fieldnames})
    return csv_path


def _make_gig_dir(tmp_path: Path, gig_id: str, tracks: list) -> tuple:
    """Create a complete gig directory with gig.csv + manifest.json + audio files.

    Each item in tracks: dict with track_id, old_full_path, audio_data (bytes).
    Returns (gig_dir, audio_paths_by_tid).
    """
    gig_dir = GigDir(gig_id=gig_id, root=tmp_path / "Gigs")
    gig_dir.ensure()

    # Write audio files to MacBook audio dir
    audio_by_tid = {}
    manifest_tracks = []
    for t in tracks:
        tid = t["track_id"]
        data = t.get("audio_data", b"audio" * 500)
        audio_path = gig_dir.audio_dir / f"{tid}.mp3"
        audio_path.write_bytes(data)
        sha = _sha256(data)
        audio_by_tid[tid] = audio_path
        manifest_tracks.append({
            "track_id": tid,
            "src_path": t.get("old_full_path", f"/nas/{tid}.mp3"),
            "local_path": str(audio_path),
            "sha256": sha,
            "status": "committed",
        })

    # Write manifest.json
    manifest = {
        "gig_id": gig_id,
        "schema_version": LIBRARY_FIELDS,
        "created_at": "2026-05-11T12:00:00+00:00",
        "source": "test.m3u",
        "tracks": manifest_tracks,
    }
    with gig_dir.manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f)

    # Write gig.csv
    with gig_dir.gig_csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv_mod.DictWriter(f, fieldnames=LIBRARY_FIELDS, extrasaction="ignore")
        w.writeheader()
        for t in tracks:
            row = {k: t.get(k, "") for k in LIBRARY_FIELDS}
            row["live_location"] = f"gig:{gig_id}"
            row["live_path"] = str(audio_by_tid[t["track_id"]])
            w.writerow(row)

    return gig_dir, audio_by_tid


# ── GigDir.merge_state_path ───────────────────────────────────────────────────


def test_gig_dir_merge_state_path(tmp_path):
    gd = GigDir(gig_id="friday", root=tmp_path)
    assert gd.merge_state_path == tmp_path / "friday" / "merge.state"


# ── MergeState WAL ────────────────────────────────────────────────────────────


def test_merge_state_append_and_replay(tmp_path):
    ms = MergeState(tmp_path / "merge.state")
    ms.append_event("t1", MERGE_FILE_COPIED, src="/mac/t1.mp3", dest="/nas/t1.mp3")
    ms.append_event("t1", MERGE_ROW_MERGED)
    ms.append_event("t2", MERGE_FILE_COPIED)

    states = ms.get_track_states()
    assert states["t1"] == MERGE_ROW_MERGED   # last event wins
    assert states["t2"] == MERGE_FILE_COPIED


def test_merge_state_empty_returns_empty(tmp_path):
    ms = MergeState(tmp_path / "merge.state")
    assert ms.get_track_states() == {}


# ── run_gig_merge: missing gig.csv ───────────────────────────────────────────


def test_run_gig_merge_raises_if_no_gig_csv(tmp_path):
    csv_path = _make_library_csv(tmp_path, [])
    gig_dir = GigDir(gig_id="nogig", root=tmp_path / "Gigs")
    gig_dir.ensure()

    with pytest.raises(FileNotFoundError, match="gig.csv"):
        run_gig_merge("nogig", csv_path, gig_dir=gig_dir)


# ── run_gig_merge: dry run ────────────────────────────────────────────────────


def test_run_gig_merge_dry_run_no_writes(tmp_path, capsys):
    nas_file = tmp_path / "nas" / "t1.mp3"
    nas_file.parent.mkdir(parents=True)

    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "gig:dry-gig", "live_path": "",
         "old_full_path": str(nas_file)},
    ])
    tracks = [{"track_id": "t1", "old_full_path": str(nas_file)}]
    gig_dir, _ = _make_gig_dir(tmp_path, "dry-gig", tracks)

    with patch("djlib.rekordbox_reader.fetch_gig_tracks", return_value={}):
        result = run_gig_merge("dry-gig", csv_path, gig_dir=gig_dir, dry_run=True)

    # No actual copy
    assert not nas_file.exists()
    # No WAL written
    assert not gig_dir.merge_state_path.exists()
    out = capsys.readouterr().out
    assert "dry-run" in out.lower() or "dry_run" in out.lower() or "t1" in out


# ── run_gig_merge: happy path ─────────────────────────────────────────────────


def test_run_gig_merge_copies_file_to_nas(tmp_path):
    nas_file = tmp_path / "nas" / "t1.mp3"
    nas_file.parent.mkdir(parents=True)

    audio_data = b"audio_content" * 500
    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "gig:friday", "live_path": "",
         "old_full_path": str(nas_file), "rating": "51"},
    ])
    tracks = [{"track_id": "t1", "old_full_path": str(nas_file),
               "audio_data": audio_data, "rating": "51"}]
    gig_dir, _ = _make_gig_dir(tmp_path, "friday", tracks)

    with patch("djlib.rekordbox_reader.fetch_gig_tracks", return_value={}):
        result = run_gig_merge("friday", csv_path, gig_dir=gig_dir)

    assert nas_file.exists()
    assert nas_file.read_bytes() == audio_data
    assert result.merged == 1
    assert result.failed_sha == 0


def test_run_gig_merge_resets_live_location(tmp_path):
    nas_file = tmp_path / "nas" / "t1.mp3"
    nas_file.parent.mkdir(parents=True)

    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "gig:friday", "live_path": "/mac/t1.mp3",
         "old_full_path": str(nas_file)},
    ])
    tracks = [{"track_id": "t1", "old_full_path": str(nas_file)}]
    gig_dir, _ = _make_gig_dir(tmp_path, "friday", tracks)

    with patch("djlib.rekordbox_reader.fetch_gig_tracks", return_value={}):
        run_gig_merge("friday", csv_path, gig_dir=gig_dir)

    with csv_path.open() as f:
        rows = list(csv_mod.DictReader(f))
    assert rows[0]["live_location"] == "nas"
    assert rows[0]["live_path"] == ""


def test_run_gig_merge_applies_lww_from_rekordbox(tmp_path):
    nas_file = tmp_path / "nas" / "t1.mp3"
    nas_file.parent.mkdir(parents=True)

    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "gig:friday", "live_path": "",
         "old_full_path": str(nas_file), "rating": "51", "play_count": "3"},
    ])
    tracks = [{"track_id": "t1", "old_full_path": str(nas_file),
               "rating": "51", "play_count": "3"}]
    gig_dir, _ = _make_gig_dir(tmp_path, "friday", tracks)

    fresh = {"t1": {"rating": "204", "play_count": "7", "cue_points_rb": "", "bpm": ""}}
    with patch("djlib.rekordbox_reader.fetch_gig_tracks", return_value=fresh):
        run_gig_merge("friday", csv_path, gig_dir=gig_dir)

    with csv_path.open() as f:
        rows = list(csv_mod.DictReader(f))
    row = rows[0]
    assert row["rating"] == "204"
    assert row["play_count"] == "7"


# ── run_gig_merge: idempotent ─────────────────────────────────────────────────


def test_run_gig_merge_idempotent_already_on_nas(tmp_path):
    nas_file = tmp_path / "nas" / "t1.mp3"
    nas_file.parent.mkdir(parents=True)
    nas_file.write_bytes(b"already_there")

    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas", "live_path": "",
         "old_full_path": str(nas_file)},
    ])
    tracks = [{"track_id": "t1", "old_full_path": str(nas_file)}]
    gig_dir, _ = _make_gig_dir(tmp_path, "friday", tracks)

    original_mtime = nas_file.stat().st_mtime

    with patch("djlib.rekordbox_reader.fetch_gig_tracks", return_value={}):
        result = run_gig_merge("friday", csv_path, gig_dir=gig_dir)

    assert result.skipped_already_merged == 1
    assert result.merged == 0
    # NAS file untouched
    assert nas_file.stat().st_mtime == original_mtime


# ── run_gig_merge: SHA mismatch ───────────────────────────────────────────────


def test_run_gig_merge_sha_mismatch_aborts_track(tmp_path):
    nas_file = tmp_path / "nas" / "t1.mp3"
    nas_file.parent.mkdir(parents=True)

    # Write audio file with known content
    audio_data = b"original_audio" * 100
    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "gig:friday", "live_path": "",
         "old_full_path": str(nas_file)},
    ])
    tracks = [{"track_id": "t1", "old_full_path": str(nas_file),
               "audio_data": audio_data}]
    gig_dir, audio_by_tid = _make_gig_dir(tmp_path, "friday", tracks)

    # Corrupt the MacBook file after manifest was written
    audio_by_tid["t1"].write_bytes(b"corrupted!" * 100)

    with patch("djlib.rekordbox_reader.fetch_gig_tracks", return_value={}):
        result = run_gig_merge("friday", csv_path, gig_dir=gig_dir)

    assert result.failed_sha == 1
    assert result.merged == 0
    assert not nas_file.exists()  # nothing copied to NAS

    # live_location stays "gig:friday" — guard still active
    with csv_path.open() as f:
        rows = list(csv_mod.DictReader(f))
    assert rows[0]["live_location"] == "gig:friday"


# ── run_gig_merge: NAS directory missing ─────────────────────────────────────


def test_run_gig_merge_nas_missing_aborts_by_default(tmp_path):
    nas_file = tmp_path / "nas_gone" / "subdir" / "t1.mp3"
    # Parent does NOT exist

    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "gig:friday", "live_path": "",
         "old_full_path": str(nas_file)},
    ])
    tracks = [{"track_id": "t1", "old_full_path": str(nas_file)}]
    gig_dir, _ = _make_gig_dir(tmp_path, "friday", tracks)

    with patch("djlib.rekordbox_reader.fetch_gig_tracks", return_value={}):
        result = run_gig_merge("friday", csv_path, gig_dir=gig_dir)

    assert result.failed_nas_missing == 1
    assert not nas_file.exists()


def test_run_gig_merge_create_missing_dirs_flag(tmp_path):
    nas_file = tmp_path / "nas_new" / "subdir" / "t1.mp3"
    # Parent does NOT exist yet

    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "gig:friday", "live_path": "",
         "old_full_path": str(nas_file)},
    ])
    tracks = [{"track_id": "t1", "old_full_path": str(nas_file)}]
    gig_dir, _ = _make_gig_dir(tmp_path, "friday", tracks)

    with patch("djlib.rekordbox_reader.fetch_gig_tracks", return_value={}):
        result = run_gig_merge("friday", csv_path, gig_dir=gig_dir,
                               create_missing_dirs=True)

    assert nas_file.exists()
    assert result.merged == 1


# ── run_gig_merge: resume ─────────────────────────────────────────────────────


def test_run_gig_merge_resume_skips_already_merged(tmp_path):
    nas_file = tmp_path / "nas" / "t1.mp3"
    nas_file.parent.mkdir(parents=True)

    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "gig:friday", "live_path": "",
         "old_full_path": str(nas_file)},
    ])
    tracks = [{"track_id": "t1", "old_full_path": str(nas_file)}]
    gig_dir, _ = _make_gig_dir(tmp_path, "friday", tracks)

    # Pre-populate WAL as if track was already fully merged
    ms = MergeState(gig_dir.merge_state_path)
    ms.append_event("t1", MERGE_FILE_COPIED)
    ms.append_event("t1", MERGE_ROW_MERGED)

    copy_call_count = []
    original_copy = __import__("djlib.gig", fromlist=["copy_track_atomic"]).copy_track_atomic

    with patch("djlib.rekordbox_reader.fetch_gig_tracks", return_value={}):
        result = run_gig_merge("friday", csv_path, gig_dir=gig_dir, resume=True)

    assert result.skipped_already_merged == 1
    assert result.merged == 0


# ── run_gig_merge: quarantine ─────────────────────────────────────────────────


def test_run_gig_merge_quarantines_unknown_files(tmp_path):
    nas_file = tmp_path / "nas" / "t1.mp3"
    nas_file.parent.mkdir(parents=True)

    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "gig:friday", "live_path": "",
         "old_full_path": str(nas_file)},
    ])
    tracks = [{"track_id": "t1", "old_full_path": str(nas_file)}]
    gig_dir, _ = _make_gig_dir(tmp_path, "friday", tracks)

    # Plant an unknown file in audio/
    unknown = gig_dir.audio_dir / "unknown-track-xyz.mp3"
    unknown.write_bytes(b"mystery music" * 100)

    quarantine_root = tmp_path / "quarantine"
    with patch("djlib.rekordbox_reader.fetch_gig_tracks", return_value={}):
        result = run_gig_merge("friday", csv_path, gig_dir=gig_dir,
                               quarantine_root=quarantine_root)

    assert result.quarantined == 1
    assert not unknown.exists()  # moved out of audio/
    assert (quarantine_root / "friday" / "unknown-track-xyz.mp3").exists()


# ── run_gig_merge: backup ─────────────────────────────────────────────────────


def test_run_gig_merge_writes_backup(tmp_path):
    nas_file = tmp_path / "nas" / "t1.mp3"
    nas_file.parent.mkdir(parents=True)

    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "gig:friday", "live_path": "",
         "old_full_path": str(nas_file)},
    ])
    tracks = [{"track_id": "t1", "old_full_path": str(nas_file)}]
    gig_dir, _ = _make_gig_dir(tmp_path, "friday", tracks)

    logs_dir = tmp_path / "LOGS"

    with patch("djlib.rekordbox_reader.fetch_gig_tracks", return_value={}):
        run_gig_merge("friday", csv_path, gig_dir=gig_dir)

    backups = list(logs_dir.glob("library-backup-friday-*.csv"))
    assert len(backups) == 1


# ── run_gig_merge: audit log ──────────────────────────────────────────────────


def test_run_gig_merge_writes_audit_log_on_changes(tmp_path):
    nas_file = tmp_path / "nas" / "t1.mp3"
    nas_file.parent.mkdir(parents=True)

    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "gig:friday", "live_path": "",
         "old_full_path": str(nas_file), "rating": "51"},
    ])
    tracks = [{"track_id": "t1", "old_full_path": str(nas_file), "rating": "51"}]
    gig_dir, _ = _make_gig_dir(tmp_path, "friday", tracks)

    fresh = {"t1": {"rating": "204", "play_count": "", "cue_points_rb": "", "bpm": ""}}
    with patch("djlib.rekordbox_reader.fetch_gig_tracks", return_value=fresh):
        run_gig_merge("friday", csv_path, gig_dir=gig_dir)

    logs_dir = tmp_path / "LOGS"
    audit_files = list(logs_dir.glob("gig-merge-friday-*.csv"))
    assert len(audit_files) == 1
    with audit_files[0].open() as f:
        rows = list(csv_mod.DictReader(f))
    fields_logged = {r["field_name"] for r in rows}
    assert "rating" in fields_logged
