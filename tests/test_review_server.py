"""Tests for the review UI server (djlib.review.server)."""
from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch

import pytest

from djlib.review.server import app, _load_genres


@pytest.fixture
def client():
    """Flask test client."""
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ── Index page ───────────────────────────────────────────────────────────────

def test_index_returns_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"<!DOCTYPE html>" in resp.data
    assert b"Review" in resp.data


# ── Genres API ───────────────────────────────────────────────────────────────

def test_genres_returns_list(client):
    resp = client.get("/api/genres")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, list)
    assert len(data) > 0
    # Check alphabetical order
    assert data == sorted(data)


def test_load_genres_returns_labels():
    labels = _load_genres()
    assert isinstance(labels, list)
    assert len(labels) > 0
    # Should be strings
    assert all(isinstance(g, str) for g in labels)


# ── Tracks API ───────────────────────────────────────────────────────────────

def test_tracks_unsorted(client):
    resp = client.get("/api/tracks?source=unsorted")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, list)
    # If unsorted.csv exists, should have data
    # (may be empty in CI, but structure should be valid)


def test_tracks_library(client):
    resp = client.get("/api/tracks?source=library")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, list)


def test_tracks_invalid_source(client):
    resp = client.get("/api/tracks?source=bogus")
    assert resp.status_code == 400


# ── Audio streaming ──────────────────────────────────────────────────────────

def test_audio_no_path(client):
    resp = client.get("/api/audio")
    assert resp.status_code == 400


def test_audio_missing_file(client):
    resp = client.get("/api/audio?path=/nonexistent/file.mp3")
    assert resp.status_code == 404


def test_audio_serves_file(client, tmp_path):
    """Create a temp file and verify audio endpoint streams it."""
    p = tmp_path / "test.mp3"
    p.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 100)  # minimal MP3-like header
    resp = client.get(f"/api/audio?path={p}")
    assert resp.status_code == 200
    assert resp.content_type.startswith("audio/")


# ── Track update API ────────────────────────────────────────────────────────

def test_update_no_body(client):
    resp = client.post("/api/tracks/update")
    assert resp.status_code in (400, 415)


def test_update_missing_track_id(client):
    resp = client.post(
        "/api/tracks/update",
        json={"fields": {"status": "accept"}},
    )
    assert resp.status_code == 400


def test_update_no_fields(client):
    resp = client.post(
        "/api/tracks/update",
        json={"track_id": "fake-id"},
    )
    assert resp.status_code == 400


def test_update_track_not_found(client):
    resp = client.post(
        "/api/tracks/update",
        json={"track_id": "nonexistent-id-12345", "fields": {"status": "accept"}},
    )
    assert resp.status_code == 404


# ── Processed tracks API ─────────────────────────────────────────────────────

def test_tracks_processed_returns_list(client):
    """Processed source should return a list (possibly empty if no move logs)."""
    resp = client.get("/api/tracks?source=processed")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, list)


def test_tracks_processed_from_move_logs(client, tmp_path):
    """Processed endpoint reads LOGS/moves-*.csv and enriches from library.csv."""
    from djlib.review import server as srv

    # Create a fake move log
    logs_dir = tmp_path / "LOGS"
    logs_dir.mkdir()
    move_csv = logs_dir / "moves-20260215-120000.csv"
    move_csv.write_text(
        "src,dest,track_id\n"
        "/Music Unsorted/DJ Test - Track One.mp3,"
        "/Music Library/DJ Test/DJ Test - Track One [5A 128].mp3,"
        "tid-aaa-111\n"
        "/Music Unsorted/Unknown.mp3,"
        "/Music Archive/Unknown/Unknown.mp3,"
        "tid-bbb-222\n"
    )

    # Create a fake library.csv
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    lib_csv = data_dir / "library.csv"
    lib_csv.write_text(
        "external_source,external_track_id,track_id,old_full_path,artist,title,"
        "bpm,key,rating,color,duration_seconds,date_added,last_played,"
        "play_count,snapshot_date,rekordbox_id,traktor_id,cue_count\n"
        "rekordbox,rb1,tid-aaa-111,"
        "/Music Library/DJ Test/DJ Test - Track One [5A 128].mp3,"
        "DJ Test,Track One,128,5A,4,,200,2025-12-15,,5,,rb1,,0\n"
    )

    old_repo = srv._REPO
    try:
        srv._REPO = tmp_path
        resp = client.get("/api/tracks?source=processed")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, list)
        assert len(data) == 2

        # First should be enriched (matched by track_id)
        enriched = [t for t in data if t["track_id"] == "tid-aaa-111"][0]
        assert enriched["artist"] == "DJ Test"
        assert enriched["title"] == "Track One"
        assert enriched["bpm"] == "128"
        assert enriched["rating"] == "4"
        assert enriched["play_count"] == "5"
        assert enriched["destination"] == "library"
        assert enriched["in_dj_software"] == "yes"
        assert enriched["move_date"] == "2026-02-15"

        # Second should be parsed from filename (no library match)
        parsed = [t for t in data if t["track_id"] == "tid-bbb-222"][0]
        assert parsed["destination"] == "archive"
        assert parsed["in_dj_software"] == "no"
        # No " - " separator in filename → title gets the whole name
        assert parsed["title"] == "Unknown"
    finally:
        srv._REPO = old_repo


def test_tracks_processed_deduplicates(client, tmp_path):
    """When same track_id appears in multiple move logs, last one wins."""
    from djlib.review import server as srv

    logs_dir = tmp_path / "LOGS"
    logs_dir.mkdir()

    # Earlier log
    (logs_dir / "moves-20260101-100000.csv").write_text(
        "src,dest,track_id\n"
        "/old/src.mp3,/Music Library/Old/path.mp3,tid-dup-001\n"
    )
    # Later log (should win)
    (logs_dir / "moves-20260201-100000.csv").write_text(
        "src,dest,track_id\n"
        "/new/src.mp3,/Music Library/New/path.mp3,tid-dup-001\n"
    )

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "library.csv").write_text(
        "track_id,old_full_path,artist,title,bpm,key,rating,play_count\n"
    )

    old_repo = srv._REPO
    try:
        srv._REPO = tmp_path
        resp = client.get("/api/tracks?source=processed")
        data = json.loads(resp.data)
        assert len(data) == 1
        assert "/Music Library/New/path.mp3" in data[0]["file_path"]
        assert data[0]["move_date"] == "2026-02-01"
    finally:
        srv._REPO = old_repo


def test_tracks_processed_empty_logs_dir(client, tmp_path):
    """Empty LOGS directory returns empty list."""
    from djlib.review import server as srv

    logs_dir = tmp_path / "LOGS"
    logs_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "library.csv").write_text("track_id,artist,title\n")

    old_repo = srv._REPO
    try:
        srv._REPO = tmp_path
        resp = client.get("/api/tracks?source=processed")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data == []
    finally:
        srv._REPO = old_repo
