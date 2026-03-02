"""Tests for enrich.py helpers and refactored functions.

Covers:
  - _is_live
  - _is_compilation_album
  - _parse_duration_tag
  - _acoustid_artist_matches
  - _offline_fallback
  - _normalize_title_from_canonical
  - _sanitize_artist  (flattened)
  - _sanitize_title   (flattened)
  - _strip_artist_prefix (flattened)
  - suggest_metadata (offline-only path)

Run with: pytest tests/test_enrich_helpers.py -v
"""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch

from djlib.enrich import (
    _is_live,
    _is_compilation_album,
    _parse_duration_tag,
    _acoustid_artist_matches,
    _offline_fallback,
    _normalize_title_from_canonical,
    _sanitize_artist,
    _sanitize_title,
    _strip_artist_prefix,
    _strip_version_suffix,
    suggest_metadata,
    MIN_GENRE_CONFIDENCE,
    ARCHIVE_TOLERANCE_S,
    _LIVE_KEYWORDS,
    _COMPILATION_KEYWORDS,
)


# ---------------------------------------------------------------------------
# _is_live
# ---------------------------------------------------------------------------

class TestIsLive:
    def test_live_version(self):
        assert _is_live("Live", "") is True

    def test_live_version_case_insensitive(self):
        assert _is_live("LIVE", "") is True

    def test_concert_version(self):
        assert _is_live("Concert", "") is True

    def test_unplugged_version(self):
        assert _is_live("Unplugged", "") is True

    def test_ao_vivo_version(self):
        assert _is_live("Ao Vivo", "") is True

    def test_in_concert_version(self):
        assert _is_live("In Concert", "") is True

    def test_live_album(self):
        assert _is_live("", "Live at Madison Square Garden") is True

    def test_concert_album(self):
        assert _is_live("", "Unplugged in New York") is True

    def test_not_live(self):
        assert _is_live("", "") is False

    def test_remix_not_live(self):
        assert _is_live("Club Mix", "") is False

    def test_partial_match_in_album(self):
        assert _is_live("", "alive and kicking") is False  # "live" is substring of "alive"

    def test_both_version_and_album(self):
        assert _is_live("Live", "Greatest Hits") is True


# ---------------------------------------------------------------------------
# _is_compilation_album
# ---------------------------------------------------------------------------

class TestIsCompilationAlbum:
    def test_greatest_hits(self):
        assert _is_compilation_album("Greatest Hits") is True

    def test_best_of(self):
        assert _is_compilation_album("Best of Depeche Mode") is True

    def test_the_best(self):
        assert _is_compilation_album("The Best 1980-1990") is True

    def test_anthology(self):
        assert _is_compilation_album("Anthology") is True

    def test_essential(self):
        assert _is_compilation_album("Essential Mix 20") is True

    def test_regular_album(self):
        assert _is_compilation_album("Nevermind") is False

    def test_empty(self):
        assert _is_compilation_album("") is False

    def test_none(self):
        assert _is_compilation_album(None) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _parse_duration_tag
# ---------------------------------------------------------------------------

class TestParseDurationTag:
    def test_normal(self):
        assert _parse_duration_tag("3:45") == 225

    def test_zero(self):
        assert _parse_duration_tag("0:00") == 0

    def test_long(self):
        assert _parse_duration_tag("12:30") == 750

    def test_empty(self):
        assert _parse_duration_tag("") == 0

    def test_no_colon(self):
        assert _parse_duration_tag("345") == 0

    def test_garbage(self):
        assert _parse_duration_tag("not:a:number") == 0

    def test_one_digit_seconds(self):
        assert _parse_duration_tag("5:5") == 305


# ---------------------------------------------------------------------------
# _acoustid_artist_matches
# ---------------------------------------------------------------------------

class TestAcoustidArtistMatches:
    def test_match(self):
        online = {"artist_suggest": "Daft Punk"}
        assert _acoustid_artist_matches(online, "Daft Punk") is True

    def test_match_partial(self):
        online = {"artist_suggest": "Daft Punk feat. Pharrell Williams"}
        assert _acoustid_artist_matches(online, "Daft Punk") is True

    def test_mismatch(self):
        online = {"artist_suggest": "Radiohead"}
        assert _acoustid_artist_matches(online, "Daft Punk") is False

    def test_empty_online(self):
        online = {"artist_suggest": ""}
        assert _acoustid_artist_matches(online, "Daft Punk") is True

    def test_empty_tags(self):
        online = {"artist_suggest": "Radiohead"}
        assert _acoustid_artist_matches(online, "") is True

    def test_short_words_ignored(self):
        # Words < 3 chars are ignored in similarity
        online = {"artist_suggest": "DJ Snake"}
        assert _acoustid_artist_matches(online, "DJ Shadow") is False

    def test_case_insensitive(self):
        online = {"artist_suggest": "DEPECHE MODE"}
        assert _acoustid_artist_matches(online, "depeche mode") is True


# ---------------------------------------------------------------------------
# _offline_fallback
# ---------------------------------------------------------------------------

class TestOfflineFallback:
    def test_basic(self):
        result = _offline_fallback("Artist", "Title", "Remix", {"genre": "House"})
        assert result["artist_suggest"] == "Artist"
        assert result["title_suggest"] == "Title"
        assert result["version_suggest"] == "Remix"
        assert result["genre_suggest"] == "House"
        assert result["meta_source"] == "filename|tags_fallback"

    def test_empty_genre(self):
        result = _offline_fallback("A", "T", "", {})
        assert result["genre_suggest"] == ""

    def test_genre_whitespace(self):
        result = _offline_fallback("A", "T", "", {"genre": "  Rock  "})
        assert result["genre_suggest"] == "Rock"


# ---------------------------------------------------------------------------
# _normalize_title_from_canonical
# ---------------------------------------------------------------------------

class TestNormalizeTitleFromCanonical:
    def test_no_change_identical(self):
        title, norm = _normalize_title_from_canonical("Billie Jean", "Billie Jean")
        assert norm is False

    def test_prefix_extended(self):
        title, norm = _normalize_title_from_canonical("Lady", "Lady (Hear Me Tonight)")
        assert norm is True
        assert "Hear Me Tonight" in title

    def test_no_normalize_live(self):
        title, norm = _normalize_title_from_canonical("Billie Jean", "Billie Jean (Live)")
        assert norm is False

    def test_empty_local(self):
        title, norm = _normalize_title_from_canonical("", "Something")
        assert norm is False


# ---------------------------------------------------------------------------
# Flattened sanitization helpers
# ---------------------------------------------------------------------------

class TestSanitizeArtist:
    def test_special_artist(self):
        assert _sanitize_artist("acdc") == "AC/DC"

    def test_acronym_preserved(self):
        assert _sanitize_artist("ABBA") == "ABBA"

    def test_title_case_applied(self):
        assert _sanitize_artist("depeche mode") == "Depeche Mode"

    def test_track_number_stripped(self):
        result = _sanitize_artist("09. One Direction")
        assert not result.startswith("09")
        assert "One Direction" in result

    def test_empty(self):
        assert _sanitize_artist("") == ""


class TestSanitizeTitle:
    def test_basic(self):
        assert _sanitize_title("some nice song") == "Some Nice Song"

    def test_url_stripped(self):
        result = _sanitize_title("Song www.example.com")
        assert "www" not in result

    def test_extension_stripped(self):
        result = _sanitize_title("Song.mp3")
        assert ".mp3" not in result

    def test_empty(self):
        assert _sanitize_title("") == ""


class TestStripArtistPrefix:
    def test_strip(self):
        assert _strip_artist_prefix("Daft Punk - Around The World", "Daft Punk") == "Around The World"

    def test_no_strip_different(self):
        result = _strip_artist_prefix("Around The World", "Daft Punk")
        assert result == "Around The World"

    def test_empty_artist(self):
        assert _strip_artist_prefix("Title", "") == "Title"

    def test_empty_title(self):
        assert _strip_artist_prefix("", "Artist") == ""


class TestStripVersionSuffix:
    def test_strip(self):
        assert _strip_version_suffix("Song Club Mix", "Club Mix") == "Song"

    def test_no_match(self):
        assert _strip_version_suffix("Song", "Club Mix") == "Song"

    def test_empty(self):
        assert _strip_version_suffix("", "") == ""


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------

class TestConstants:
    def test_min_genre_confidence(self):
        assert 0 < MIN_GENRE_CONFIDENCE < 1

    def test_archive_tolerance(self):
        assert ARCHIVE_TOLERANCE_S > 0

    def test_live_keywords_tuple(self):
        assert isinstance(_LIVE_KEYWORDS, tuple)
        assert "live" in _LIVE_KEYWORDS

    def test_compilation_keywords_frozenset(self):
        assert isinstance(_COMPILATION_KEYWORDS, frozenset)
        assert "greatest hits" in _COMPILATION_KEYWORDS


# ---------------------------------------------------------------------------
# suggest_metadata — offline path only (no network)
# ---------------------------------------------------------------------------

class TestSuggestMetadataOffline:
    """Test suggest_metadata with enable_online=False (no network calls)."""

    def test_basic_tags(self, tmp_path: Path):
        p = tmp_path / "test.mp3"
        p.touch()
        tags = {"artist": "Artist", "title": "Title", "genre": "House"}
        result = suggest_metadata(p, tags, enable_online=False)
        assert result["artist_suggest"]
        assert result["title_suggest"]
        assert result["meta_source"] == "filename|tags_fallback"

    def test_genre_passthrough(self, tmp_path: Path):
        p = tmp_path / "test.mp3"
        p.touch()
        tags = {"artist": "A", "title": "T", "genre": "Techno"}
        result = suggest_metadata(p, tags, enable_online=False)
        assert result["genre_suggest"] == "Techno"

    def test_empty_tags_uses_filename(self, tmp_path: Path):
        p = tmp_path / "Artist - Title.mp3"
        p.touch()
        tags: dict = {}
        result = suggest_metadata(p, tags, enable_online=False)
        # Should derive from filename
        assert result["artist_suggest"] or result["title_suggest"]


# ---------------------------------------------------------------------------
# _resolve_via_genre_sources: Beatport artist extraction
# ---------------------------------------------------------------------------

class TestResolveViaGenreSourcesBeatportArtist:
    """When artist is empty and Beatport finds a remix, extract artist."""

    @patch("djlib.metadata.beatport.search_track")
    @patch("djlib.metadata.lastfm.track_info", return_value={})
    def test_beatport_provides_artist_for_remix_without_artist(
        self, mock_lastfm_info, mock_bp_search
    ):
        """Remix with no artist: Beatport result should populate artist_suggest."""
        from djlib.enrich import _resolve_via_genre_sources

        # Mock Beatport search_track to return a result with artist
        mock_bp_search.return_value = {
            "artist": "Pablo Fierro",
            "genre": "Afro House",
            "release_date": "2021-06-15",
            "release_name": "El Carinoso EP",
        }

        # Mock the genre resolver to return a valid result
        from djlib.metadata.genre_resolver import GenreResolution, SourceScore
        mock_genre_result = GenreResolution(
            main="Afro House",
            subs=[],
            confidence=0.8,
            breakdown=[SourceScore(source="bp", weight=25.0, tags={"Afro House": 25.0})],
        )

        with patch(
            "djlib.metadata.genre_resolver.resolve", return_value=mock_genre_result
        ):
            result = _resolve_via_genre_sources(
                artist="",
                title="El Carinoso",
                version="Gregor Salto Remix",
                dur_sec=468,
                live=False,
                tags={},
            )

        assert result is not None
        assert result["artist_suggest"] == "Pablo Fierro"
        assert result["year_suggest"] == "2021"

    @patch("djlib.metadata.beatport.search_track")
    @patch("djlib.metadata.lastfm.track_info", return_value={})
    def test_beatport_does_not_overwrite_existing_artist(
        self, mock_lastfm_info, mock_bp_search
    ):
        """When artist is already known, Beatport should not overwrite it."""
        from djlib.enrich import _resolve_via_genre_sources

        mock_bp_search.return_value = {
            "artist": "Pablo Fierro",
            "genre": "Afro House",
            "release_date": "2021-06-15",
        }

        from djlib.metadata.genre_resolver import GenreResolution, SourceScore
        mock_genre_result = GenreResolution(
            main="Afro House",
            subs=[],
            confidence=0.8,
            breakdown=[SourceScore(source="bp", weight=25.0, tags={"Afro House": 25.0})],
        )

        with patch(
            "djlib.metadata.genre_resolver.resolve", return_value=mock_genre_result
        ):
            result = _resolve_via_genre_sources(
                artist="Existing Artist",
                title="El Carinoso",
                version="Gregor Salto Remix",
                dur_sec=468,
                live=False,
                tags={},
            )

        assert result is not None
        # Should keep the existing artist, not overwrite with Beatport
        assert result["artist_suggest"] == "Existing Artist"
