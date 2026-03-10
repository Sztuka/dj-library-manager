"""Tests for djlib.ai_classify module."""
from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path
from typing import Dict, List
from unittest.mock import MagicMock, patch

import pytest

from djlib.ai_classify import (
    _build_track_info,
    _validate_result,
    build_classify_prompt,
    classify_track,
    format_version_for_csv,
    format_version_for_filename,
    load_genre_labels,
)


# ── Genre loading ────────────────────────────────────────────────────────────


def test_load_genre_labels():
    """Should load genres from genres.yml and return sorted list."""
    labels = load_genre_labels()
    assert isinstance(labels, list)
    assert len(labels) > 0
    assert labels == sorted(labels)
    assert "Tech House" in labels
    assert "Afro House" in labels


# ── Track info extraction ────────────────────────────────────────────────────


def test_build_track_info_full():
    """Should extract all available metadata fields."""
    row = {
        "file_path": "~/Music Unsorted/Deep House/Artist - Title (Remix).mp3",
        "tag_artist_original": "Some Artist",
        "tag_title_original": "Some Title",
        "tag_genre_original": "House",
        "artist_suggest": "Parsed Artist",
        "title_suggest": "Parsed Title",
        "version_suggest": "Remix",
        "bpm": "124",
        "key_camelot": "5A",
        "duration_suggest": "6:30",
        "genres_beatport": "Deep House",
        "genres_lastfm": "electronic, deep house",
        "genres_musicbrainz": "house",
        "genres_soundcloud": "deep, chill",
    }
    info = _build_track_info(row)
    assert "Artist - Title (Remix).mp3" in info
    assert "Deep House" in info
    assert "BPM: 124" in info
    assert "Audio tag artist: Some Artist" in info
    assert "Beatport genre: Deep House" in info


def test_build_track_info_empty():
    """Should handle empty row gracefully."""
    info = _build_track_info({})
    assert info == ""


def test_build_track_info_minimal():
    """Should work with just a filename."""
    row = {"file_path": "/tmp/test.wav"}
    info = _build_track_info(row)
    assert "test.wav" in info


# ── Prompt building ──────────────────────────────────────────────────────────


def test_build_classify_prompt_returns_json():
    """Prompt should be valid JSON list of messages."""
    row = {
        "file_path": "~/Music Unsorted/Artist - Track (Edit).mp3",
        "bpm": "126",
        "tag_artist_original": "Artist",
        "tag_title_original": "Track",
    }
    labels = ["Afro House", "Deep House", "Tech House"]
    prompt = build_classify_prompt(row, labels)
    messages = json.loads(prompt)
    assert isinstance(messages, list)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    # System prompt should contain genre list
    assert "Afro House" in messages[0]["content"]
    assert "Tech House" in messages[0]["content"]
    # System prompt should contain naming rules
    assert "NAMING RULES" in messages[0]["content"]
    assert "version" in messages[0]["content"].lower()


def test_build_classify_prompt_includes_bpm_guide():
    """Prompt should contain BPM range guidance."""
    row = {"file_path": "/tmp/test.mp3", "bpm": "140"}
    labels = ["Techno", "House"]
    prompt = build_classify_prompt(row, labels)
    messages = json.loads(prompt)
    assert "BPM ranges" in messages[0]["content"]
    assert "128-140" in messages[0]["content"]


def test_build_classify_prompt_includes_remix_rule():
    """Prompt should contain the critical remix classification rule."""
    row = {"file_path": "/tmp/test.mp3"}
    labels = ["House"]
    prompt = build_classify_prompt(row, labels)
    messages = json.loads(prompt)
    assert "REMIX RULE" in messages[0]["content"]


def test_build_classify_prompt_exclude_file_genre_tag():
    """When exclude_file_genre_tag=True, prompt omits tag_genre_original but keeps external sources."""
    row = {
        "file_path": "~/Music Library/Artist/Track.mp3",
        "tag_artist_original": "Some Artist",
        "tag_title_original": "Some Track",
        "tag_genre_original": "Afro House",
        "genres_beatport": "Afro House",
        "genres_lastfm": "afro house, house",
        "bpm": "124",
    }
    labels = ["Afro House", "Tech House", "House"]

    # Without exclusion — all genre tags present
    prompt_with = build_classify_prompt(row, labels, exclude_file_genre_tag=False)
    msgs_with = json.loads(prompt_with)
    user_with = msgs_with[1]["content"]
    assert "Audio tag genre" in user_with  # tag_genre_original visible
    assert "Beatport genre" in user_with

    # With exclusion — only file genre tag removed, external sources kept
    prompt_without = build_classify_prompt(row, labels, exclude_file_genre_tag=True)
    msgs_without = json.loads(prompt_without)
    user_without = msgs_without[1]["content"]
    system_without = msgs_without[0]["content"]
    assert "Audio tag genre" not in user_without  # tag_genre_original stripped
    # External sources are KEPT (per-track data from online DBs)
    assert "Beatport genre" in user_without
    assert "Last.fm genres" in user_without
    # Artist/title/BPM still present
    assert "Some Artist" in user_without
    assert "Some Track" in user_without
    assert "124" in user_without
    # System prompt should note that file tag was excluded
    assert "embedded genre tag has been excluded" in system_without


# ── Result validation ────────────────────────────────────────────────────────


def test_validate_result_correct_genre():
    """Valid genre passes through unchanged."""
    labels = ["Tech House", "Deep House", "Afro House"]
    result = {
        "artist": "Test",
        "title": "Track",
        "version": ["Remix"],
        "genre": "Tech House",
        "confidence": 0.9,
        "reasoning": "test",
    }
    validated = _validate_result(result, labels)
    assert validated["genre"] == "Tech House"
    assert "genre_warning" not in validated


def test_validate_result_case_insensitive_genre():
    """Genre matching should be case-insensitive."""
    labels = ["Tech House", "Deep House"]
    result = {"genre": "tech house", "version": [], "confidence": 0.8}
    validated = _validate_result(result, labels)
    assert validated["genre"] == "Tech House"
    assert "genre_warning" not in validated


def test_validate_result_unknown_genre():
    """Unknown genre should trigger warning."""
    labels = ["Tech House", "Deep House"]
    result = {"genre": "Gabber", "version": [], "confidence": 0.8}
    validated = _validate_result(result, labels)
    assert "genre_warning" in validated
    assert "Gabber" in validated["genre_warning"]


def test_validate_result_string_version():
    """Version as string should be converted to list."""
    labels = ["House"]
    result = {"genre": "House", "version": "Remix, Clean Intro", "confidence": 0.8}
    validated = _validate_result(result, labels)
    assert isinstance(validated["version"], list)
    assert validated["version"] == ["Remix", "Clean Intro"]


def test_validate_result_empty_string_version():
    """Empty string version should become empty list."""
    labels = ["House"]
    result = {"genre": "House", "version": "", "confidence": 0.8}
    validated = _validate_result(result, labels)
    assert validated["version"] == []


def test_validate_result_confidence_string():
    """Confidence as string should be converted to float."""
    labels = ["House"]
    result = {"genre": "House", "version": [], "confidence": "0.85"}
    validated = _validate_result(result, labels)
    assert validated["confidence"] == 0.85


def test_validate_result_invalid_confidence():
    """Invalid confidence should default to 0.0."""
    labels = ["House"]
    result = {"genre": "House", "version": [], "confidence": "high"}
    validated = _validate_result(result, labels)
    assert validated["confidence"] == 0.0


# ── Version formatting ───────────────────────────────────────────────────────


def test_format_version_for_filename_multiple():
    assert format_version_for_filename(["Extended Mix", "Clean"]) == "(Extended Mix) (Clean)"


def test_format_version_for_filename_single():
    assert format_version_for_filename(["Remix"]) == "(Remix)"


def test_format_version_for_filename_empty():
    assert format_version_for_filename([]) == ""


def test_format_version_for_csv_multiple():
    assert format_version_for_csv(["Hard Edit", "Clean Intro"]) == "Hard Edit, Clean Intro"


def test_format_version_for_csv_single():
    assert format_version_for_csv(["Original Mix"]) == "Original Mix"


def test_format_version_for_csv_empty():
    assert format_version_for_csv([]) == ""


# ── classify_track with mocked API ──────────────────────────────────────────


def test_classify_track_success():
    """Full classify_track call with mocked OpenAI response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "artist": "Diplo x Damian Marley",
                    "title": "Welcome to the Party",
                    "version": ["Vintage Culture Remix"],
                    "genre": "Tech House",
                    "confidence": 0.88,
                    "reasoning": "124 BPM, driving bassline",
                })
            }
        }],
        "usage": {"prompt_tokens": 500, "completion_tokens": 80},
    }

    row = {
        "file_path": "~/Music Unsorted/Diplo - Welcome to the Party (Vintage Culture Remix).mp3",
        "tag_artist_original": "Diplo",
        "tag_title_original": "Welcome to the Party",
        "bpm": "124",
        "key_camelot": "7A",
    }

    with patch("djlib.ai_classify.http_requests.post", return_value=mock_response):
        result = classify_track(
            row,
            genre_labels=["Tech House", "House", "Deep House"],
            api_key="sk-test",
        )

    assert result["artist"] == "Diplo x Damian Marley"
    assert result["title"] == "Welcome to the Party"
    assert result["version"] == ["Vintage Culture Remix"]
    assert result["genre"] == "Tech House"
    assert result["confidence"] == 0.88
    assert "_usage" in result
    assert result["_usage"]["input_tokens"] == 500


def test_classify_track_no_api_key():
    """Should raise ValueError when no API key."""
    with pytest.raises(ValueError, match="No OpenAI API key"):
        classify_track({"file_path": "/tmp/test.mp3"}, api_key="")


def test_classify_track_markdown_fences():
    """Should handle response wrapped in markdown code fences."""
    content = '```json\n{"artist": "Test", "title": "Track", "version": [], "genre": "House", "confidence": 0.9, "reasoning": "test"}\n```'
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }

    with patch("djlib.ai_classify.http_requests.post", return_value=mock_response):
        result = classify_track(
            {"file_path": "/tmp/test.mp3"},
            genre_labels=["House"],
            api_key="sk-test",
        )
    assert result["artist"] == "Test"
    assert result["genre"] == "House"


# ── API endpoint tests ───────────────────────────────────────────────────────


@pytest.fixture
def client():
    """Flask test client."""
    from djlib.review.server import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


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


def test_api_ai_classify_no_api_key(client):
    """Returns 501 when no API key configured."""
    with patch("djlib.review.server.get_openai_api_key", return_value=""):
        resp = client.post("/api/ai-classify", json={"track_id": "test-123"})
        assert resp.status_code == 501


def test_api_ai_classify_no_body(client):
    """Returns 400 when no JSON body."""
    with patch("djlib.review.server.get_openai_api_key", return_value="sk-test"):
        resp = client.post("/api/ai-classify")
        assert resp.status_code in (400, 415)


def test_api_ai_classify_missing_track_id(client):
    """Returns 400 when track_id missing."""
    with patch("djlib.review.server.get_openai_api_key", return_value="sk-test"):
        resp = client.post("/api/ai-classify", json={})
        assert resp.status_code == 400


def test_api_ai_classify_not_found(client):
    """Returns 404 when track not in CSV."""
    with patch("djlib.review.server.get_openai_api_key", return_value="sk-test"):
        resp = client.post("/api/ai-classify", json={"track_id": "nonexistent"})
        assert resp.status_code == 404


def test_api_ai_classify_success(client, tmp_path):
    """Successful classification through API endpoint."""
    from djlib.review import server as srv

    csv_path = _make_unsorted_csv(tmp_path, [{
        "track_id": "classify-test-1",
        "file_path": "/tmp/Music Unsorted/Deep House/Artist - Track (Edit).mp3",
        "artist": "",
        "title": "Track",
        "version_info": "",
        "tag_artist_original": "Artist",
        "tag_title_original": "Track Edit",
        "tag_genre_original": "",
        "artist_suggest": "Artist",
        "title_suggest": "Track",
        "version_suggest": "Edit",
        "bpm": "122",
        "key_camelot": "8A",
        "duration_suggest": "5:30",
        "genres_soundcloud": "",
        "genres_beatport": "",
        "genres_musicbrainz": "",
        "genres_lastfm": "",
    }])

    mock_classify_result = {
        "artist": "The Artist",
        "title": "The Track",
        "version": ["Deep Edit"],
        "genre": "Deep House",
        "confidence": 0.91,
        "reasoning": "122 BPM, smooth bassline",
        "_usage": {"input_tokens": 450, "output_tokens": 70, "model": "gpt-4o-mini"},
    }

    with patch.object(srv, "UNSORTED_CSV", csv_path), \
         patch("djlib.review.server.get_openai_api_key", return_value="sk-test"), \
         patch("djlib.ai_classify.classify_track", return_value=mock_classify_result) as mock_ct:
        resp = client.post("/api/ai-classify", json={"track_id": "classify-test-1"})
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["artist"] == "The Artist"
        assert data["title"] == "The Track"
        assert data["version"] == ["Deep Edit"]
        assert data["genre"] == "Deep House"
        assert data["confidence"] == 0.91
        # _usage should be stripped from response
        assert "_usage" not in data

    # Clean up cache
    srv._classify_cache.pop("classify-test-1", None)


def test_api_ai_classify_uses_cache(client):
    """Second request for same track returns cached result."""
    import djlib.review.server as srv

    srv._classify_cache["cached-classify"] = {
        "artist": "Cached",
        "title": "Track",
        "version": ["Remix"],
        "genre": "Tech House",
        "confidence": 0.95,
        "reasoning": "cached",
    }

    with patch("djlib.review.server.get_openai_api_key", return_value="sk-test"):
        resp = client.post("/api/ai-classify", json={"track_id": "cached-classify"})
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["artist"] == "Cached"
        assert data["genre"] == "Tech House"

    del srv._classify_cache["cached-classify"]
