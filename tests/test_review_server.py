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


# ── Enrich Track API ─────────────────────────────────────────────────────────

def _make_unsorted_csv(tmp_dir: Path, rows: List[Dict[str, str]]) -> Path:
    """Create a test unsorted.csv with given rows."""
    csv_path = tmp_dir / "unsorted.csv"
    if not rows:
        csv_path.write_text("")
        return csv_path
    fieldnames = list(rows[0].keys())
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


def test_enrich_track_no_body(client):
    """Returns 400 when no JSON body."""
    resp = client.post("/api/enrich-track")
    assert resp.status_code in (400, 415)


def test_enrich_track_missing_track_id(client):
    """Returns 400 when track_id missing."""
    resp = client.post("/api/enrich-track", json={})
    assert resp.status_code == 400


def test_enrich_track_not_found(client):
    """Returns 404 when track not in unsorted.csv."""
    resp = client.post("/api/enrich-track", json={"track_id": "nonexistent-123"})
    assert resp.status_code == 404


def test_enrich_track_success(client, tmp_path):
    """Successful enrichment returns genre data from resolver."""
    from djlib.review import server as srv
    from djlib.metadata.genre_resolver import GenreResolution, SourceScore

    csv_path = _make_unsorted_csv(tmp_path, [{
        "track_id": "enrich-test-1",
        "file_path": "/tmp/test/Artist - Title.wav",
        "artist": "Natasha Bedingfield",
        "title": "Unwritten",
        "version_info": "Talon Afrohouse Remix",
        "duration_suggest": "5:30",
        "tag_genre_original": "",
        "artist_suggest": "",
        "title_suggest": "",
        "genre_suggest": "",
    }])

    mock_result = GenreResolution(
        main="Afro House",
        subs=["Deep House"],
        confidence=0.85,
        breakdown=[
            SourceScore(source="beatport", weight=30.0, tags={"Afro House": 40.0}),
            SourceScore(source="lastfm", weight=15.0, tags={"afro house": 15.0, "house": 10.0}),
        ],
    )

    with patch.object(srv, "UNSORTED_CSV", csv_path), \
         patch.object(srv, "_resolve_genres", return_value=mock_result):
        resp = client.post("/api/enrich-track", json={"track_id": "enrich-test-1"})

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["genre"] == "Afro House"
        assert data["genre_full"] == "Afro House, Deep House"
        assert data["confidence"] == 0.85
        assert "beatport" in data["sources"]
        # source_genres maps resolver sources to CSV column names
        assert "genres_beatport" in data["source_genres"]
        assert "Afro House" in data["source_genres"]["genres_beatport"]
        assert "genres_lastfm" in data["source_genres"]
        # meta_source is pipe-separated sorted source list
        assert data["meta_source"] == "beatport|lastfm"


def test_enrich_track_no_results(client, tmp_path):
    """Returns null genre when resolver finds nothing."""
    from djlib.review import server as srv

    csv_path = _make_unsorted_csv(tmp_path, [{
        "track_id": "enrich-empty-1",
        "file_path": "/tmp/test/Unknown - Track.wav",
        "artist": "Unknown",
        "title": "Track",
        "version_info": "",
        "duration_suggest": "",
        "tag_genre_original": "",
        "artist_suggest": "",
        "title_suggest": "",
        "genre_suggest": "",
    }])

    with patch.object(srv, "UNSORTED_CSV", csv_path), \
         patch.object(srv, "_resolve_genres", return_value=None):
        resp = client.post("/api/enrich-track", json={"track_id": "enrich-empty-1"})

    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["genre"] is None
    assert data["confidence"] == 0


def test_enrich_track_empty_artist_title(client, tmp_path):
    """Returns 400 when both artist and title are empty."""
    from djlib.review import server as srv

    csv_path = _make_unsorted_csv(tmp_path, [{
        "track_id": "enrich-empty-2",
        "file_path": "/tmp/test/file.wav",
        "artist": "",
        "title": "",
        "version_info": "",
        "duration_suggest": "",
        "tag_genre_original": "",
        "artist_suggest": "Old Suggest",
        "title_suggest": "Old Suggest",
        "genre_suggest": "",
    }])

    with patch.object(srv, "UNSORTED_CSV", csv_path):
        resp = client.post("/api/enrich-track", json={"track_id": "enrich-empty-2"})

    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert "No artist or title" in data["error"]


def test_enrich_uses_user_edited_fields(client, tmp_path):
    """Enrichment passes user-edited artist/title, not artist_suggest."""
    from djlib.review import server as srv
    from djlib.metadata.genre_resolver import GenreResolution, SourceScore

    csv_path = _make_unsorted_csv(tmp_path, [{
        "track_id": "enrich-edit-1",
        "file_path": "/tmp/test/Wrong - Order.wav",
        "artist": "Natasha Bedingfield",   # User edited this
        "title": "Unwritten",              # User edited this
        "version_info": "Talon Remix",
        "duration_suggest": "5:00",
        "tag_genre_original": "",
        "artist_suggest": "Unwritten",     # STALE from bad parse
        "title_suggest": "Natasha Bedingfield",  # STALE from bad parse
        "genre_suggest": "",
    }])

    mock_result = GenreResolution(
        main="Afro House", subs=[], confidence=0.9,
        breakdown=[SourceScore(source="beatport", weight=30.0, tags={"Afro House": 40.0})],
    )
    captured_args = {}

    def fake_resolve(artist, title, version="", **kwargs):
        captured_args["artist"] = artist
        captured_args["title"] = title
        captured_args["version"] = version
        return mock_result

    with patch.object(srv, "UNSORTED_CSV", csv_path), \
         patch.object(srv, "_resolve_genres", side_effect=fake_resolve):
        resp = client.post("/api/enrich-track", json={"track_id": "enrich-edit-1"})

    assert resp.status_code == 200
    # Verify it used the user-edited values, NOT the stale suggest values
    assert captured_args["artist"] == "Natasha Bedingfield"
    assert captured_args["title"] == "Unwritten"
    assert captured_args["version"] == "Talon Remix"


# ── Swap Artist/Title API ────────────────────────────────────────────────────

def test_swap_no_body(client):
    """Returns 400 when no JSON body."""
    resp = client.post("/api/swap-artist-title")
    assert resp.status_code in (400, 415)


def test_swap_missing_track_id(client):
    """Returns 400 when track_id missing."""
    resp = client.post("/api/swap-artist-title", json={})
    assert resp.status_code == 400


def test_swap_track_not_found(client):
    """Returns 404 when track not found."""
    resp = client.post("/api/swap-artist-title", json={"track_id": "nonexistent"})
    assert resp.status_code == 404


def test_swap_success(client, tmp_path):
    """Swaps artist and title via filename re-parse and returns new values."""
    from djlib.review import server as srv

    csv_path = _make_unsorted_csv(tmp_path, [{
        "track_id": "swap-test-1",
        "file_path": str(tmp_path / "Unwritten (Talon Remix) - Natasha Bedingfield.wav"),
        "artist": "Unwritten (Talon Remix)",
        "title": "Natasha Bedingfield",
        "version_info": "",
        "artist_suggest": "Unwritten (Talon Remix)",
        "title_suggest": "Natasha Bedingfield",
    }])
    # Create the file so Path.expanduser works (not strictly needed but clean)
    (tmp_path / "Unwritten (Talon Remix) - Natasha Bedingfield.wav").touch()

    with patch.object(srv, "UNSORTED_CSV", csv_path):
        resp = client.post("/api/swap-artist-title", json={"track_id": "swap-test-1"})

    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["ok"] is True
    # The parser detects reversed order (first part has "Remix") and auto-swaps
    assert data["artist"] == "Natasha Bedingfield"
    assert data["title"] == "Unwritten"
    assert data["version_info"] == "Talon Remix"

    # Verify CSV was updated
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert rows[0]["artist"] == "Natasha Bedingfield"
    assert rows[0]["title"] == "Unwritten"
    assert rows[0]["version_info"] == "Talon Remix"
    # Suggest fields also swapped
    assert rows[0]["artist_suggest"] == "Natasha Bedingfield"
    assert rows[0]["title_suggest"] == "Unwritten (Talon Remix)"


def test_swap_reparse_dash_in_parens(client, tmp_path):
    """Swap correctly handles filenames with dashes inside parentheses.

    Filename: 'Unwritten (Talon Afrohouse Remix - Extended) - Natasha Bedingfield.wav'
    Initial parse was broken (dash inside parens split into 3 segments).
    Swap should re-parse and fix artist, title, AND version_info.
    """
    from djlib.review import server as srv

    csv_path = _make_unsorted_csv(tmp_path, [{
        "track_id": "swap-dash-1",
        "file_path": str(tmp_path / "Unwritten (Talon Afrohouse Remix - Extended) - Natasha Bedingfield.wav"),
        "artist": "Unwritten (Talon Afrohouse Remix",
        "title": "Extended)",
        "version_info": "Natasha Bedingfield",
        "artist_suggest": "Unwritten (Talon Afrohouse Remix",
        "title_suggest": "Extended)",
    }])
    (tmp_path / "Unwritten (Talon Afrohouse Remix - Extended) - Natasha Bedingfield.wav").touch()

    with patch.object(srv, "UNSORTED_CSV", csv_path):
        resp = client.post("/api/swap-artist-title", json={"track_id": "swap-dash-1"})

    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["ok"] is True
    # Parser now correctly handles dash inside parens + auto-detects reversed order
    assert data["artist"] == "Natasha Bedingfield"
    assert data["title"] == "Unwritten"
    assert data["version_info"] == "Talon Afrohouse Remix - Extended"


def test_swap_no_filepath_naive_swap(client, tmp_path):
    """When no file_path, fall back to naive artist/title swap."""
    from djlib.review import server as srv

    csv_path = _make_unsorted_csv(tmp_path, [{
        "track_id": "swap-nofile-1",
        "file_path": "",
        "artist": "Title Here",
        "title": "Artist Here",
        "version_info": "Original Mix",
        "artist_suggest": "",
        "title_suggest": "",
    }])

    with patch.object(srv, "UNSORTED_CSV", csv_path):
        resp = client.post("/api/swap-artist-title", json={"track_id": "swap-nofile-1"})

    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["artist"] == "Artist Here"
    assert data["title"] == "Title Here"
    assert data["version_info"] == "Original Mix"


# ── Swap Detection ───────────────────────────────────────────────────────────

def test_detect_swap_positive():
    """Detects swapped artist/title when filename parsing differs from current fields."""
    from djlib.review.server import _detect_artist_title_swap

    # Filename: "Natasha Bedingfield - Unwritten.wav"
    # But current fields have them swapped
    row = {
        "file_path": "/tmp/Music/Natasha Bedingfield - Unwritten.wav",
        "artist": "Unwritten",             # This looks like a title
        "title": "Natasha Bedingfield",     # This looks like an artist
    }
    result = _detect_artist_title_swap(row)
    assert result is not None
    assert result["swapped"] is True
    assert result["suggested_artist"] == "Natasha Bedingfield"
    assert result["suggested_title"] == "Unwritten"


def test_detect_swap_negative():
    """No swap suggestion when current fields match filename."""
    from djlib.review.server import _detect_artist_title_swap

    row = {
        "file_path": "/tmp/Music/Natasha Bedingfield - Unwritten.wav",
        "artist": "Natasha Bedingfield",
        "title": "Unwritten",
    }
    result = _detect_artist_title_swap(row)
    assert result is None


def test_detect_swap_no_file_path():
    """Returns None when no file path."""
    from djlib.review.server import _detect_artist_title_swap

    result = _detect_artist_title_swap({"artist": "A", "title": "B"})
    assert result is None


def test_detect_swap_empty_artist_title():
    """Returns None when artist or title is empty."""
    from djlib.review.server import _detect_artist_title_swap

    result = _detect_artist_title_swap({
        "file_path": "/tmp/test.wav",
        "artist": "",
        "title": "Something",
    })
    assert result is None

