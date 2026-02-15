"""Tests for SoundCloud query generation (_candidate_queries).

Covers: Strategy 1 (full version), vs/&/and/x splitting, word cap,
genre stripping, mashup handling, dedup, originals,
_keep_token filtering of artist/title fragments.
"""
import pytest

from djlib.metadata.soundcloud import (
    _candidate_queries,
    _clean_for_query,
    _light_clean,
    get_soundcloud_genres,
)


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestLightClean:
    """Verify _light_clean preserves separators that _clean_for_query strips."""

    def test_preserves_ampersand(self):
        assert "&" in _light_clean("A & B")

    def test_preserves_vs(self):
        assert "vs" in _light_clean("A vs B")

    def test_strips_brackets(self):
        assert "[" not in _light_clean("Track [9A 128]")

    def test_strips_parens(self):
        assert "(" not in _light_clean("Track (Radio Edit)")

    def test_normalizes_whitespace(self):
        assert _light_clean("  A   B  ") == "A B"


class TestCleanForQueryContrast:
    """Confirm _clean_for_query strips & (which _light_clean preserves)."""

    def test_strips_ampersand(self):
        assert "&" not in _clean_for_query("A & B")


# ---------------------------------------------------------------------------
# _candidate_queries tests
# ---------------------------------------------------------------------------

class TestCandidateQueries:
    """Unit tests for _candidate_queries."""

    # -- Strategy 1: full query first --

    def test_full_version_is_first_query(self):
        qs = _candidate_queries(
            "Bastille", "Pompeii",
            "Merchant vs Vidojean & Oliver Loenn City Boys Edit",
        )
        assert len(qs) > 0
        # Strategy 1 should contain all three parts
        assert "Bastille" in qs[0]
        assert "Pompeii" in qs[0]
        assert "Merchant" in qs[0]

    def test_full_version_preserves_vs_and_ampersand(self):
        qs = _candidate_queries("A", "B", "X vs Y & Z Edit")
        # Strategy 1 should preserve both separators
        assert "vs" in qs[0]
        assert "&" in qs[0]

    def test_full_version_capped_at_max_words(self):
        long_version = " ".join(f"Word{i}" for i in range(15)) + " Remix"
        qs = _candidate_queries("Artist", "Title", long_version)
        assert len(qs[0].split()) <= 10

    # -- Strategy 2: vs / & / and splitting --

    def test_vs_splits_first_remixer(self):
        qs = _candidate_queries(
            "Bastille", "Pompeii",
            "Merchant vs Vidojean & Oliver Loenn City Boys Edit",
        )
        assert "Merchant Pompeii" in qs

    def test_vs_dot_splits_first_remixer(self):
        qs = _candidate_queries("X", "Track", "A vs. B Remix")
        assert "A Track" in qs

    def test_ampersand_splits_first_remixer(self):
        qs = _candidate_queries("Akon", "Right Now", "Okan Evci & Emre Yuksel Remix")
        assert any("Okan Evci" in q and "Right Now" in q for q in qs)

    def test_and_splits_first_remixer(self):
        qs = _candidate_queries("X", "Track", "Alpha and Beta Remix")
        assert "Alpha Track" in qs

    def test_x_uppercase_splits_first_remixer(self):
        """'X' as multi-artist separator (common in mashup credits)."""
        qs = _candidate_queries(
            "Alesso X Depeche Mode",
            "Enjoy The Silence X If I Lose Myself",
            "Vidojean X Oliver Loenn Mashup",
        )
        # first_remixer = "Vidojean" (split on X)
        assert any("Vidojean" in q and "Silence" in q for q in qs)
        # Strategy 2 should NOT contain "Oliver Loenn"
        strat2_candidates = [q for q in qs if q.startswith("Vidojean") and "Oliver" not in q]
        assert len(strat2_candidates) >= 1

    def test_x_lowercase_splits_first_remixer(self):
        qs = _candidate_queries("A", "B", "Foo x Bar Remix")
        assert "Foo B" in qs

    # -- Originals (no version) --

    def test_original_track_single_query(self):
        qs = _candidate_queries("Daft Punk", "Around The World", "")
        assert qs == ["Daft Punk Around The World"]

    def test_empty_inputs_returns_empty(self):
        assert _candidate_queries("", "", "") == []

    # -- Dedup and limits --

    def test_no_duplicate_queries(self):
        qs = _candidate_queries("Artist", "Title", "Artist Remix")
        assert len(qs) == len(set(qs))

    def test_max_queries_respected(self):
        qs = _candidate_queries("A", "B", "C vs D & E Remix", max_queries=3)
        assert len(qs) <= 3

    # -- Genre removal from remixer --

    def test_genre_names_stripped_from_remixer(self):
        qs = _candidate_queries("Anyma", "After Love", "Blue Purple Afro House Remix")
        # Strategy 1 keeps the full version including "Afro House"
        assert "Afro House" in qs[0]
        # Strategies 2-5 should NOT contain "Afro House" (stripped by _RE_VERSION_GENRES)
        for q in qs[1:]:
            assert "Afro House" not in q

    # -- Mashup handling --

    def test_mashup_appends_keyword(self):
        qs = _candidate_queries("Drake", "Fake Love", "Albert Delgado Mashup")
        assert any("mashup" in q.lower() for q in qs)

    # -- Single remixer (no split needed) --

    def test_single_remixer_no_split(self):
        qs = _candidate_queries("X", "Track", "Solardo Remix")
        # first_remixer == remixer → Strategy 2 skipped, Strategy 3 present
        assert "Solardo Track" in qs

# ---------------------------------------------------------------------------
# _keep_token filtering (integration via mocked SC API)
# ---------------------------------------------------------------------------

class TestKeepTokenFiltering:
    """Verify _keep_token rejects artist/title fragments from SC tags."""

    @staticmethod
    def _fake_sc_response(genre: str, tag_list: str, title: str = "", artist: str = ""):
        """Build a fake SC API JSON response with one track."""
        return {
            "collection": [
                {
                    "duration": 180_000,  # 3 min
                    "genre": genre,
                    "tag_list": tag_list,
                    "title": title,
                    "user": {"username": artist},
                }
            ]
        }

    def _call_with_mock(self, artist, title, version, genre, tag_list):
        """Call get_soundcloud_genres with a mocked HTTP layer."""
        from unittest.mock import patch, MagicMock

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = self._fake_sc_response(
            genre, tag_list,
            title=f"{artist} - {title} ({version})" if version else f"{artist} - {title}",
            artist=artist,
        )
        fake_resp.from_cache = False

        # Clear LRU cache so each test gets a fresh call
        get_soundcloud_genres.cache_clear()

        with patch("djlib.metadata.soundcloud.get_valid_client_id", return_value="fake"), \
             patch("djlib.metadata.soundcloud.requests.get", return_value=fake_resp):
            return get_soundcloud_genres(artist, title, version)

    def test_multiword_artist_fragment_rejected(self):
        """'depeche mode' should be rejected when artist contains those words."""
        result = self._call_with_mock(
            "Alesso X Depeche Mode",
            "Enjoy The Silence",
            "Vidojean Mashup",
            genre="Afro House",
            tag_list='"depeche mode" "enjoy the silence" "afro house" "vidojean"',
        )
        assert result is not None
        assert "depeche mode" not in result
        assert "enjoy the silence" not in result
        assert "afro house" in result

    def test_single_word_artist_rejected(self):
        """Single-word exact match from artist should be dropped."""
        result = self._call_with_mock(
            "Drake", "Fake Love", "SomeGuy Remix",
            genre="Hip Hop",
            tag_list='"drake" "hip hop" "someguy"',
        )
        assert result is not None
        assert "drake" not in result

    def test_genuine_genre_kept(self):
        """Multi-word genre tags that don't overlap with artist/title survive."""
        result = self._call_with_mock(
            "SomeArtist", "SomeTrack", "SomeGuy Remix",
            genre="Afro House",
            tag_list='"tech house" "deep house" "someguy"',
        )
        assert result is not None
        assert "afro house" in result
        assert "tech house" in result
        assert "deep house" in result