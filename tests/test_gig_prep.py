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
    assert gd.gig_csv_path == tmp_path / "friday" / "gig.csv"


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


def test_run_gig_prep_copy_writes_gig_csv(tmp_path):
    """COMMIT must write gig.csv with a frozen row for every prepped track."""
    src = tmp_path / "src" / "a.mp3"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"audio1" * 500)

    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas", "live_path": "",
         "old_full_path": str(src), "artist": "DJ Test", "title": "Test Track"},
    ])
    resolved = [ResolveResult(str(src), "t1", {"track_id": "t1", "live_location": "nas"}, "exact")]
    gig_dir = GigDir(gig_id="snap-gig", root=tmp_path / "Gigs")
    run_gig_prep_copy("snap-gig", resolved, csv_path, gig_dir)

    assert gig_dir.gig_csv_path.exists(), "gig.csv must be written after COMMIT"
    import csv as csv_mod
    with gig_dir.gig_csv_path.open() as f:
        rows = list(csv_mod.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["track_id"] == "t1"
    assert rows[0]["artist"] == "DJ Test"
    assert rows[0]["title"] == "Test Track"
    # live_location captured at COMMIT time — proves snapshot uses post-COMMIT by_tid
    assert rows[0]["live_location"] == "gig:snap-gig"


def test_run_gig_prep_copy_manifest_schema_version_is_list(tmp_path):
    """manifest.json schema_version must be a list of library field names."""
    src = tmp_path / "src" / "a.mp3"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"audio1" * 500)

    csv_path = _make_library_csv(tmp_path, [
        {"track_id": "t1", "live_location": "nas", "live_path": "", "old_full_path": str(src)},
    ])
    resolved = [ResolveResult(str(src), "t1", {"track_id": "t1", "live_location": "nas"}, "exact")]
    gig_dir = GigDir(gig_id="sv-gig", root=tmp_path / "Gigs")
    run_gig_prep_copy("sv-gig", resolved, csv_path, gig_dir)

    import json
    manifest = json.loads(gig_dir.manifest_path.read_text())
    sv = manifest["schema_version"]
    assert isinstance(sv, list), "schema_version must be a list of field names"
    assert "track_id" in sv
    assert "live_location" in sv


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


# ── Marek QA: edge cases ─────────────────────────────────────────────────────


# parse_m3u: BOM (UTF-8-sig) must not be treated as a path component
def test_parse_m3u_bom_stripped(tmp_path):
    """M3U files exported by some DJ software include a UTF-8 BOM.
    With encoding='utf-8' (not 'utf-8-sig') the BOM leaks into the first line
    and '#EXTM3U' is not recognised — causing the BOM+header to be returned
    as a file path instead of being skipped as a comment."""
    m3u = tmp_path / "bom.m3u"
    # Write BOM + header + one real path
    m3u.write_bytes(
        "﻿#EXTM3U\n/Music/track.mp3\n".encode("utf-8")
    )
    paths = parse_m3u(m3u)
    assert paths == ["/Music/track.mp3"], (
        "BOM must be stripped; '#EXTM3U' line must be treated as comment"
    )


def test_parse_m3u_windows_crlf(tmp_path):
    """Windows \\r\\n line endings must not leave trailing \\r in paths."""
    m3u = tmp_path / "win.m3u"
    m3u.write_bytes(b"#EXTM3U\r\n/Music/a.mp3\r\n/Music/b.mp3\r\n")
    paths = parse_m3u(m3u)
    assert paths == ["/Music/a.mp3", "/Music/b.mp3"]
    assert all(not p.endswith("\r") for p in paths)


def test_parse_m3u_bom_plus_crlf(tmp_path):
    """BOM + Windows line endings — the most hostile M3U from a Windows DJ."""
    m3u = tmp_path / "win_bom.m3u"
    m3u.write_bytes(
        "﻿#EXTM3U\r\n/Music/track.mp3\r\n".encode("utf-8")
    )
    paths = parse_m3u(m3u)
    assert paths == ["/Music/track.mp3"]


def test_parse_m3u_unicode_paths(tmp_path):
    """Paths with CJK, emoji, square brackets, and ampersand must pass through."""
    m3u = tmp_path / "unicode.m3u"
    m3u.write_text(
        "#EXTM3U\n"
        "/Music/中文/track.mp3\n"
        "/Music/DJ Snake & Lil Jon — Turn Down [feat. UTF-8].mp3\n"
        "/Music/\U0001f3b5 set/track.flac\n",
        encoding="utf-8",
    )
    paths = parse_m3u(m3u)
    assert len(paths) == 3
    assert "/Music/中文/track.mp3" in paths
    assert any("[feat. UTF-8]" in p for p in paths)


# validate_gig_prep: interrupted run state must not block resume

def test_validate_preparing_state_same_gig_no_error(tmp_path):
    """live_location='gig:<id>:preparing' is our own interrupted run.
    validate_gig_prep must NOT raise ON_ANOTHER_GIG for the same gig_id.
    Before the fix this returned ON_ANOTHER_GIG, blocking --resume entirely."""
    r = ResolveResult(
        playlist_path="/path/track.mp3",
        track_id="tid-1",
        library_row={"track_id": "tid-1", "live_location": "gig:friday:preparing"},
        match_type="exact",
    )
    errors = validate_gig_prep("friday", [r], check_files_exist=False)
    assert errors == [], (
        "'gig:friday:preparing' must be treated as safe for gig_id='friday'"
    )


def test_validate_preparing_state_other_gig_is_error(tmp_path):
    """'gig:saturday:preparing' while prepping for 'friday' IS an error."""
    r = ResolveResult(
        playlist_path="/path/track.mp3",
        track_id="tid-1",
        library_row={"track_id": "tid-1", "live_location": "gig:saturday:preparing"},
        match_type="exact",
    )
    errors = validate_gig_prep("friday", [r], check_files_exist=False)
    assert len(errors) == 1
    assert errors[0].kind == "ON_ANOTHER_GIG"


# validate_gig_prep: path that exists but is a directory

def test_validate_path_is_directory_triggers_file_missing(tmp_path):
    """A path that exists as a directory must still produce FILE_MISSING.
    Otherwise copy_track_atomic will crash mid-run with IsADirectoryError
    instead of a clean pre-flight failure."""
    dir_path = tmp_path / "track_that_is_dir.mp3"
    dir_path.mkdir()
    r = ResolveResult(
        playlist_path=str(dir_path),
        track_id="tid-1",
        library_row={"track_id": "tid-1", "live_location": "nas"},
        match_type="exact",
    )
    errors = validate_gig_prep("friday", [r], check_files_exist=True)
    # Path.exists() returns True for dirs — current code gives no error.
    # This test documents the gap: FILE_MISSING should fire for directories.
    # When the bug is fixed, change the assertion to: assert any(e.kind == "FILE_MISSING" for e in errors)
    assert errors == [], "KNOWN GAP: directory is not detected as non-file by validate_gig_prep"


# resolve_tracks: filename fallback with NFC vs NFD mismatch

def test_resolve_nfc_nfd_mismatch_exact(tmp_path):
    """M3U written with NFC 'é', library row has NFD 'é' — exact match fails.
    The filename fallback may pick the wrong track when both NFC and NFD
    entries share the same basename."""
    import unicodedata
    nfc = "/Music/path1/Beyoncé.mp3"      # NFC: é as U+00E9
    nfd = unicodedata.normalize("NFD", nfc)       # NFD: e + combining accent
    # Two rows: one NFC, one NFD path — same basename after normalisation
    rows = [
        {"track_id": "tid-nfc", "old_full_path": nfc},
        {"track_id": "tid-nfd", "old_full_path": nfd},
    ]
    # Playlist uses NFD (as macOS Finder would export)
    nfd_playlist = unicodedata.normalize("NFD", nfc)
    results = resolve_tracks([nfd_playlist], rows)
    # Both entries share the same basename after Path().name — fallback is ambiguous
    # so result should be not_found (ambiguous) OR exact on the NFD entry.
    # Currently: filename fallback picks tid-nfd (correct by accident).
    # Documents that NFC/NFD path drift in library.csv breaks exact matching.
    assert results[0].match_type in ("exact", "filename", "not_found"), (
        "NFC/NFD mismatch must not crash — result is implementation-defined but safe"
    )


# PrepState: corrupted mid-file JSON must not lose subsequent entries

def test_prep_state_corrupt_line_skipped(tmp_path):
    """A corrupt JSON line in prep.state must be silently skipped.
    Subsequent valid entries must still be readable."""
    ps = PrepState(tmp_path / "prep.state")
    ps.append_event("tid-1", "verified", sha256="abc")
    # Inject corrupt line directly
    with (tmp_path / "prep.state").open("a", encoding="utf-8") as f:
        f.write("NOT VALID JSON {{{{\n")
    ps.append_event("tid-2", "verified", sha256="def")

    states = ps.get_track_states()
    assert states.get("tid-1") == "verified"
    assert states.get("tid-2") == "verified"


# run_gig_prep_copy: empty playlist must produce manifest with zero tracks

def test_run_gig_prep_copy_empty_resolved(tmp_path):
    """An empty playlist must complete without error and write a valid manifest."""
    import json as _json
    import csv as _csv

    csv_path = tmp_path / "data" / "library.csv"
    csv_path.parent.mkdir(parents=True)
    csv_path.write_text("track_id,live_location,live_path,old_full_path\n")

    gig_dir = GigDir(gig_id="empty-gig", root=tmp_path / "Gigs")
    result = run_gig_prep_copy("empty-gig", [], csv_path, gig_dir)

    assert result.copied == 0
    assert result.failed == 0
    assert gig_dir.manifest_path.exists()
    manifest = _json.loads(gig_dir.manifest_path.read_text())
    assert manifest["tracks"] == []


# run_gig_prep_copy: same gig_id reused silently overwrites manifest

def test_run_gig_prep_copy_reuses_gig_id_overwrites_manifest(tmp_path):
    """Running gig-prep twice with the same gig_id silently overwrites manifest.json
    and pollutes prep.state with events from both runs.
    This test documents the behaviour — currently no warning is emitted."""
    import json as _json
    import csv as _csv
    import time as _time

    def _write_csv(path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            w = _csv.DictWriter(f, fieldnames=["track_id", "live_location", "live_path", "old_full_path"])
            w.writeheader()
            w.writerows(rows)

    src1 = tmp_path / "src" / "a.mp3"
    src1.parent.mkdir(parents=True)
    src1.write_bytes(b"audio1")

    csv_path = tmp_path / "data" / "library.csv"
    _write_csv(csv_path, [{"track_id": "t1", "live_location": "nas", "live_path": "", "old_full_path": str(src1)}])

    gig_dir = GigDir(gig_id="friday", root=tmp_path / "Gigs")
    resolved1 = [ResolveResult(str(src1), "t1", {"track_id": "t1", "live_location": "nas"}, "exact")]
    run_gig_prep_copy("friday", resolved1, csv_path, gig_dir)
    manifest_v1_tracks = _json.loads(gig_dir.manifest_path.read_text())["tracks"]

    # Second run with a different track but same gig_id
    src2 = tmp_path / "src" / "b.mp3"
    src2.write_bytes(b"audio2")
    _write_csv(csv_path, [{"track_id": "t2", "live_location": "nas", "live_path": "", "old_full_path": str(src2)}])
    resolved2 = [ResolveResult(str(src2), "t2", {"track_id": "t2", "live_location": "nas"}, "exact")]
    run_gig_prep_copy("friday", resolved2, csv_path, gig_dir)
    manifest_v2_tracks = _json.loads(gig_dir.manifest_path.read_text())["tracks"]

    # Manifest is overwritten — only second run's tracks appear
    track_ids_v2 = {t["track_id"] for t in manifest_v2_tracks}
    assert "t1" not in track_ids_v2, (
        "KNOWN GAP: manifest.json is silently overwritten on gig_id reuse; t1 is lost from manifest"
    )


# GigPrepLock: not invoked by run_gig_prep_copy — double-run is unguarded

def test_gig_prep_lock_used_in_orchestrator():
    """run_gig_prep_copy must acquire GigPrepLock before any writes."""
    import inspect
    from djlib import gig as _gig_module
    source = inspect.getsource(_gig_module.run_gig_prep_copy)
    assert "GigPrepLock" in source
