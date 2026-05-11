"""Tests for djlib.gig — gig-cleanup (Phase 4)."""
from __future__ import annotations

import csv as csv_mod
import hashlib
import json
from pathlib import Path

import pytest

from djlib.gig import GigCleanupResult, GigDir, run_gig_cleanup


# ── Helpers ───────────────────────────────────────────────────────────────────


LIBRARY_FIELDS = [
    "track_id", "live_location", "live_path", "old_full_path",
    "artist", "title", "bpm", "rating", "play_count", "cue_points_rb",
]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_library_csv(tmp_path: Path, rows: list) -> Path:
    csv_path = tmp_path / "data" / "library.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv_mod.DictWriter(f, fieldnames=LIBRARY_FIELDS, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in LIBRARY_FIELDS})
    return csv_path


def _make_gig_dir(tmp_path: Path, gig_id: str, tracks: list) -> tuple:
    """Create gig directory with gig.csv + manifest.json + MacBook audio files.

    Each item in tracks: dict with track_id, old_full_path (NAS path), audio_data.
    Returns (gig_dir, audio_paths_by_tid).
    """
    gig_dir = GigDir(gig_id=gig_id, root=tmp_path / "Gigs")
    gig_dir.ensure()

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

    manifest = {
        "gig_id": gig_id,
        "schema_version": LIBRARY_FIELDS,
        "created_at": "2026-05-11T12:00:00+00:00",
        "source": "test.m3u",
        "tracks": manifest_tracks,
    }
    with gig_dir.manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f)

    with gig_dir.gig_csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv_mod.DictWriter(f, fieldnames=LIBRARY_FIELDS, extrasaction="ignore")
        w.writeheader()
        for t in tracks:
            row = {k: t.get(k, "") for k in LIBRARY_FIELDS}
            row["live_location"] = "nas"
            row["live_path"] = ""
            w.writerow(row)

    return gig_dir, audio_by_tid


# ── Missing gig.csv ───────────────────────────────────────────────────────────


def test_run_gig_cleanup_raises_if_no_gig_csv(tmp_path):
    csv_path = _make_library_csv(tmp_path, [])
    gig_dir = GigDir(gig_id="nogig", root=tmp_path / "Gigs")
    gig_dir.ensure()

    with pytest.raises(FileNotFoundError, match="gig.csv"):
        run_gig_cleanup("nogig", csv_path, gig_dir=gig_dir)


# ── Guard: not all merged ─────────────────────────────────────────────────────


def test_run_gig_cleanup_aborts_if_track_not_on_nas(tmp_path):
    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "gig:friday"},  # still on gig
    ])
    tracks = [{"track_id": "t1"}]
    gig_dir, audio_by_tid = _make_gig_dir(tmp_path, "friday", tracks)

    result = run_gig_cleanup("friday", csv_path, gig_dir=gig_dir)

    assert result.not_merged == 1
    assert result.deleted_files == 0
    assert audio_by_tid["t1"].exists()  # MacBook file untouched


def test_run_gig_cleanup_aborts_if_any_track_not_merged(tmp_path):
    """If only one of two tracks is still on gig, both files are kept."""
    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas"},
        {"track_id": "t2", "live_location": "gig:friday"},  # not merged
    ])
    tracks = [{"track_id": "t1"}, {"track_id": "t2"}]
    gig_dir, audio_by_tid = _make_gig_dir(tmp_path, "friday", tracks)

    result = run_gig_cleanup("friday", csv_path, gig_dir=gig_dir)

    assert result.not_merged == 1
    assert result.deleted_files == 0
    assert audio_by_tid["t1"].exists()
    assert audio_by_tid["t2"].exists()


# ── Happy path ────────────────────────────────────────────────────────────────


def test_run_gig_cleanup_deletes_audio_files(tmp_path):
    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas"},
    ])
    tracks = [{"track_id": "t1"}]
    gig_dir, audio_by_tid = _make_gig_dir(tmp_path, "friday", tracks)

    result = run_gig_cleanup("friday", csv_path, gig_dir=gig_dir)

    assert result.deleted_files == 1
    assert result.not_merged == 0
    assert not audio_by_tid["t1"].exists()


def test_run_gig_cleanup_removes_audio_dir_when_empty(tmp_path):
    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas"},
    ])
    tracks = [{"track_id": "t1"}]
    gig_dir, _ = _make_gig_dir(tmp_path, "friday", tracks)

    run_gig_cleanup("friday", csv_path, gig_dir=gig_dir)

    assert not gig_dir.audio_dir.exists()


def test_run_gig_cleanup_keeps_other_gig_files(tmp_path):
    """manifest.json, gig.csv, prep.state must survive cleanup."""
    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas"},
    ])
    tracks = [{"track_id": "t1"}]
    gig_dir, _ = _make_gig_dir(tmp_path, "friday", tracks)
    (gig_dir.path / "prep.state").write_text("")
    (gig_dir.path / "merge.state").write_text("")

    run_gig_cleanup("friday", csv_path, gig_dir=gig_dir)

    assert gig_dir.manifest_path.exists()
    assert gig_dir.gig_csv_path.exists()
    assert (gig_dir.path / "prep.state").exists()
    assert (gig_dir.path / "merge.state").exists()


def test_run_gig_cleanup_multiple_tracks(tmp_path):
    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas"},
        {"track_id": "t2", "live_location": "nas"},
        {"track_id": "t3", "live_location": "nas"},
    ])
    tracks = [{"track_id": "t1"}, {"track_id": "t2"}, {"track_id": "t3"}]
    gig_dir, audio_by_tid = _make_gig_dir(tmp_path, "friday", tracks)

    result = run_gig_cleanup("friday", csv_path, gig_dir=gig_dir)

    assert result.deleted_files == 3
    for af in audio_by_tid.values():
        assert not af.exists()


def test_run_gig_cleanup_idempotent_already_deleted(tmp_path):
    """Running cleanup twice should not fail — already-deleted files are skipped."""
    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas"},
    ])
    tracks = [{"track_id": "t1"}]
    gig_dir, audio_by_tid = _make_gig_dir(tmp_path, "friday", tracks)

    run_gig_cleanup("friday", csv_path, gig_dir=gig_dir)
    # Re-create gig_dir structure without the audio file
    result2 = run_gig_cleanup("friday", csv_path, gig_dir=gig_dir)

    assert result2.not_merged == 0
    assert result2.deleted_files == 0  # nothing left to delete


# ── Dry run ───────────────────────────────────────────────────────────────────


def test_run_gig_cleanup_dry_run_no_deletes(tmp_path, capsys):
    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas"},
    ])
    tracks = [{"track_id": "t1"}]
    gig_dir, audio_by_tid = _make_gig_dir(tmp_path, "friday", tracks)

    result = run_gig_cleanup("friday", csv_path, gig_dir=gig_dir, dry_run=True)

    assert result.deleted_files == 1  # counted but not actually deleted
    assert audio_by_tid["t1"].exists()  # file still there
    assert gig_dir.audio_dir.exists()  # dir still there
    out = capsys.readouterr().out
    assert "dry-run" in out


# ── verify_nas ────────────────────────────────────────────────────────────────


def test_run_gig_cleanup_verify_nas_happy_path(tmp_path):
    audio_data = b"good audio" * 100
    nas_file = tmp_path / "nas" / "t1.mp3"
    nas_file.parent.mkdir(parents=True)
    nas_file.write_bytes(audio_data)

    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas", "old_full_path": str(nas_file)},
    ])
    tracks = [{"track_id": "t1", "old_full_path": str(nas_file), "audio_data": audio_data}]
    gig_dir, audio_by_tid = _make_gig_dir(tmp_path, "friday", tracks)

    result = run_gig_cleanup("friday", csv_path, gig_dir=gig_dir, verify_nas=True)

    assert result.sha_failures == 0
    assert result.deleted_files == 1
    assert not audio_by_tid["t1"].exists()


def test_run_gig_cleanup_verify_nas_sha_mismatch_keeps_file(tmp_path):
    audio_data = b"original audio" * 100
    nas_file = tmp_path / "nas" / "t1.mp3"
    nas_file.parent.mkdir(parents=True)
    nas_file.write_bytes(b"corrupted!" * 100)  # different content on NAS

    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas", "old_full_path": str(nas_file)},
    ])
    tracks = [{"track_id": "t1", "old_full_path": str(nas_file), "audio_data": audio_data}]
    gig_dir, audio_by_tid = _make_gig_dir(tmp_path, "friday", tracks)

    result = run_gig_cleanup("friday", csv_path, gig_dir=gig_dir, verify_nas=True)

    assert result.sha_failures == 1
    assert result.deleted_files == 0
    assert audio_by_tid["t1"].exists()  # MacBook file kept — NAS is suspect


def test_run_gig_cleanup_verify_nas_missing_nas_file(tmp_path):
    audio_data = b"audio" * 100
    nas_file = tmp_path / "nas" / "t1.mp3"
    # NAS file does NOT exist

    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas", "old_full_path": str(nas_file)},
    ])
    tracks = [{"track_id": "t1", "old_full_path": str(nas_file), "audio_data": audio_data}]
    gig_dir, audio_by_tid = _make_gig_dir(tmp_path, "friday", tracks)

    result = run_gig_cleanup("friday", csv_path, gig_dir=gig_dir, verify_nas=True)

    assert result.sha_failures == 1
    assert result.deleted_files == 0
    assert audio_by_tid["t1"].exists()
