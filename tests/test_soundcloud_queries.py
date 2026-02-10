"""Tests for SoundCloud query generation (_candidate_queries).

Covers: Strategy 1 (full version), vs/&/and splitting, word cap,
genre stripping, mashup handling, dedup, originals.
"""
import pytest

from djlib.metadata.soundcloud import _candidate_queries, _clean_for_query, _light_clean


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
