"""Tests for djlib.gig — Phase 2: dry-run + copy protocol."""
from __future__ import annotations

from pathlib import Path

import pytest

from djlib.gig import (
    GigDir,
    GigPrepLock,
    PrepState,
    ResolveResult,
    ValidationError,
    copy_track_atomic,
    estimate_total_bytes,
    parse_m3u,
    resolve_tracks,
    run_gig_prep_copy,
    validate_gig_prep,
    PREP_COMMITTED,
    PREP_VERIFIED,
)


# ── parse_m3u ────────────────────────────────────────────────────────────────


def test_parse_m3u_basic(tmp_path):
    m3u = tmp_path / "set.m3u"
    m3u.write_text(
        "#EXTM3U\n"
        "#EXTINF:300,Artist - Title\n"
        "/Music/Library/artist/track.mp3\n"
        "\n"
        "# comment\n"
        "/Music/Library/other/track2.flac\n"
    )
    paths = parse_m3u(m3u)
    assert paths == ["/Music/Library/artist/track.mp3", "/Music/Library/other/track2.flac"]


def test_parse_m3u_relative_paths(tmp_path):
    subdir = tmp_path / "playlists"
    subdir.mkdir()
    m3u = subdir / "set.m3u"
    m3u.write_text("../audio/track.mp3\n")
    paths = parse_m3u(m3u)
    assert len(paths) == 1
    assert paths[0] == str((subdir / "../audio/track.mp3").resolve())


def test_parse_m3u_empty(tmp_path):
    m3u = tmp_path / "empty.m3u"
    m3u.write_text("#EXTM3U\n# only comments\n\n")
    assert parse_m3u(m3u) == []


def test_parse_m3u_no_header(tmp_path):
    m3u = tmp_path / "bare.m3u"
    m3u.write_text("/a.mp3\n/b.mp3\n")
    assert parse_m3u(m3u) == ["/a.mp3", "/b.mp3"]


# ── resolve_tracks ────────────────────────────────────────────────────────────


def _row(track_id: str, path: str, **kw) -> dict:
    return {"track_id": track_id, "old_full_path": path, **kw}


def test_resolve_exact_match():
    rows = [_row("tid-1", "/Music/artist/track.mp3")]
    results = resolve_tracks(["/Music/artist/track.mp3"], rows)
    assert len(results) == 1
    assert results[0].track_id == "tid-1"
    assert results[0].match_type == "exact"


def test_resolve_filename_fallback():
    rows = [_row("tid-2", "/NAS/Music/artist/track.mp3")]
    results = resolve_tracks(["/MacBook/Music/artist/track.mp3"], rows)
    assert results[0].track_id == "tid-2"
    assert results[0].match_type == "filename"


def test_resolve_filename_ambiguous_returns_not_found():
    """Two tracks with same filename = can't safely guess → not_found."""
    rows = [
        _row("tid-a", "/path1/track.mp3"),
        _row("tid-b", "/path2/track.mp3"),
    ]
    results = resolve_tracks(["/other/track.mp3"], rows)
    assert results[0].match_type == "not_found"
    assert results[0].track_id is None


def test_resolve_not_found():
    results = resolve_tracks(["/nonexistent/track.mp3"], [])
    assert results[0].match_type == "not_found"
    assert results[0].track_id is None


def test_resolve_multiple_mixed():
    rows = [
        _row("t1", "/Music/a.mp3"),
        _row("t2", "/Music/b.mp3"),
    ]
    results = resolve_tracks(["/Music/a.mp3", "/Music/c.mp3"], rows)
    assert results[0].track_id == "t1"
    assert results[1].match_type == "not_found"


# ── validate_gig_prep ─────────────────────────────────────────────────────────


def _resolved(path: str, tid: str | None, live_loc: str = "nas") -> ResolveResult:
    row = {"track_id": tid or "", "live_location": live_loc, "old_full_path": path}
    match = "exact" if tid else "not_found"
    return ResolveResult(
        playlist_path=path,
        track_id=tid,
        library_row=row if tid else None,
        match_type=match,
    )


def test_validate_clean(tmp_path):
    f = tmp_path / "track.mp3"
    f.write_bytes(b"audio")
    r = _resolved(str(f), "tid-1", live_loc="nas")
    errors = validate_gig_prep("friday", [r])
    assert errors == []


def test_validate_not_in_library(tmp_path):
    r = _resolved("/nonexistent.mp3", None)
    errors = validate_gig_prep("friday", [r], check_files_exist=False)
    assert len(errors) == 1
    assert errors[0].kind == "NOT_IN_LIBRARY"


def test_validate_on_another_gig(tmp_path):
    f = tmp_path / "track.mp3"
    f.write_bytes(b"audio")
    r = _resolved(str(f), "tid-1", live_loc="gig:saturday")
    errors = validate_gig_prep("friday", [r])
    assert len(errors) == 1
    assert errors[0].kind == "ON_ANOTHER_GIG"
    assert "saturday" in errors[0].detail


def test_validate_same_gig_no_error(tmp_path):
    """Track already on this gig (resuming prep) should not be an error."""
    f = tmp_path / "track.mp3"
    f.write_bytes(b"audio")
    r = _resolved(str(f), "tid-1", live_loc="gig:friday")
    errors = validate_gig_prep("friday", [r])
    assert errors == []


def test_validate_file_missing_on_disk(tmp_path):
    r = _resolved("/does/not/exist.mp3", "tid-1", live_loc="nas")
    errors = validate_gig_prep("friday", [r], check_files_exist=True)
    assert any(e.kind == "FILE_MISSING" for e in errors)


def test_validate_collects_all_errors(tmp_path):
    """All errors must be reported, not just the first one."""
    r1 = _resolved("/missing1.mp3", None)
    r2 = _resolved("/missing2.mp3", None)
    errors = validate_gig_prep("friday", [r1, r2], check_files_exist=False)
    assert len(errors) == 2


# ── estimate_total_bytes ──────────────────────────────────────────────────────


def test_estimate_total_bytes(tmp_path):
    f1 = tmp_path / "a.mp3"
    f2 = tmp_path / "b.mp3"
    f1.write_bytes(b"x" * 1000)
    f2.write_bytes(b"y" * 2000)
    resolved = [
        ResolveResult(str(f1), "t1", {"old_full_path": str(f1), "track_id": "t1"}, "exact"),
        ResolveResult(str(f2), "t2", {"old_full_path": str(f2), "track_id": "t2"}, "exact"),
    ]
    assert estimate_total_bytes(resolved) == 3000


def test_estimate_skips_not_found():
    r = ResolveResult("/ghost.mp3", None, None, "not_found")
    assert estimate_total_bytes([r]) == 0


# ── CLI: gig-prep subcommand registered ──────────────────────────────────────


def test_cli_gig_prep_dry_run_flag():
    from djlib.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["gig-prep", "friday-2026-05-15", "--from-m3u", "/tmp/set.m3u", "--dry-run"])
    assert args.gig_id == "friday-2026-05-15"
    assert args.from_m3u == "/tmp/set.m3u"
    assert args.dry_run is True


def test_cli_gig_prep_no_dry_run_flag():
    from djlib.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["gig-prep", "friday", "--from-m3u", "/tmp/set.m3u"])
    assert args.dry_run is False


def test_cli_gig_prep_resume_flag():
    from djlib.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["gig-prep", "friday", "--from-m3u", "/tmp/set.m3u", "--resume"])
    assert args.resume is True


# ── GigDir ───────────────────────────────────────────────────────────────────


def test_gig_dir_paths(tmp_path):
    gd = GigDir(gig_id="friday", root=tmp_path)
    assert gd.path == tmp_path / "friday"
    assert gd.audio_dir == tmp_path / "friday" / "audio"
    assert gd.prep_state_path == tmp_path / "friday" / "prep.state"
    assert gd.manifest_path == tmp_path / "friday" / "manifest.json"


def test_gig_dir_ensure_creates_dirs(tmp_path):
    gd = GigDir(gig_id="saturday", root=tmp_path)
    gd.ensure()
    assert gd.audio_dir.exists()


def test_gig_dir_audio_dest_uses_track_id(tmp_path):
    gd = GigDir(gig_id="friday", root=tmp_path)
    dest = gd.audio_dest("track-abc", "/Music/artist/song.flac")
    assert dest.name == "track-abc.flac"
    assert dest.parent == gd.audio_dir


# ── PrepState ────────────────────────────────────────────────────────────────


def test_prep_state_append_and_read(tmp_path):
    ps = PrepState(tmp_path / "prep.state")
    ps.append_event("tid-1", "copy_start", src="/a.mp3")
    ps.append_event("tid-1", "verified", sha256="abc123")
    ps.append_event("tid-2", "copy_start", src="/b.mp3")

    states = ps.get_track_states()
    assert states["tid-1"] == "verified"
    assert states["tid-2"] == "copy_start"


def test_prep_state_empty_file(tmp_path):
    ps = PrepState(tmp_path / "prep.state")
    assert ps.get_track_states() == {}


def test_prep_state_missing_file(tmp_path):
    ps = PrepState(tmp_path / "nonexistent.state")
    assert ps.get_track_states() == {}


# ── copy_track_atomic ────────────────────────────────────────────────────────


def test_copy_track_atomic_copies_file(tmp_path):
    src = tmp_path / "src.mp3"
    src.write_bytes(b"audio data" * 100)
    dest = tmp_path / "dest" / "output.mp3"

    sha = copy_track_atomic(src, dest)
    assert dest.exists()
    assert dest.read_bytes() == src.read_bytes()
    assert len(sha) == 64  # sha256 hex


def test_copy_track_atomic_no_partial_after_success(tmp_path):
    src = tmp_path / "src.mp3"
    src.write_bytes(b"data")
    dest = tmp_path / "out.mp3"
    copy_track_atomic(src, dest)
    assert not (tmp_path / "out.mp3.partial").exists()


def test_copy_track_atomic_creates_parent_dirs(tmp_path):
    src = tmp_path / "src.mp3"
    src.write_bytes(b"data")
    dest = tmp_path / "a" / "b" / "c" / "out.mp3"
    copy_track_atomic(src, dest)
    assert dest.exists()


# ── GigPrepLock ──────────────────────────────────────────────────────────────


def test_gig_prep_lock_acquire_release(tmp_path):
    lock = GigPrepLock(tmp_path / ".prep.lock")
    assert lock.acquire() is True
    lock.release()


def test_gig_prep_lock_double_acquire_fails(tmp_path):
    lock1 = GigPrepLock(tmp_path / ".prep.lock")
    lock2 = GigPrepLock(tmp_path / ".prep.lock")
    assert lock1.acquire() is True
    assert lock2.acquire() is False
    lock1.release()


# ── run_gig_prep_copy (integration) ──────────────────────────────────────────


def _make_library_csv(tmp_path: Path, rows: list) -> Path:
    import csv as csv_mod
    csv_path = tmp_path / "data" / "library.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        fieldnames = list(rows[0].keys())
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv_mod.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
    else:
        csv_path.write_text("track_id,live_location,live_path,old_full_path\n")
    return csv_path


def test_run_gig_prep_copy_happy_path(tmp_path):
    src1 = tmp_path / "src" / "a.mp3"
    src2 = tmp_path / "src" / "b.mp3"
    src1.parent.mkdir(parents=True)
    src1.write_bytes(b"audio1" * 500)
    src2.write_bytes(b"audio2" * 500)

    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas", "live_path": "", "old_full_path": str(src1)},
        {"track_id": "t2", "live_location": "nas", "live_path": "", "old_full_path": str(src2)},
    ])

    resolved = [
        ResolveResult(str(src1), "t1", {"track_id": "t1", "live_location": "nas"}, "exact"),
        ResolveResult(str(src2), "t2", {"track_id": "t2", "live_location": "nas"}, "exact"),
    ]

    gig_dir = GigDir(gig_id="test-gig", root=tmp_path / "Gigs")
    result = run_gig_prep_copy("test-gig", resolved, csv_path, gig_dir)

    assert result.copied == 2
    assert result.failed == 0
    assert result.committed == 2

    # Audio files present
    assert (gig_dir.audio_dir / "t1.mp3").exists()
    assert (gig_dir.audio_dir / "t2.mp3").exists()

    # manifest written
    import json
    manifest = json.loads(gig_dir.manifest_path.read_text())
    assert manifest["gig_id"] == "test-gig"
    assert len(manifest["tracks"]) == 2

    # library.csv updated
    import csv as csv_mod
    with csv_path.open() as f:
        rows = list(csv_mod.DictReader(f))
    by_tid = {r["track_id"]: r for r in rows}
    assert by_tid["t1"]["live_location"] == "gig:test-gig"
    assert by_tid["t1"]["live_path"] == str(gig_dir.audio_dir / "t1.mp3")


def test_run_gig_prep_copy_resume_skips_verified(tmp_path):
    src = tmp_path / "src" / "a.mp3"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"audio")

    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas", "live_path": "", "old_full_path": str(src)},
    ])

    resolved = [ResolveResult(str(src), "t1", {"track_id": "t1", "live_location": "nas"}, "exact")]
    gig_dir = GigDir(gig_id="resume-gig", root=tmp_path / "Gigs")
    gig_dir.ensure()

    # Pre-seed prep.state as if previous run already verified t1
    ps = PrepState(gig_dir.prep_state_path)
    ps.append_event("t1", PREP_VERIFIED, sha256="fakehash", dest=str(gig_dir.audio_dest("t1", str(src))))

    result = run_gig_prep_copy("resume-gig", resolved, csv_path, gig_dir, resume=True)
    assert result.skipped == 1
    assert result.copied == 0
