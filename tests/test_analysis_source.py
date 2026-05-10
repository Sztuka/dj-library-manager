"""Tests for PR3: `analysis_source` column + `--allow-no-rekordbox` contract.

The scan/apply contract used to contradict itself: `scan` (non-strict) happily
accepted tracks whose BPM+Key came from ID3 tags, but `apply` then blocked
them with `NO_REKORDBOX_ID`. A DJ could process a file all the way to the
Review UI, click apply, and silently nothing happened.

These tests lock in the fix:
- CLI exposes `--allow-no-rekordbox` on `apply`.
- The REVIEW UI processed endpoint exposes `analysis_source` and
  `ready_for_rekordbox` so the UI can badge tag-only tracks.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from djlib.review.server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ── CLI flag ────────────────────────────────────────────────────────────────

def test_apply_cli_has_allow_no_rekordbox_flag():
    """Regression: the flag must exist on the `apply` subcommand."""
    from djlib.cli import build_parser

    parser = build_parser()
    # argparse raises SystemExit on unknown args; success = no exit
    args = parser.parse_args(["apply", "--allow-no-rekordbox"])
    assert args.allow_no_rekordbox is True

    args = parser.parse_args(["apply"])
    assert args.allow_no_rekordbox is False


# ── REVIEW UI exposes analysis_source + ready_for_rekordbox ─────────────────

def test_processed_exposes_analysis_source_and_readiness(client, tmp_path, monkeypatch):
    """REVIEW UI needs to tell the DJ which tracks are tag-only (still
    waiting for Rekordbox analysis) vs. fully analyzed."""
    from djlib.review import server as srv

    base = tmp_path.resolve()
    lib = base / "Music Library"
    roots = [
        ("mixes", lib / "MIXES"),
        ("rejected", base / "Music Rejected"),
        ("library", lib),
    ]
    monkeypatch.setattr(srv, "_get_processed_dest_roots", lambda: roots)

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "library.csv").write_text(
        "track_id,rekordbox_id,external_source,analysis_source,old_full_path,artist,title,bpm,key\n"
        # Fully analyzed by Rekordbox
        f"tid-rb,rb-1,rekordbox,rekordbox,{base}/Music Library/A/a.mp3,A,A,128,5A\n"
        # Tag-only: applied via --allow-no-rekordbox, no Rekordbox ID yet
        f"tid-tag,,,tags,{base}/Music Library/B/b.mp3,B,B,120,7A\n"
    )

    old_repo = srv._REPO
    try:
        srv._REPO = tmp_path
        resp = client.get("/api/tracks?source=processed")
        data = json.loads(resp.data)
        by_tid = {t["track_id"]: t for t in data}

        rb_track = by_tid["tid-rb"]
        assert rb_track["analysis_source"] == "rekordbox"
        assert rb_track["ready_for_rekordbox"] == "yes"
        assert rb_track["in_dj_software"] == "yes"

        tag_track = by_tid["tid-tag"]
        assert tag_track["analysis_source"] == "tags"
        assert tag_track["ready_for_rekordbox"] == "no"
        # Not in any DJ software yet
        assert tag_track["in_dj_software"] == "no"
    finally:
        srv._REPO = old_repo


def test_processed_defaults_when_analysis_source_missing(client, tmp_path, monkeypatch):
    """Old library.csv files (pre-PR3) have no `analysis_source` column.
    The endpoint must default gracefully: empty string, readiness inferred
    from presence of rekordbox_id alone."""
    from djlib.review import server as srv

    base = tmp_path.resolve()
    lib = base / "Music Library"
    monkeypatch.setattr(
        srv,
        "_get_processed_dest_roots",
        lambda: [("library", lib)],
    )

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "library.csv").write_text(
        # No analysis_source column at all (legacy schema)
        "track_id,rekordbox_id,external_source,old_full_path,artist,title\n"
        f"tid-old,rb-7,rekordbox,{base}/Music Library/X/x.mp3,X,X\n"
    )

    old_repo = srv._REPO
    try:
        srv._REPO = tmp_path
        resp = client.get("/api/tracks?source=processed")
        data = json.loads(resp.data)
        assert len(data) == 1
        assert data[0]["analysis_source"] == ""
        assert data[0]["ready_for_rekordbox"] == "yes"  # has rekordbox_id
    finally:
        srv._REPO = old_repo
