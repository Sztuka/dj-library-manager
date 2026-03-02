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


def test_build_genre_prompt_remix_instruction():
    """Prompt includes remix-specific instructions when version contains remix keywords."""
    from djlib.review.server import _build_genre_prompt

    # Remix track: should include remix classification rule
    ctx_remix = {
        "artist": "Ol' Dirty Bastard",
        "title": "Got Your Money",
        "version": "Vik Toreus Remix",
        "bpm": "124",
    }
    prompt_remix = _build_genre_prompt(ctx_remix, ["Tech House", "Hip-Hop", "House"])
    assert "REMIX/EDIT CLASSIFICATION RULE" in prompt_remix
    assert "BPM genre ranges" in prompt_remix
    assert "Version/Remix: Vik Toreus Remix" in prompt_remix
    assert "BPM: 124" in prompt_remix

    # Original track: should NOT include remix rule
    ctx_original = {
        "artist": "Daft Punk",
        "title": "Around The World",
        "bpm": "121",
    }
    prompt_original = _build_genre_prompt(ctx_original, ["House", "French House"])
    assert "REMIX/EDIT CLASSIFICATION RULE" not in prompt_original
    assert "BPM genre ranges" in prompt_original


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
        # year field is always present (may be None)
        assert "year" in data


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


def test_enrich_track_returns_year_from_soundcloud(client, tmp_path):
    """Year from SoundCloud cache is included in enrich response."""
    from djlib.review import server as srv
    from djlib.metadata.genre_resolver import GenreResolution, SourceScore

    csv_path = _make_unsorted_csv(tmp_path, [{
        "track_id": "year-sc-1",
        "file_path": "/tmp/test/Artist - Title.wav",
        "artist": "Test Artist",
        "title": "Test Title",
        "version_info": "Extended Mix",
        "duration_suggest": "6:00",
        "tag_genre_original": "",
        "artist_suggest": "",
        "title_suggest": "",
        "genre_suggest": "",
    }])

    mock_result = GenreResolution(
        main="Deep House", subs=["Afro House"], confidence=0.80,
        breakdown=[
            SourceScore(source="soundcloud", weight=8.0, tags={"Deep House": 12.0}),
        ],
    )

    with patch.object(srv, "UNSORTED_CSV", csv_path), \
         patch.object(srv, "_resolve_genres", return_value=mock_result), \
         patch("djlib.metadata.soundcloud.get_cached_year", return_value="2023"):
        resp = client.post("/api/enrich-track", json={"track_id": "year-sc-1"})

    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["year"] == "2023"
    assert data["genre"] == "Deep House"


def test_enrich_track_returns_year_from_beatport(client, tmp_path):
    """Year from Beatport release_date is included when SC has no year."""
    from djlib.review import server as srv
    from djlib.metadata.genre_resolver import GenreResolution, SourceScore

    csv_path = _make_unsorted_csv(tmp_path, [{
        "track_id": "year-bp-1",
        "file_path": "/tmp/test/Artist - Title.wav",
        "artist": "Another Artist",
        "title": "Another Title",
        "version_info": "",
        "duration_suggest": "4:30",
        "tag_genre_original": "",
        "artist_suggest": "",
        "title_suggest": "",
        "genre_suggest": "",
    }])

    mock_result = GenreResolution(
        main="Tech House", subs=[], confidence=0.90,
        breakdown=[
            SourceScore(source="beatport", weight=25.0, tags={"Tech House": 30.0}),
        ],
    )

    with patch.object(srv, "UNSORTED_CSV", csv_path), \
         patch.object(srv, "_resolve_genres", return_value=mock_result), \
         patch("djlib.metadata.soundcloud.get_cached_year", return_value=None), \
         patch("djlib.metadata.beatport.search_track", return_value={"release_date": "2024-06-15"}):
        resp = client.post("/api/enrich-track", json={"track_id": "year-bp-1"})

    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["year"] == "2024"


def test_enrich_track_year_none_when_no_sources(client, tmp_path):
    """Year is null when neither SC nor BP have year data."""
    from djlib.review import server as srv
    from djlib.metadata.genre_resolver import GenreResolution, SourceScore

    csv_path = _make_unsorted_csv(tmp_path, [{
        "track_id": "year-none-1",
        "file_path": "/tmp/test/Artist - Title.wav",
        "artist": "No Year Artist",
        "title": "No Year Title",
        "version_info": "",
        "duration_suggest": "",
        "tag_genre_original": "",
        "artist_suggest": "",
        "title_suggest": "",
        "genre_suggest": "",
    }])

    mock_result = GenreResolution(
        main="House", subs=[], confidence=0.70,
        breakdown=[
            SourceScore(source="lastfm", weight=15.0, tags={"house": 20.0}),
        ],
    )

    with patch.object(srv, "UNSORTED_CSV", csv_path), \
         patch.object(srv, "_resolve_genres", return_value=mock_result), \
         patch("djlib.metadata.soundcloud.get_cached_year", return_value=None), \
         patch("djlib.metadata.beatport.search_track", return_value=None):
        resp = client.post("/api/enrich-track", json={"track_id": "year-none-1"})

    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["year"] is None
    assert data["genre"] == "House"


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


# ── AI Track Identify API ────────────────────────────────────────────────────

def test_identify_track_no_api_key(client):
    """Returns 501 when OpenAI API key is not configured."""
    with patch("djlib.review.server.get_openai_api_key", return_value=""):
        resp = client.post("/api/identify-track", json={"track_id": "test-123"})
        assert resp.status_code == 501
        data = json.loads(resp.data)
        assert "not configured" in data["error"]


def test_identify_track_no_body(client):
    """Returns 400 when no JSON body."""
    with patch("djlib.review.server.get_openai_api_key", return_value="sk-test"):
        resp = client.post("/api/identify-track")
        assert resp.status_code in (400, 415)


def test_identify_track_missing_track_id(client):
    """Returns 400 when track_id missing."""
    with patch("djlib.review.server.get_openai_api_key", return_value="sk-test"):
        resp = client.post("/api/identify-track", json={})
        assert resp.status_code == 400


def test_identify_track_not_found(client):
    """Returns 404 when track not in unsorted.csv."""
    with patch("djlib.review.server.get_openai_api_key", return_value="sk-test"):
        resp = client.post("/api/identify-track", json={"track_id": "nonexistent-xyz"})
        assert resp.status_code == 404


def test_identify_track_success(client, tmp_path):
    """Successful AI track identification with mocked OpenAI response."""
    from djlib.review import server as srv

    csv_path = _make_unsorted_csv(tmp_path, [{
        "track_id": "identify-test-1",
        "file_path": "/tmp/Music Unsorted/september maru w_ Dave Nunes.mp3",
        "artist": "",
        "title": "september maru w Dave Nunes",
        "version_info": "",
        "tag_artist_original": "",
        "tag_title_original": "september maru w Dave Nunes",
        "tag_genre_original": "",
        "artist_suggest": "",
        "title_suggest": "september maru w Dave Nunes",
        "version_suggest": "",
        "bpm": "122",
        "key_camelot": "5A",
        "duration_suggest": "6:45",
        "genres_soundcloud": "electronic, deep house",
        "genres_beatport": "",
        "genres_musicbrainz": "",
        "genres_lastfm": "",
        "genre_suggest": "",
        "year_suggest": "",
        "meta_source": "soundcloud",
    }])

    mock_openai_response = {
        "output_text": json.dumps({
            "artist": "Tera Kòrá",
            "title": "September Maru",
            "version": "feat. Dave Nunes",
            "year": "2023",
            "confidence": 0.75,
            "reasoning": "Filename suggests 'september maru' with 'w_ Dave Nunes' indicating featuring artist.",
        }),
        "output": [],
    }

    with patch.object(srv, "UNSORTED_CSV", csv_path), \
         patch("djlib.review.server.get_openai_api_key", return_value="sk-test"), \
         patch("djlib.review.server.http_requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json.return_value = mock_openai_response

        resp = client.post("/api/identify-track", json={"track_id": "identify-test-1"})
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["artist"] == "Tera Kòrá"
        assert data["title"] == "September Maru"
        assert data["version"] == "feat. Dave Nunes"
        assert data["year"] == "2023"
        assert data["confidence"] == 0.75
        assert "reasoning" in data

    # Clean up cache
    srv._identify_cache.pop("identify-test-1", None)


def test_identify_track_uses_cache(client):
    """Second request for same track_id returns cached result."""
    import djlib.review.server as srv

    srv._identify_cache["cached-identify"] = {
        "artist": "Cached Artist",
        "title": "Cached Title",
        "version": "",
        "year": "2024",
        "confidence": 0.9,
        "reasoning": "cached",
    }

    with patch("djlib.review.server.get_openai_api_key", return_value="sk-test"):
        resp = client.post("/api/identify-track", json={"track_id": "cached-identify"})
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["artist"] == "Cached Artist"
        assert data["reasoning"] == "cached"

    del srv._identify_cache["cached-identify"]


def test_identify_track_openai_error(client, tmp_path):
    """Returns 502 when OpenAI API call fails."""
    from djlib.review import server as srv

    csv_path = _make_unsorted_csv(tmp_path, [{
        "track_id": "identify-err-1",
        "file_path": "/tmp/test.wav",
        "artist": "Test",
        "title": "Track",
        "version_info": "",
        "tag_artist_original": "",
        "tag_title_original": "",
        "tag_genre_original": "",
        "artist_suggest": "",
        "title_suggest": "",
        "version_suggest": "",
        "bpm": "",
        "key_camelot": "",
        "duration_suggest": "",
        "genres_soundcloud": "",
        "genres_beatport": "",
        "genres_musicbrainz": "",
        "genres_lastfm": "",
        "genre_suggest": "",
        "year_suggest": "",
        "meta_source": "",
    }])

    with patch.object(srv, "UNSORTED_CSV", csv_path), \
         patch("djlib.review.server.get_openai_api_key", return_value="sk-test"), \
         patch("djlib.review.server.http_requests.post") as mock_post:
        mock_post.side_effect = Exception("Connection timeout")

        resp = client.post("/api/identify-track", json={"track_id": "identify-err-1"})
        assert resp.status_code == 502
        data = json.loads(resp.data)
        assert "failed" in data["error"]


def test_build_identify_prompt_content():
    """Prompt includes all available track context."""
    from djlib.review.server import _build_identify_prompt

    row = {
        "file_path": "~/Music Unsorted/september maru w_ Dave Nunes.mp3",
        "tag_artist_original": "",
        "tag_title_original": "september maru w Dave Nunes",
        "tag_genre_original": "Electronic",
        "artist_suggest": "",
        "title_suggest": "september maru feat. Dave Nunes",
        "version_suggest": "",
        "artist": "",
        "title": "september maru feat. Dave Nunes",
        "version_info": "",
        "bpm": "122",
        "key_camelot": "5A",
        "duration_suggest": "6:45",
        "genres_soundcloud": "electronic, deep house",
        "genres_beatport": "",
        "genres_musicbrainz": "",
        "genres_lastfm": "",
        "genre_suggest": "Deep House",
        "year_suggest": "",
        "meta_source": "soundcloud",
    }

    prompt = _build_identify_prompt(row)

    # Filename and folder present
    assert "september maru w_ Dave Nunes.mp3" in prompt
    assert "Music Unsorted" in prompt

    # Audio tags
    assert "september maru w Dave Nunes" in prompt
    assert "Electronic" in prompt

    # Audio characteristics
    assert "BPM: 122" in prompt
    assert "Key: 5A" in prompt
    assert "Duration: 6:45" in prompt

    # Online metadata
    assert "SoundCloud tags: electronic, deep house" in prompt

    # Identification rules
    assert "IDENTIFICATION RULES" in prompt
    assert "feat." in prompt
    assert "Title Case" in prompt
    assert "JSON" in prompt


def test_build_identify_prompt_minimal():
    """Prompt works with minimal data (just filename)."""
    from djlib.review.server import _build_identify_prompt

    row = {
        "file_path": "/tmp/unknown_track.wav",
        "tag_artist_original": "",
        "tag_title_original": "",
        "tag_genre_original": "",
        "artist_suggest": "",
        "title_suggest": "",
        "version_suggest": "",
        "artist": "",
        "title": "",
        "version_info": "",
        "bpm": "",
        "key_camelot": "",
        "duration_suggest": "",
        "genres_soundcloud": "",
        "genres_beatport": "",
        "genres_musicbrainz": "",
        "genres_lastfm": "",
        "genre_suggest": "",
        "year_suggest": "",
        "meta_source": "",
    }

    prompt = _build_identify_prompt(row)
    assert "unknown_track.wav" in prompt
    assert "IDENTIFICATION RULES" in prompt


# ── AI Chat tests ────────────────────────────────────────────────────────────

def test_ai_chat_no_api_key(client):
    """Returns 501 when OpenAI API key is not configured."""
    with patch("djlib.review.server.get_openai_api_key", return_value=""):
        resp = client.post("/api/ai-chat", json={"track_id": "t1", "message": "test"})
        assert resp.status_code == 501
        data = json.loads(resp.data)
        assert "not configured" in data["error"]


def test_ai_chat_no_body(client):
    """Returns 400 when no JSON body."""
    with patch("djlib.review.server.get_openai_api_key", return_value="sk-test"):
        resp = client.post("/api/ai-chat")
        assert resp.status_code in (400, 415)


def test_ai_chat_missing_track_id(client):
    """Returns 400 when track_id is missing."""
    with patch("djlib.review.server.get_openai_api_key", return_value="sk-test"):
        resp = client.post("/api/ai-chat", json={"message": "hello"})
        assert resp.status_code == 400


def test_ai_chat_empty_message(client):
    """Returns 400 when message is empty."""
    with patch("djlib.review.server.get_openai_api_key", return_value="sk-test"):
        resp = client.post("/api/ai-chat", json={"track_id": "t1", "message": ""})
        assert resp.status_code == 400


def test_ai_chat_track_not_found(client):
    """Returns 404 when track not in unsorted.csv."""
    with patch("djlib.review.server.get_openai_api_key", return_value="sk-test"):
        resp = client.post("/api/ai-chat", json={"track_id": "nonexistent", "message": "hi"})
        assert resp.status_code == 404


def test_ai_chat_reset(client):
    """Reset clears the session for a track."""
    import djlib.review.server as srv

    srv._chat_sessions["reset-test"] = {
        "messages": [{"role": "system", "content": "sys"}],
        "last_access": 0,
    }

    with patch("djlib.review.server.get_openai_api_key", return_value="sk-test"):
        resp = client.post("/api/ai-chat", json={"track_id": "reset-test", "reset": True})
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert data["history_length"] == 0

    assert "reset-test" not in srv._chat_sessions


def test_ai_chat_success(client, tmp_path):
    """Successful AI chat round-trip with suggestion block."""
    from djlib.review import server as srv

    csv_path = _make_unsorted_csv(tmp_path, [{
        "track_id": "chat-test-1",
        "file_path": "/tmp/Ethnica x We Dem Boyz (Loup Musa Edit).wav",
        "artist": "Ethnica",
        "title": "We Dem Boyz",
        "version_info": "Loup Musa Edit",
        "tag_artist_original": "",
        "tag_title_original": "",
        "tag_genre_original": "",
        "artist_suggest": "",
        "title_suggest": "",
        "version_suggest": "",
        "bpm": "128",
        "key_camelot": "7A",
        "duration_suggest": "5:30",
        "genres_soundcloud": "",
        "genres_beatport": "",
        "genres_musicbrainz": "",
        "genres_lastfm": "",
        "genre_suggest": "",
        "year_suggest": "",
        "meta_source": "",
    }])

    reply_text = (
        "This is a mashup/edit by Loup Musa combining Ethnica and We Dem Boyz.\n\n"
        "```suggestion\n"
        '{"artist": "Loup Musa", "title": "Ethnica x We Dem Boyz", "version_info": "Edit"}\n'
        "```"
    )

    mock_openai_response = {
        "output_text": reply_text,
        "output": [
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": reply_text, "annotations": []}
            ]}
        ],
    }

    with patch.object(srv, "UNSORTED_CSV", csv_path), \
         patch("djlib.review.server.get_openai_api_key", return_value="sk-test"), \
         patch("djlib.review.server.http_requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json.return_value = mock_openai_response

        resp = client.post("/api/ai-chat", json={
            "track_id": "chat-test-1",
            "message": "this is a mashup by Loup Musa, fix the metadata",
        })
        assert resp.status_code == 200
        data = json.loads(resp.data)

        # Reply text should NOT contain the suggestion block
        assert "```suggestion" not in data["reply"]
        assert "mashup" in data["reply"].lower()

        # Suggestion should be parsed
        assert data["suggestion"] is not None
        assert data["suggestion"]["artist"] == "Loup Musa"
        assert data["suggestion"]["title"] == "Ethnica x We Dem Boyz"
        assert data["suggestion"]["version_info"] == "Edit"

        # History length should be 2 (user msg + assistant msg)
        assert data["history_length"] == 2

    # Clean up
    srv._chat_sessions.pop("chat-test-1", None)


def test_ai_chat_conversation_history(client, tmp_path):
    """Session persists across multiple messages."""
    from djlib.review import server as srv

    csv_path = _make_unsorted_csv(tmp_path, [{
        "track_id": "chat-hist-1",
        "file_path": "/tmp/test.mp3",
        "artist": "Test",
        "title": "Track",
        "version_info": "",
        "tag_artist_original": "",
        "tag_title_original": "",
        "tag_genre_original": "",
        "artist_suggest": "",
        "title_suggest": "",
        "version_suggest": "",
        "bpm": "120",
        "key_camelot": "",
        "duration_suggest": "",
        "genres_soundcloud": "",
        "genres_beatport": "",
        "genres_musicbrainz": "",
        "genres_lastfm": "",
        "genre_suggest": "",
        "year_suggest": "",
        "meta_source": "",
    }])

    mock_resp1 = {
        "output_text": "Sure, this is a tech house track.",
        "output": [{"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "Sure, this is a tech house track.", "annotations": []}
        ]}],
    }
    mock_resp2 = {
        "output_text": "You're right, it sounds more like Afro House.\n\n```suggestion\n{\"genre\": \"Afro House\"}\n```",
        "output": [{"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "You're right, it sounds more like Afro House.\n\n```suggestion\n{\"genre\": \"Afro House\"}\n```", "annotations": []}
        ]}],
    }

    with patch.object(srv, "UNSORTED_CSV", csv_path), \
         patch("djlib.review.server.get_openai_api_key", return_value="sk-test"), \
         patch("djlib.review.server.http_requests.post") as mock_post:

        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None

        # First message
        mock_post.return_value.json.return_value = mock_resp1
        resp1 = client.post("/api/ai-chat", json={
            "track_id": "chat-hist-1",
            "message": "what genre is this?",
        })
        d1 = json.loads(resp1.data)
        assert d1["history_length"] == 2  # user + assistant

        # Second message
        mock_post.return_value.json.return_value = mock_resp2
        resp2 = client.post("/api/ai-chat", json={
            "track_id": "chat-hist-1",
            "message": "are you sure? listen to the rhythm, it sounds more afro",
        })
        d2 = json.loads(resp2.data)
        assert d2["history_length"] == 4  # 2 user + 2 assistant
        assert d2["suggestion"] is not None
        assert d2["suggestion"]["genre"] == "Afro House"

    srv._chat_sessions.pop("chat-hist-1", None)


def test_ai_chat_openai_error(client, tmp_path):
    """Returns 502 when OpenAI call fails, does not add failed message to session."""
    from djlib.review import server as srv

    csv_path = _make_unsorted_csv(tmp_path, [{
        "track_id": "chat-err-1",
        "file_path": "/tmp/test.wav",
        "artist": "Test",
        "title": "Track",
        "version_info": "",
        "tag_artist_original": "",
        "tag_title_original": "",
        "tag_genre_original": "",
        "artist_suggest": "",
        "title_suggest": "",
        "version_suggest": "",
        "bpm": "",
        "key_camelot": "",
        "duration_suggest": "",
        "genres_soundcloud": "",
        "genres_beatport": "",
        "genres_musicbrainz": "",
        "genres_lastfm": "",
        "genre_suggest": "",
        "year_suggest": "",
        "meta_source": "",
    }])

    with patch.object(srv, "UNSORTED_CSV", csv_path), \
         patch("djlib.review.server.get_openai_api_key", return_value="sk-test"), \
         patch("djlib.review.server.http_requests.post") as mock_post:
        mock_post.side_effect = Exception("Timeout")

        resp = client.post("/api/ai-chat", json={
            "track_id": "chat-err-1",
            "message": "hello",
        })
        assert resp.status_code == 502
        data = json.loads(resp.data)
        assert "failed" in data["error"]

    # Session should exist but without the failed user message
    entry = srv._chat_sessions.get("chat-err-1", {})
    msgs = entry.get("messages", []) if isinstance(entry, dict) else entry
    user_msgs = [m for m in msgs if m.get("role") == "user"]
    assert len(user_msgs) == 0

    srv._chat_sessions.pop("chat-err-1", None)


def test_parse_suggestion_block():
    """Tests suggestion block parsing from AI response text."""
    from djlib.review.server import _parse_suggestion_block

    # Standard suggestion block
    text1 = 'Here you go:\n\n```suggestion\n{"artist": "Loup Musa", "title": "Test"}\n```'
    result1 = _parse_suggestion_block(text1)
    assert result1 is not None
    assert result1["artist"] == "Loup Musa"
    assert result1["title"] == "Test"

    # json block
    text2 = 'Updated:\n\n```json\n{"genre": "Afro House"}\n```'
    result2 = _parse_suggestion_block(text2)
    assert result2 is not None
    assert result2["genre"] == "Afro House"

    # No block — should return None
    text3 = "I think this is Tech House. No changes needed."
    assert _parse_suggestion_block(text3) is None

    # Invalid JSON
    text4 = "```suggestion\n{not valid json}\n```"
    assert _parse_suggestion_block(text4) is None

    # Generic ``` block should NOT be parsed (avoid false positives)
    text5 = 'Use this format:\n\n```\n{"artist": "Oops"}\n```'
    assert _parse_suggestion_block(text5) is None


def test_parse_suggestion_block_version_normalization():
    """AI 'version' key is normalized to 'version_info' for CSV compatibility."""
    from djlib.review.server import _parse_suggestion_block

    # AI uses "version" — should be remapped to "version_info"
    text1 = '```suggestion\n{"artist": "Loup Musa", "title": "Ethnica x We Dem Boyz", "version": "Edit"}\n```'
    result1 = _parse_suggestion_block(text1)
    assert result1 is not None
    assert "version_info" in result1
    assert result1["version_info"] == "Edit"
    assert "version" not in result1

    # AI uses "version_info" directly — should be kept as-is
    text2 = '```suggestion\n{"version_info": "Remix"}\n```'
    result2 = _parse_suggestion_block(text2)
    assert result2 is not None
    assert result2["version_info"] == "Remix"

    # If both are present somehow, version_info takes precedence
    text3 = '```suggestion\n{"version": "Edit", "version_info": "Remix"}\n```'
    result3 = _parse_suggestion_block(text3)
    assert result3 is not None
    assert result3["version_info"] == "Remix"


def test_parse_suggestion_block_genre_validation():
    """Genre in suggestion block is validated against genres.yml."""
    from djlib.review.server import _parse_suggestion_block

    # Case-insensitive match should correct casing
    text = '```suggestion\n{"genre": "afro house"}\n```'
    result = _parse_suggestion_block(text)
    assert result is not None
    # Should be corrected to proper case from genres.yml
    assert result["genre"] == "Afro House"


def test_gather_track_context():
    """_gather_track_context returns formatted string with track info."""
    from djlib.review.server import _gather_track_context

    row = {
        "file_path": "/tmp/Music Unsorted/Test Artist - Test Title.mp3",
        "tag_artist_original": "Test Artist",
        "tag_title_original": "Test Title",
        "tag_genre_original": "House",
        "artist_suggest": "Test Artist",
        "title_suggest": "Test Title",
        "version_suggest": "Original Mix",
        "artist": "Test Artist",
        "title": "Test Title",
        "version_info": "Original Mix",
        "bpm": "126",
        "key_camelot": "8B",
        "duration_suggest": "7:20",
        "genres_soundcloud": "house, deep house",
        "genres_beatport": "House",
        "genres_musicbrainz": "",
        "genres_lastfm": "house",
        "genre_suggest": "House",
        "year_suggest": "2024",
        "meta_source": "beatport",
    }

    ctx = _gather_track_context(row)
    assert "Test Artist - Test Title.mp3" in ctx
    assert "BPM: 126" in ctx
    assert "Key: 8B" in ctx
    assert "house, deep house" in ctx
    assert "beatport" in ctx.lower() or "Beatport" in ctx


def test_build_chat_system_prompt():
    """Chat system prompt includes track context and formatting rules."""
    from djlib.review.server import _build_chat_system_prompt

    row = {
        "file_path": "/tmp/Ethnica x We Dem Boyz (Loup Musa Edit).wav",
        "tag_artist_original": "",
        "tag_title_original": "",
        "tag_genre_original": "",
        "artist_suggest": "",
        "title_suggest": "",
        "version_suggest": "",
        "artist": "Ethnica",
        "title": "We Dem Boyz",
        "version_info": "Loup Musa Edit",
        "bpm": "128",
        "key_camelot": "7A",
        "duration_suggest": "5:30",
        "genres_soundcloud": "",
        "genres_beatport": "",
        "genres_musicbrainz": "",
        "genres_lastfm": "",
        "genre_suggest": "",
        "year_suggest": "",
        "meta_source": "",
    }

    prompt = _build_chat_system_prompt(row)
    # Should contain track context
    assert "Ethnica x We Dem Boyz" in prompt
    assert "Loup Musa Edit" in prompt
    # Should contain mashup/edit formatting rules
    assert "mashup" in prompt.lower() or "edit" in prompt.lower()
    # Should mention suggestion block format
    assert "```suggestion" in prompt
    # Should reference genre list
    assert "genre" in prompt.lower()
    # Should mention web search capability
    assert "web search" in prompt.lower()


def test_ai_chat_web_search_used(client, tmp_path):
    """When AI uses web search, response includes web_search flag and sources."""
    from djlib.review import server as srv

    csv_path = _make_unsorted_csv(tmp_path, [{
        "track_id": "ws-test-1",
        "file_path": "/tmp/unknown_track.wav",
        "artist": "",
        "title": "",
        "version_info": "",
        "tag_artist_original": "",
        "tag_title_original": "",
        "tag_genre_original": "",
        "artist_suggest": "",
        "title_suggest": "",
        "version_suggest": "",
        "bpm": "126",
        "key_camelot": "",
        "duration_suggest": "",
        "genres_soundcloud": "",
        "genres_beatport": "",
        "genres_musicbrainz": "",
        "genres_lastfm": "",
        "genre_suggest": "",
        "year_suggest": "",
        "meta_source": "",
    }])

    reply_text = (
        "Based on my web search, this track is by Bicep.\n\n"
        "```suggestion\n"
        '{"artist": "Bicep", "title": "Glue", "year": "2017"}\n'
        "```"
    )
    mock_openai_response = {
        "output_text": reply_text,
        "output": [
            {"type": "web_search_call", "id": "ws_123", "status": "completed"},
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": reply_text, "annotations": [
                    {"type": "url_citation", "url": "https://www.beatport.com/track/glue/9876", "title": "Glue by Bicep - Beatport"},
                    {"type": "url_citation", "url": "https://www.discogs.com/release/123", "title": "Bicep - Glue - Discogs"},
                ]}
            ]}
        ],
    }

    with patch.object(srv, "UNSORTED_CSV", csv_path), \
         patch("djlib.review.server.get_openai_api_key", return_value="sk-test"), \
         patch("djlib.review.server.http_requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json.return_value = mock_openai_response

        resp = client.post("/api/ai-chat", json={
            "track_id": "ws-test-1",
            "message": "search online for this track",
        })
        assert resp.status_code == 200
        data = json.loads(resp.data)

        assert data["web_search"] is True
        assert len(data["sources"]) == 2
        assert data["sources"][0]["url"] == "https://www.beatport.com/track/glue/9876"
        assert data["sources"][0]["title"] == "Glue by Bicep - Beatport"
        assert data["suggestion"]["artist"] == "Bicep"
        assert data["suggestion"]["title"] == "Glue"

    srv._chat_sessions.pop("ws-test-1", None)


def test_ai_chat_no_web_search_flag(client, tmp_path):
    """When AI does NOT use web search, response omits web_search key."""
    from djlib.review import server as srv

    csv_path = _make_unsorted_csv(tmp_path, [{
        "track_id": "nows-test-1",
        "file_path": "/tmp/Artist - Title.mp3",
        "artist": "Artist",
        "title": "Title",
        "version_info": "",
        "tag_artist_original": "Artist",
        "tag_title_original": "Title",
        "tag_genre_original": "House",
        "artist_suggest": "Artist",
        "title_suggest": "Title",
        "version_suggest": "",
        "bpm": "126",
        "key_camelot": "8B",
        "duration_suggest": "6:00",
        "genres_soundcloud": "",
        "genres_beatport": "",
        "genres_musicbrainz": "",
        "genres_lastfm": "",
        "genre_suggest": "House",
        "year_suggest": "2024",
        "meta_source": "",
    }])

    mock_openai_response = {
        "output_text": "This looks like a house track from 2024. The metadata seems correct.",
        "output": [
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "This looks like a house track from 2024. The metadata seems correct.", "annotations": []}
            ]}
        ],
    }

    with patch.object(srv, "UNSORTED_CSV", csv_path), \
         patch("djlib.review.server.get_openai_api_key", return_value="sk-test"), \
         patch("djlib.review.server.http_requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json.return_value = mock_openai_response

        resp = client.post("/api/ai-chat", json={
            "track_id": "nows-test-1",
            "message": "what genre is this?",
        })
        assert resp.status_code == 200
        data = json.loads(resp.data)

        # web_search key should NOT be present when search wasn't used
        assert "web_search" not in data
        assert "sources" not in data

    srv._chat_sessions.pop("nows-test-1", None)


def test_call_openai_chat_responses_api_format():
    """_call_openai_chat sends correct Responses API payload and parses result."""
    from djlib.review.server import _call_openai_chat

    messages = [
        {"role": "system", "content": "You are a DJ assistant."},
        {"role": "user", "content": "Identify this track"},
    ]

    mock_response = {
        "output_text": "This is Bicep - Glue (2017).",
        "output": [
            {"type": "web_search_call", "id": "ws_abc", "status": "completed"},
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "This is Bicep - Glue (2017).", "annotations": [
                    {"type": "url_citation", "url": "https://example.com", "title": "Example"},
                ]}
            ]}
        ],
    }

    with patch("djlib.review.server.http_requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json.return_value = mock_response

        result = _call_openai_chat("sk-test", messages, max_tokens=400)

        # Check result structure
        assert result["text"] == "This is Bicep - Glue (2017)."
        assert result["web_search_used"] is True
        assert len(result["annotations"]) == 1
        assert result["annotations"][0]["url"] == "https://example.com"

        # Check the API was called with Responses API format
        call_args = mock_post.call_args
        assert "/v1/responses" in call_args[0][0]
        payload = call_args[1]["json"]
        assert payload["model"] == "gpt-4.1-mini"  # default ai_chat_model
        assert {"type": "web_search_preview"} in payload["tools"]
        assert payload["instructions"] == "You are a DJ assistant."
        # System message should NOT be in input
        assert all(m["role"] != "system" for m in payload["input"])
        assert payload["input"][0]["role"] == "user"


def test_call_openai_chat_empty_output_text_fallback():
    """When top-level output_text is empty, text is extracted from nested output."""
    from djlib.review.server import _call_openai_chat

    # Real API behavior: output_text at top level is empty for web search responses
    mock_response = {
        "output_text": "",
        "output": [
            {"type": "web_search_call", "id": "ws_abc", "status": "completed"},
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "The track \"Jamal\" by YASH was released on March 1, 2024. ([music.apple.com](https://music.apple.com/us/song/123))", "annotations": [
                    {"type": "url_citation", "url": "https://music.apple.com/us/song/123", "title": "Jamal - Song by YASH"},
                ]}
            ]}
        ],
    }

    with patch("djlib.review.server.http_requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json.return_value = mock_response

        result = _call_openai_chat("sk-test", [
            {"role": "system", "content": "You are a DJ assistant."},
            {"role": "user", "content": "what year?"},
        ])

        # Text should be extracted from nested output, not empty output_text
        assert result["text"] != ""
        assert "2024" in result["text"]
        assert "YASH" in result["text"]
        # Markdown citation should be stripped
        assert "([music.apple.com]" not in result["text"]
        assert "https://music.apple.com" not in result["text"]
        assert result["web_search_used"] is True
        assert len(result["annotations"]) == 1


def test_call_openai_chat_strips_markdown_citations():
    """Markdown citation patterns are stripped from AI reply text."""
    from djlib.review.server import _call_openai_chat

    text_with_citations = (
        'This track was released in 2017. ([beatport.com](https://www.beatport.com/track/123)) '
        'The artist is [Bicep](https://en.wikipedia.org/wiki/Bicep_(band)) from Belfast.'
    )
    mock_response = {
        "output_text": text_with_citations,
        "output": [
            {"type": "web_search_call", "id": "ws_1", "status": "completed"},
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": text_with_citations, "annotations": []}
            ]}
        ],
    }

    with patch("djlib.review.server.http_requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json.return_value = mock_response

        result = _call_openai_chat("sk-test", [
            {"role": "system", "content": "test"},
            {"role": "user", "content": "test"},
        ])

        # Parenthesized citations stripped entirely
        assert "([beatport.com]" not in result["text"]
        assert "beatport.com/track" not in result["text"]
        # Bare markdown links: text kept, URL stripped
        assert "Bicep" in result["text"]
        assert "wikipedia.org" not in result["text"]
        # Core content preserved
        assert "2017" in result["text"]
        assert "Belfast" in result["text"]


def test_call_openai_chat_no_output_graceful():
    """Handles edge case where output list has no message items."""
    from djlib.review.server import _call_openai_chat

    mock_response = {
        "output_text": "",
        "output": [],
    }

    with patch("djlib.review.server.http_requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json.return_value = mock_response

        result = _call_openai_chat("sk-test", [
            {"role": "system", "content": "test"},
            {"role": "user", "content": "test"},
        ])

        assert result["text"] == ""
        assert result["web_search_used"] is False
        assert result["annotations"] == []


def test_call_openai_responses_json_with_web_search():
    """_call_openai_responses_json uses Responses API with web search and parses JSON."""
    from djlib.review.server import _call_openai_responses_json

    result_json = {
        "artist": "Billie Eilish, Khalid",
        "title": "Lovely",
        "version": "BAI Extended",
        "year": "2018",
        "confidence": 0.95,
        "reasoning": "Original released April 2018, BAI remix found on SoundCloud.",
    }

    mock_response = {
        "output_text": json.dumps(result_json),
        "output": [
            {"type": "web_search_call", "id": "ws_1", "status": "completed"},
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": json.dumps(result_json), "annotations": []}
            ]}
        ],
    }

    with patch("djlib.review.server.http_requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json.return_value = mock_response

        result = _call_openai_responses_json("sk-test", "Identify this track")

        assert result["artist"] == "Billie Eilish, Khalid"
        assert result["year"] == "2018"
        assert result["confidence"] == 0.95

        # Verify Responses API was called with web_search_preview
        call_args = mock_post.call_args
        assert "/v1/responses" in call_args[0][0]
        payload = call_args[1]["json"]
        assert {"type": "web_search_preview"} in payload["tools"]


def test_call_openai_responses_json_with_markdown_fences():
    """Handles JSON wrapped in markdown fences from Responses API."""
    from djlib.review.server import _call_openai_responses_json

    fenced_json = '```json\n{"artist": "Test", "year": "2020"}\n```'

    mock_response = {
        "output_text": fenced_json,
        "output": [],
    }

    with patch("djlib.review.server.http_requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json.return_value = mock_response

        result = _call_openai_responses_json("sk-test", "test prompt")
        assert result["artist"] == "Test"
        assert result["year"] == "2020"


def test_call_openai_responses_json_nested_fallback():
    """Falls back to nested output when output_text is empty."""
    from djlib.review.server import _call_openai_responses_json

    result_json = '{"artist": "Nested", "title": "Fallback", "year": "2019"}'

    mock_response = {
        "output_text": "",
        "output": [
            {"type": "web_search_call", "id": "ws_1", "status": "completed"},
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": result_json, "annotations": []}
            ]}
        ],
    }

    with patch("djlib.review.server.http_requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json.return_value = mock_response

        result = _call_openai_responses_json("sk-test", "test prompt")
        assert result["artist"] == "Nested"
        assert result["year"] == "2019"


def test_chat_session_ttl_cleanup():
    """Expired sessions are cleaned up and LRU cap is enforced."""
    import time
    from djlib.review import server as srv

    original = dict(srv._chat_sessions)
    srv._chat_sessions.clear()

    try:
        # Add an old session (expired)
        srv._chat_sessions["old-track"] = {
            "messages": [{"role": "system", "content": "old"}],
            "last_access": time.time() - 7200,  # 2 hours ago
        }
        # Add a fresh session
        srv._chat_sessions["new-track"] = {
            "messages": [{"role": "system", "content": "new"}],
            "last_access": time.time(),
        }

        srv._cleanup_chat_sessions()

        assert "old-track" not in srv._chat_sessions
        assert "new-track" in srv._chat_sessions
    finally:
        srv._chat_sessions.clear()
        srv._chat_sessions.update(original)


def test_chat_session_lru_eviction():
    """LRU eviction removes oldest sessions when cap is exceeded."""
    import time
    from djlib.review import server as srv

    original = dict(srv._chat_sessions)
    srv._chat_sessions.clear()
    old_max = srv._CHAT_MAX_SESSIONS

    try:
        srv._CHAT_MAX_SESSIONS = 3

        for i in range(5):
            srv._chat_sessions[f"track-{i}"] = {
                "messages": [{"role": "system", "content": f"sys-{i}"}],
                "last_access": time.time() + i,
            }

        srv._cleanup_chat_sessions()

        assert len(srv._chat_sessions) == 3
        # Oldest two (track-0, track-1) should be evicted
        assert "track-0" not in srv._chat_sessions
        assert "track-1" not in srv._chat_sessions
        assert "track-4" in srv._chat_sessions
    finally:
        srv._chat_sessions.clear()
        srv._chat_sessions.update(original)
        srv._CHAT_MAX_SESSIONS = old_max


def test_ai_chat_track_deleted_clears_session(client, tmp_path):
    """If track is deleted while chatting, session is cleaned up and 404 returned."""
    from djlib.review import server as srv

    csv_path = _make_unsorted_csv(tmp_path, [{
        "track_id": "del-test-1",
        "file_path": "/tmp/test.wav",
        "artist": "Test",
        "title": "Track",
        "version_info": "",
        "tag_artist_original": "",
        "tag_title_original": "",
        "tag_genre_original": "",
        "artist_suggest": "",
        "title_suggest": "",
        "version_suggest": "",
        "bpm": "",
        "key_camelot": "",
        "duration_suggest": "",
        "genres_soundcloud": "",
        "genres_beatport": "",
        "genres_musicbrainz": "",
        "genres_lastfm": "",
        "genre_suggest": "",
        "year_suggest": "",
        "meta_source": "",
    }])

    srv._chat_sessions["gone-track"] = {
        "messages": [{"role": "system", "content": "sys"}],
        "last_access": 0,
    }

    with patch.object(srv, "UNSORTED_CSV", csv_path), \
         patch("djlib.review.server.get_openai_api_key", return_value="sk-test"):
        resp = client.post("/api/ai-chat", json={
            "track_id": "gone-track",
            "message": "hello",
        })
        assert resp.status_code == 404
        assert "no longer exists" in json.loads(resp.data)["error"]

    assert "gone-track" not in srv._chat_sessions


def test_ai_chat_stale_prompt_refresh(client, tmp_path):
    """System prompt is refreshed with latest track data on each request."""
    import time
    from djlib.review import server as srv

    csv_path = _make_unsorted_csv(tmp_path, [{
        "track_id": "stale-1",
        "file_path": "/tmp/test.mp3",
        "artist": "Old Artist",
        "title": "Old Title",
        "version_info": "",
        "tag_artist_original": "",
        "tag_title_original": "",
        "tag_genre_original": "",
        "artist_suggest": "",
        "title_suggest": "",
        "version_suggest": "",
        "bpm": "120",
        "key_camelot": "",
        "duration_suggest": "",
        "genres_soundcloud": "",
        "genres_beatport": "",
        "genres_musicbrainz": "",
        "genres_lastfm": "",
        "genre_suggest": "",
        "year_suggest": "",
        "meta_source": "",
    }])

    mock_resp = {"choices": [{"message": {"content": "OK, noted."}}]}

    with patch.object(srv, "UNSORTED_CSV", csv_path), \
         patch("djlib.review.server.get_openai_api_key", return_value="sk-test"), \
         patch("djlib.review.server.http_requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None
        mock_post.return_value.json.return_value = mock_resp

        # First message
        resp1 = client.post("/api/ai-chat", json={
            "track_id": "stale-1",
            "message": "hello",
        })
        assert resp1.status_code == 200

        entry = srv._chat_sessions["stale-1"]
        sys_msg = entry["messages"][0]["content"]
        assert "Old Artist" in sys_msg

        # Update CSV to change artist (simulating table edit)
        import csv as csv_mod
        rows = []
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv_mod.DictReader(f)
            for row in reader:
                if row["track_id"] == "stale-1":
                    row["artist"] = "New Artist"
                rows.append(row)
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv_mod.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

        # Second message — should refresh system prompt
        resp2 = client.post("/api/ai-chat", json={
            "track_id": "stale-1",
            "message": "what about now?",
        })
        assert resp2.status_code == 200

        sys_msg2 = srv._chat_sessions["stale-1"]["messages"][0]["content"]
        assert "New Artist" in sys_msg2

    srv._chat_sessions.pop("stale-1", None)

