"""Tests for djlib.metadata.beatport search_track logic.

Focus: no-artist remix searches (version provides specificity).
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock
from djlib.metadata.beatport import search_track, _search_cache


def _make_track(
    name: str,
    artist_name: str,
    mix_name: str = "Original Mix",
    genre: str = "Afro House",
    length_ms: int = 420_000,
    bpm: int = 125,
):
    """Build a minimal Beatport API track dict."""
    return {
        "name": name,
        "artists": [{"name": artist_name}],
        "mix_name": mix_name,
        "genre": {"name": genre},
        "sub_genre": None,
        "release": {
            "name": f"Release of {name}",
            "image": {},
            "label": {"name": "Test Label"},
        },
        "new_release_date": "2021-06-15",
        "length_ms": length_ms,
        "bpm": bpm,
        "key": {"camelot_number": 10, "camelot_letter": "A", "name": "C minor"},
    }


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear Beatport in-process cache between tests."""
    _search_cache.clear()
    yield
    _search_cache.clear()


def _mock_bp_api(tracks):
    """Return (mock_get_valid_token, mock_requests_get) for patching."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"tracks": tracks}
    mock_response.from_cache = True  # Skip rate limiting

    mock_get = MagicMock(return_value=mock_response)
    mock_token = MagicMock(return_value="fake_token")
    return mock_token, mock_get


class TestNoArtistRemixSearch:
    """Beatport should find remixes even without artist when version is present."""

    @patch("djlib.metadata.beatport.requests.get")
    @patch("djlib.metadata.beatport.get_valid_token")
    def test_remix_found_without_artist(self, mock_token, mock_get):
        """El Carinoso (Gregor Salto Remix) — no artist tag, Beatport has it."""
        tracks = [
            _make_track(
                name="El Carinoso",
                artist_name="Pablo Fierro",
                mix_name="Gregor Salto Remix",
                genre="Afro House",
                length_ms=468_000,
                bpm=125,
            )
        ]
        mock_token.return_value = "fake_token"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"tracks": tracks}
        mock_resp.from_cache = True
        mock_get.return_value = mock_resp

        result = search_track(
            artist="", title="El Carinoso", version="Gregor Salto Remix"
        )

        assert result is not None
        assert result["artist"] == "Pablo Fierro"
        assert result["genre"] == "Afro House"
        assert result["mix_name"] == "Gregor Salto Remix"

    @patch("djlib.metadata.beatport.requests.get")
    @patch("djlib.metadata.beatport.get_valid_token")
    def test_no_artist_no_version_returns_none(self, mock_token, mock_get):
        """Title-only query without version should still be rejected."""
        result = search_track(artist="", title="El Carinoso")
        assert result is None
        mock_get.assert_not_called()

    @patch("djlib.metadata.beatport.requests.get")
    @patch("djlib.metadata.beatport.get_valid_token")
    def test_no_artist_remix_wrong_version_rejected(self, mock_token, mock_get):
        """If Beatport only has original, remix search (no artist) should reject."""
        tracks = [
            _make_track(
                name="El Carinoso",
                artist_name="Pablo Fierro",
                mix_name="Original Mix",
                genre="Afro House",
            )
        ]
        mock_token.return_value = "fake_token"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"tracks": tracks}
        mock_resp.from_cache = True
        mock_get.return_value = mock_resp

        result = search_track(
            artist="", title="El Carinoso", version="Gregor Salto Remix"
        )

        # Version mismatch → rejected (best_version_score == 0)
        assert result is None

    @patch("djlib.metadata.beatport.requests.get")
    @patch("djlib.metadata.beatport.get_valid_token")
    def test_no_artist_remix_title_mismatch_rejected(self, mock_token, mock_get):
        """If Beatport returns a different title, should still be rejected."""
        tracks = [
            _make_track(
                name="Completely Different Song",
                artist_name="Some Artist",
                mix_name="Gregor Salto Remix",
            )
        ]
        mock_token.return_value = "fake_token"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"tracks": tracks}
        mock_resp.from_cache = True
        mock_get.return_value = mock_resp

        result = search_track(
            artist="", title="El Carinoso", version="Gregor Salto Remix"
        )

        # Title "el carinoso" words don't match "completely different song"
        assert result is None

    @patch("djlib.metadata.beatport.requests.get")
    @patch("djlib.metadata.beatport.get_valid_token")
    def test_with_artist_still_works(self, mock_token, mock_get):
        """Existing artist-based search should still work as before."""
        tracks = [
            _make_track(
                name="El Carinoso",
                artist_name="Pablo Fierro",
                mix_name="Gregor Salto Remix",
                genre="Afro House",
            )
        ]
        mock_token.return_value = "fake_token"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"tracks": tracks}
        mock_resp.from_cache = True
        mock_get.return_value = mock_resp

        result = search_track(
            artist="Pablo Fierro", title="El Carinoso", version="Gregor Salto Remix"
        )

        assert result is not None
        assert result["artist"] == "Pablo Fierro"

    @patch("djlib.metadata.beatport.requests.get")
    @patch("djlib.metadata.beatport.get_valid_token")
    def test_no_artist_no_title_returns_none(self, mock_token, mock_get):
        """Both artist and title empty should return None."""
        result = search_track(artist="", title="")
        assert result is None
        mock_get.assert_not_called()
