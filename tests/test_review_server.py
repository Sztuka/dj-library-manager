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
    assert b"<!doctype html>" in resp.data.lower()
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
    """Processed source should return a list (possibly empty)."""
    resp = client.get("/api/tracks?source=processed")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, list)


def test_tracks_processed_from_library_csv(client, tmp_path):
    """Processed endpoint reads library.csv filtered by destination folders."""
    from djlib.review import server as srv

    # Create a fake library.csv with tracks in different folders
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    lib_csv = data_dir / "library.csv"
    lib_csv.write_text(
        "external_source,external_track_id,track_id,old_full_path,artist,title,"
        "bpm,key,rating,color,duration_seconds,date_added,last_played,"
        "play_count,snapshot_date,rekordbox_id,traktor_id,cue_count\n"
        # Track in Music Library (should appear)
        "rekordbox,rb1,tid-aaa-111,"
        "/Users/test/Music Library/DJ Test/DJ Test - Track One [5A 128].mp3,"
        "DJ Test,Track One,128,5A,4,,200,2025-12-15,,5,,rb1,,0\n"
        # Track in Music Archive (should appear)
        "traktor,,tid-bbb-222,"
        "/Users/test/Music Archive/Unknown/Unknown.mp3,"
        "Unknown Artist,Unknown Track,120,3B,0,,180,2025-11-01,,0,,,,0\n"
        # Track in ~/Music (NOT processed — DJ software import, should be excluded)
        "rekordbox+traktor,rb2,tid-ccc-333,"
        "/Users/test/Music/Some DJ Track.mp3,"
        "Some DJ,Track,130,7A,3,,240,2025-10-01,,10,,rb2,,2\n"
    )

    old_repo = srv._REPO
    try:
        srv._REPO = tmp_path
        resp = client.get("/api/tracks?source=processed")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, list)
        assert len(data) == 2  # Only Music Library + Music Archive

        # Library track
        lib_track = [t for t in data if t["track_id"] == "tid-aaa-111"][0]
        assert lib_track["artist"] == "DJ Test"
        assert lib_track["title"] == "Track One"
        assert lib_track["bpm"] == "128"
        assert lib_track["key"] == "5A"
        assert lib_track["rating"] == "4"
        assert lib_track["play_count"] == "5"
        assert lib_track["destination"] == "library"
        assert lib_track["in_dj_software"] == "yes"
        assert lib_track["date_added"] == "2025-12-15"

        # Archive track
        arch_track = [t for t in data if t["track_id"] == "tid-bbb-222"][0]
        assert arch_track["destination"] == "archive"
        assert arch_track["in_dj_software"] == "yes"  # has external_source=traktor
    finally:
        srv._REPO = old_repo


def test_tracks_processed_no_duplicates(client, tmp_path):
    """Library.csv has unique track_ids, so processed should have no dupes."""
    from djlib.review import server as srv

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "library.csv").write_text(
        "external_source,track_id,old_full_path,artist,title,bpm,key,rating,play_count\n"
        "rekordbox,tid-001,/Users/test/Music Library/A/track.mp3,Artist A,Track A,128,5A,3,2\n"
        "traktor,tid-002,/Users/test/Music Library/B/track.mp3,Artist B,Track B,130,7B,0,0\n"
    )

    old_repo = srv._REPO
    try:
        srv._REPO = tmp_path
        resp = client.get("/api/tracks?source=processed")
        data = json.loads(resp.data)
        assert len(data) == 2
        tids = [t["track_id"] for t in data]
        assert len(tids) == len(set(tids))  # no duplicates
    finally:
        srv._REPO = old_repo


def test_tracks_processed_rejected_and_mixes(client, tmp_path):
    """Tracks in Music Rejected and Music Mixes are also processed."""
    from djlib.review import server as srv

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "library.csv").write_text(
        "external_source,track_id,old_full_path,artist,title,bpm,key,rating,play_count\n"
        "rekordbox,tid-rej,/Users/test/Music Rejected/bad.mp3,Bad,Track,120,1A,0,0\n"
        "traktor,tid-mix,/Users/test/Music Library/MIXES/set.mp3,DJ,Mix Set,125,,0,0\n"
    )

    old_repo = srv._REPO
    try:
        srv._REPO = tmp_path
        resp = client.get("/api/tracks?source=processed")
        data = json.loads(resp.data)
        assert len(data) == 2
        dests = {t["track_id"]: t["destination"] for t in data}
        assert dests["tid-rej"] == "rejected"
        # Music Library/MIXES → "library" since "Music Library" matches first
        assert dests["tid-mix"] == "library"
    finally:
        srv._REPO = old_repo


def test_tracks_processed_empty_library(client, tmp_path):
    """Empty library.csv returns empty list."""
    from djlib.review import server as srv

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "library.csv").write_text(
        "external_source,track_id,old_full_path,artist,title\n"
    )

    old_repo = srv._REPO
    try:
        srv._REPO = tmp_path
        resp = client.get("/api/tracks?source=processed")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data == []
    finally:
        srv._REPO = old_repo


# ── Reveal in Finder API ─────────────────────────────────────────────────────

def test_reveal_no_path(client):
    """Missing path returns 400."""
    resp = client.post("/api/reveal", json={})
    assert resp.status_code == 400


def test_reveal_file_not_found(client):
    """Non-existent file returns 404."""
    resp = client.post("/api/reveal", json={"path": "/tmp/nonexistent_djlib_test.mp3"})
    assert resp.status_code == 404


def test_reveal_success(client, tmp_path):
    """Valid file path triggers Finder reveal."""
    test_file = tmp_path / "test.mp3"
    test_file.write_bytes(b"\x00")

    with patch("djlib.review.server.subprocess.Popen") as mock_popen:
        resp = client.post("/api/reveal", json={"path": str(test_file)})
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        mock_popen.assert_called_once()


# ── AI Genre Suggest API ─────────────────────────────────────────────────────

def test_ai_status_returns_availability(client):
    """AI status endpoint returns availability flag."""
    with patch("djlib.review.server.get_openai_api_key", return_value=""):
        resp = client.get("/api/ai-status")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["available"] is False

    with patch("djlib.review.server.get_openai_api_key", return_value="sk-test"):
        resp = client.get("/api/ai-status")
        data = json.loads(resp.data)
        assert data["available"] is True


def test_suggest_genre_no_api_key(client):
    """Returns 501 when OpenAI API key is not configured."""
    with patch("djlib.review.server.get_openai_api_key", return_value=""):
        resp = client.post("/api/suggest-genre", json={
            "track_id": "test-123",
            "context": {"artist": "Test", "title": "Track"},
        })
        assert resp.status_code == 501
        data = json.loads(resp.data)
        assert "not configured" in data["error"]


def test_suggest_genre_missing_context(client):
    """Returns 400 when no artist or title provided."""
    with patch("djlib.review.server.get_openai_api_key", return_value="sk-test"):
        resp = client.post("/api/suggest-genre", json={
            "track_id": "test-123",
            "context": {},
        })
        assert resp.status_code == 400


def test_suggest_genre_success(client):
    """Successful AI genre suggestion with mocked OpenAI response."""
    mock_openai_response = {
        "choices": [{
            "message": {
                "content": '{"genre": "Tech House", "confidence": 0.92, "reasoning": "124 BPM, tribal elements"}'
            }
        }]
    }

    with patch("djlib.review.server.get_openai_api_key", return_value="sk-test"), \
         patch("djlib.review.server.http_requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json.return_value = mock_openai_response

        resp = client.post("/api/suggest-genre", json={
            "track_id": "test-456",
            "context": {
                "artist": "Test Artist",
                "title": "Test Track",
                "bpm": "124",
            },
        })
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["genre"] == "Tech House"
        assert data["confidence"] == 0.92
        assert "reasoning" in data


def test_suggest_genre_uses_cache(client):
    """Second request for same track_id returns cached result."""
    import djlib.review.server as srv
    # Pre-populate cache
    srv._ai_cache["cached-track"] = {
        "genre": "Afro House",
        "confidence": 0.88,
        "reasoning": "cached result",
    }

    with patch("djlib.review.server.get_openai_api_key", return_value="sk-test"):
        resp = client.post("/api/suggest-genre", json={
            "track_id": "cached-track",
            "context": {"artist": "X", "title": "Y"},
        })
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["genre"] == "Afro House"
        assert data["reasoning"] == "cached result"

    # Cleanup
    del srv._ai_cache["cached-track"]


def test_suggest_genre_openai_error(client):
    """Returns 502 when OpenAI API call fails."""
    with patch("djlib.review.server.get_openai_api_key", return_value="sk-test"), \
         patch("djlib.review.server.http_requests.post") as mock_post:
        mock_post.side_effect = Exception("Connection timeout")

        resp = client.post("/api/suggest-genre", json={
            "track_id": "test-err",
            "context": {"artist": "A", "title": "B"},
        })
        assert resp.status_code == 502
        data = json.loads(resp.data)
        assert "failed" in data["error"]

