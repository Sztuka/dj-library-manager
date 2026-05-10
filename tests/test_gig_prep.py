"""Tests for djlib.gig — Phase 2 slice 1: dry-run gig-prep."""
from __future__ import annotations

from pathlib import Path

import pytest

from djlib.gig import (
    ResolveResult,
    ValidationError,
    parse_m3u,
    resolve_tracks,
    validate_gig_prep,
    estimate_total_bytes,
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
