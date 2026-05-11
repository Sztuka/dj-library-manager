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


def test_run_gig_cleanup_verify_nas_empty_sha_skips_delete(tmp_path):
    """--verify-nas with missing sha256 in manifest skips the file (can't verify)."""
    audio_data = b"audio" * 100
    nas_file = tmp_path / "nas" / "t1.mp3"
    nas_file.parent.mkdir(parents=True)
    nas_file.write_bytes(audio_data)

    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas", "old_full_path": str(nas_file)},
    ])
    tracks = [{"track_id": "t1", "old_full_path": str(nas_file), "audio_data": audio_data}]
    gig_dir, audio_by_tid = _make_gig_dir(tmp_path, "friday", tracks)

    # Overwrite manifest with empty sha256
    import json as _json
    with gig_dir.manifest_path.open() as f:
        manifest = _json.load(f)
    manifest["tracks"][0]["sha256"] = ""
    with gig_dir.manifest_path.open("w") as f:
        _json.dump(manifest, f)

    result = run_gig_cleanup("friday", csv_path, gig_dir=gig_dir, verify_nas=True)

    assert result.sha_failures == 1
    assert result.deleted_files == 0
    assert audio_by_tid["t1"].exists()  # MacBook file kept — no reference hash


# ── Guard edge cases ──────────────────────────────────────────────────────────


def test_run_gig_cleanup_aborts_if_track_missing_from_library(tmp_path):
    """Track in gig.csv but absent from library.csv → abort (treat as not merged)."""
    csv_path = _make_library_csv(tmp_path, [])  # empty library
    tracks = [{"track_id": "t1"}]
    gig_dir, audio_by_tid = _make_gig_dir(tmp_path, "friday", tracks)

    result = run_gig_cleanup("friday", csv_path, gig_dir=gig_dir)

    assert result.not_merged == 1
    assert audio_by_tid["t1"].exists()


def test_run_gig_cleanup_aborts_if_track_still_preparing(tmp_path):
    """Track in 'gig:<id>:preparing' state (crash mid-prep) → abort."""
    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "gig:friday:preparing"},
    ])
    tracks = [{"track_id": "t1"}]
    gig_dir, audio_by_tid = _make_gig_dir(tmp_path, "friday", tracks)

    result = run_gig_cleanup("friday", csv_path, gig_dir=gig_dir)

    assert result.not_merged == 1
    assert audio_by_tid["t1"].exists()


# ── No local_path in manifest ─────────────────────────────────────────────────


def test_run_gig_cleanup_skips_track_with_no_local_path(tmp_path):
    """Track with missing local_path in manifest is skipped safely (no Path('.') footgun)."""
    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas"},
    ])
    tracks = [{"track_id": "t1"}]
    gig_dir, audio_by_tid = _make_gig_dir(tmp_path, "friday", tracks)

    # Corrupt manifest: remove local_path
    import json as _json
    with gig_dir.manifest_path.open() as f:
        manifest = _json.load(f)
    del manifest["tracks"][0]["local_path"]
    with gig_dir.manifest_path.open("w") as f:
        _json.dump(manifest, f)

    result = run_gig_cleanup("friday", csv_path, gig_dir=gig_dir)

    assert result.deleted_files == 0  # nothing to delete — local_path unknown
    assert result.not_merged == 0     # guard passed
    # Crucially: no exception, no deletion of unintended paths


# ── Empty gig.csv ─────────────────────────────────────────────────────────────


def test_run_gig_cleanup_empty_gig_csv_returns_early(tmp_path):
    """gig.csv with only a header row (no tracks) returns immediately with 0 deleted."""
    csv_path = _make_library_csv(tmp_path, [])
    gig_dir = GigDir(gig_id="friday", root=tmp_path / "Gigs")
    gig_dir.ensure()

    # Write gig.csv with header only
    import csv as csv_mod
    with gig_dir.gig_csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv_mod.DictWriter(f, fieldnames=LIBRARY_FIELDS)
        w.writeheader()

    # Plant a file in audio/ — it must NOT be deleted
    stray = gig_dir.audio_dir / "mystery.mp3"
    stray.write_bytes(b"data")

    result = run_gig_cleanup("friday", csv_path, gig_dir=gig_dir)

    assert result.deleted_files == 0
    assert stray.exists()  # untouched


# ── rmdir non-empty audio/ doesn't crash ─────────────────────────────────────


def test_run_gig_cleanup_default_gig_dir_creation(tmp_path, monkeypatch):
    """run_gig_cleanup with gig_dir=None uses _default_gig_root() — default path resolution works."""
    monkeypatch.setattr("djlib.gig._default_gig_root", lambda: tmp_path / "Gigs")
    csv_path = _make_library_csv(tmp_path, [])

    with pytest.raises(FileNotFoundError, match="gig.csv"):
        run_gig_cleanup("friday", csv_path)  # no gig_dir= — proves default path used


# ── GigDir gig_id validation ──────────────────────────────────────────────────


def test_gigdir_rejects_path_traversal_gig_id():
    with pytest.raises(ValueError, match="gig_id"):
        GigDir(gig_id="../../etc")


def test_gigdir_rejects_empty_gig_id():
    with pytest.raises(ValueError, match="gig_id"):
        GigDir(gig_id="")


def test_gigdir_rejects_gig_id_with_slash():
    with pytest.raises(ValueError, match="gig_id"):
        GigDir(gig_id="friday/subdir")


# ── Path traversal guard ──────────────────────────────────────────────────────


def test_run_gig_cleanup_refuses_local_path_outside_audio_dir(tmp_path):
    """local_path pointing outside audio_dir must be refused — no arbitrary file deletion."""
    victim = tmp_path / "precious.csv"
    victim.write_bytes(b"do not delete")

    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas"},
    ])
    tracks = [{"track_id": "t1"}]
    gig_dir, _ = _make_gig_dir(tmp_path, "friday", tracks)

    # Overwrite manifest local_path to point outside audio_dir
    with gig_dir.manifest_path.open() as f:
        manifest = json.load(f)
    manifest["tracks"][0]["local_path"] = str(victim)
    with gig_dir.manifest_path.open("w") as f:
        json.dump(manifest, f)

    result = run_gig_cleanup("friday", csv_path, gig_dir=gig_dir)

    assert victim.exists()          # precious file untouched
    assert result.deleted_files == 0


# ── Malformed manifest.json ───────────────────────────────────────────────────


def test_run_gig_cleanup_malformed_manifest_raises_value_error(tmp_path):
    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas"},
    ])
    gig_dir = GigDir(gig_id="friday", root=tmp_path / "Gigs")
    gig_dir.ensure()

    with gig_dir.gig_csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv_mod.DictWriter(f, fieldnames=LIBRARY_FIELDS)
        w.writeheader()
        w.writerow({k: "t1" if k == "track_id" else "" for k in LIBRARY_FIELDS})
    # Write truncated JSON
    gig_dir.manifest_path.write_text('{"gig_id": "friday", "tracks": [{"track')

    with pytest.raises(ValueError, match="manifest.json"):
        run_gig_cleanup("friday", csv_path, gig_dir=gig_dir)


# ── Duplicate track_id in manifest ───────────────────────────────────────────


def test_run_gig_cleanup_duplicate_manifest_track_id_uses_first(tmp_path):
    """Duplicate track_id in manifest: first entry used, warning logged, no crash."""
    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas"},
    ])
    tracks = [{"track_id": "t1"}]
    gig_dir, audio_by_tid = _make_gig_dir(tmp_path, "friday", tracks)

    # Add second entry with same track_id but different (nonexistent) local_path
    with gig_dir.manifest_path.open() as f:
        manifest = json.load(f)
    first_local = manifest["tracks"][0]["local_path"]
    manifest["tracks"].append({
        "track_id": "t1",
        "local_path": "/nonexistent/t1_v2.mp3",
        "src_path": "/nas/t1.mp3",
        "sha256": "",
    })
    with gig_dir.manifest_path.open("w") as f:
        json.dump(manifest, f)

    result = run_gig_cleanup("friday", csv_path, gig_dir=gig_dir)

    # First entry was processed — MacBook file deleted
    assert not audio_by_tid["t1"].exists()
    assert result.deleted_files == 1


# ── verify_nas: empty src_path doesn't crash ─────────────────────────────────


def test_run_gig_cleanup_verify_nas_empty_src_path_no_crash(tmp_path):
    """verify_nas=True with empty src_path and old_full_path → sha_failures, no crash."""
    audio_data = b"audio" * 100
    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas", "old_full_path": ""},
    ])
    tracks = [{"track_id": "t1", "old_full_path": "", "audio_data": audio_data}]
    gig_dir, audio_by_tid = _make_gig_dir(tmp_path, "friday", tracks)

    # Overwrite manifest: sha256 present, src_path empty
    with gig_dir.manifest_path.open() as f:
        manifest = json.load(f)
    manifest["tracks"][0]["src_path"] = ""
    with gig_dir.manifest_path.open("w") as f:
        json.dump(manifest, f)

    result = run_gig_cleanup("friday", csv_path, gig_dir=gig_dir, verify_nas=True)

    assert result.sha_failures == 1
    assert result.deleted_files == 0
    assert audio_by_tid["t1"].exists()


# ── verify_nas: self-verify guard ─────────────────────────────────────────────


def test_run_gig_cleanup_verify_nas_refuses_if_src_equals_local(tmp_path):
    """verify_nas=True with src_path == local_path → sha_failures, file kept."""
    audio_data = b"audio" * 100
    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas"},
    ])
    tracks = [{"track_id": "t1", "audio_data": audio_data}]
    gig_dir, audio_by_tid = _make_gig_dir(tmp_path, "friday", tracks)

    # Make src_path point to the same file as local_path
    local = str(audio_by_tid["t1"])
    with gig_dir.manifest_path.open() as f:
        manifest = json.load(f)
    manifest["tracks"][0]["src_path"] = local
    with gig_dir.manifest_path.open("w") as f:
        json.dump(manifest, f)

    result = run_gig_cleanup("friday", csv_path, gig_dir=gig_dir, verify_nas=True)

    assert result.sha_failures == 1
    assert result.deleted_files == 0
    assert audio_by_tid["t1"].exists()


# ── Empty live_location treated as nas ────────────────────────────────────────


def test_run_gig_cleanup_empty_live_location_treated_as_nas(tmp_path):
    """Legacy tracks with live_location='' are treated as 'nas' — cleanup proceeds."""
    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": ""},  # pre-gig-tracking legacy row
    ])
    tracks = [{"track_id": "t1"}]
    gig_dir, audio_by_tid = _make_gig_dir(tmp_path, "friday", tracks)

    result = run_gig_cleanup("friday", csv_path, gig_dir=gig_dir)

    assert result.not_merged == 0
    assert result.deleted_files == 1
    assert not audio_by_tid["t1"].exists()


# ── delete_failures counted ───────────────────────────────────────────────────


def test_run_gig_cleanup_counts_delete_failures(tmp_path, monkeypatch):
    """unlink() failure increments delete_failures (not silently swallowed)."""
    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas"},
    ])
    tracks = [{"track_id": "t1"}]
    gig_dir, audio_by_tid = _make_gig_dir(tmp_path, "friday", tracks)

    original_unlink = audio_by_tid["t1"].unlink

    def failing_unlink(*args, **kwargs):
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(type(audio_by_tid["t1"]), "unlink", lambda self, *a, **k: failing_unlink())

    result = run_gig_cleanup("friday", csv_path, gig_dir=gig_dir)

    assert result.delete_failures == 1
    assert result.deleted_files == 0


# ── rmdir non-empty audio/ doesn't crash ─────────────────────────────────────


def test_run_gig_cleanup_rmdir_tolerates_ds_store(tmp_path):
    """.DS_Store (or any leftover) in audio/ prevents rmdir but doesn't crash."""
    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas"},
    ])
    tracks = [{"track_id": "t1"}]
    gig_dir, _ = _make_gig_dir(tmp_path, "friday", tracks)

    # Plant a .DS_Store-like file that won't be deleted (not in manifest)
    (gig_dir.audio_dir / ".DS_Store").write_bytes(b"")

    result = run_gig_cleanup("friday", csv_path, gig_dir=gig_dir)

    assert result.deleted_files == 1
    assert gig_dir.audio_dir.exists()  # not removed — still has .DS_Store
