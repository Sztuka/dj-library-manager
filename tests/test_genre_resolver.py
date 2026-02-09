"""Tests for djlib.metadata.genre_resolver — pure functions + mocked integration."""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest

from djlib.metadata.genre_resolver import (
    ALL_SOURCES,
    GenreResolution,
    SourceScore,
    _detect_remix,
    _downweight_factor,
    _is_beatport_electronic,
    _is_noise,
    _score_tag,
    _specificity_boost,
    canonical,
    resolve,
)


# ============================================================================
# canonical()
# ============================================================================

class TestCanonical:
    def test_basic_normalization(self):
        assert canonical("Tech House") == "tech house"
        assert canonical("TECHNO") == "techno"

    def test_alias_edm(self):
        assert canonical("EDM") == "electronic"
        assert canonical("edm") == "electronic"

    def test_alias_d_and_b(self):
        assert canonical("D&B") == "drum and bass"
        assert canonical("d&b") == "drum and bass"
        assert canonical("d n b") == "drum and bass"

    def test_alias_tech_house(self):
        assert canonical("tech-house") == "tech house"
        assert canonical("techno house") == "tech house"

    def test_passthrough(self):
        assert canonical("deep house") == "deep house"
        assert canonical("pop") == "pop"


# ============================================================================
# _is_noise()
# ============================================================================

class TestIsNoise:
    def test_empty_is_noise(self):
        assert _is_noise("") is True
        assert _is_noise("  ") is True

    def test_known_noise(self):
        assert _is_noise("charts") is True
        assert _is_noise("seen live") is True
        assert _is_noise("compilation") is True
        assert _is_noise("summer mix") is True
        assert _is_noise("brasil") is True

    def test_year_is_noise(self):
        assert _is_noise("2024") is True
        assert _is_noise("2019") is True

    def test_short_is_noise(self):
        assert _is_noise("ab") is True
        assert _is_noise("1") is True

    def test_genre_is_not_noise(self):
        assert _is_noise("house") is False
        assert _is_noise("techno") is False
        assert _is_noise("drum and bass") is False
        assert _is_noise("afro house") is False

    def test_wochen_noise(self):
        assert _is_noise("1-4 wochen") is True


# ============================================================================
# _downweight_factor()
# ============================================================================

class TestDownweightFactor:
    def test_folk_downweighted(self):
        assert _downweight_factor("folk") == pytest.approx(0.30)

    def test_indie_folk_downweighted(self):
        assert _downweight_factor("indie folk") == pytest.approx(0.40)

    def test_alternative_downweighted(self):
        assert _downweight_factor("alternative") == pytest.approx(0.60)
        assert _downweight_factor("alternative rock") == pytest.approx(0.60)

    def test_normal_genre_not_downweighted(self):
        assert _downweight_factor("house") == pytest.approx(1.0)
        assert _downweight_factor("pop") == pytest.approx(1.0)
        assert _downweight_factor("rock") == pytest.approx(1.0)
        assert _downweight_factor("electronic") == pytest.approx(1.0)


# ============================================================================
# _specificity_boost()
# ============================================================================

class TestSpecificityBoost:
    def test_subgenre_boosted(self):
        # Afro House has boost 1.8 in genres.yml
        assert _specificity_boost("afro house") > 1.0

    def test_parent_genre_not_boosted(self):
        # HOUSE has boost 1.0 (default)
        assert _specificity_boost("house") == pytest.approx(1.0)

    def test_unknown_genre_default(self):
        assert _specificity_boost("completely unknown genre") == pytest.approx(1.0)


# ============================================================================
# _detect_remix()
# ============================================================================

class TestDetectRemix:
    def test_explicit_remix(self):
        assert _detect_remix("Solardo Remix", "Track", "Artist") is True

    def test_rework(self):
        assert _detect_remix("Rework", "Track", "Artist") is True

    def test_bootleg(self):
        assert _detect_remix("Bootleg", "Track", "Artist") is True

    def test_mashup(self):
        assert _detect_remix("Mashup", "Track", "Artist") is True

    def test_producer_edit(self):
        # "City Boys Edit" is a producer edit → treated as remix
        assert _detect_remix("City Boys Edit", "Track", "Artist") is True

    def test_radio_edit_not_remix(self):
        assert _detect_remix("Radio Edit", "Track", "Artist") is False

    def test_extended_edit_not_remix(self):
        assert _detect_remix("Extended Edit", "Track", "Artist") is False

    def test_club_edit_not_remix(self):
        assert _detect_remix("Club Edit", "Track", "Artist") is False

    def test_no_version_no_remix(self):
        assert _detect_remix("", "Track", "Artist") is False

    def test_remastered_not_remix(self):
        assert _detect_remix("Remastered", "Track", "Artist") is False

    def test_version_1_not_remix(self):
        assert _detect_remix("Version 1", "Track", "Artist") is False

    def test_fallback_title_remix(self):
        # No version, but title contains "remix"
        assert _detect_remix("", "Track (Solardo Remix)", "Artist") is True

    def test_fallback_artist_remix(self):
        assert _detect_remix("", "Track", "Artist Remix") is True

    def test_fallback_no_match(self):
        assert _detect_remix("", "Simple Track", "Simple Artist") is False


# ============================================================================
# _is_beatport_electronic()
# ============================================================================

class TestIsBeatportElectronic:
    def test_direct_match(self):
        assert _is_beatport_electronic("house") is True
        assert _is_beatport_electronic("techno") is True
        assert _is_beatport_electronic("tech house") is True

    def test_compound_genre(self):
        # "Techno (Peak Time / Driving)" should match via word boundary
        assert _is_beatport_electronic("Techno (Peak Time / Driving)") is True

    def test_non_electronic(self):
        assert _is_beatport_electronic("pop") is False
        assert _is_beatport_electronic("rock") is False
        assert _is_beatport_electronic("country") is False

    def test_no_false_positive_warehouse(self):
        assert _is_beatport_electronic("warehouse") is False


# ============================================================================
# _score_tag()
# ============================================================================

class TestScoreTag:
    def test_basic_scoring(self):
        scores: Dict[str, float] = {}
        local: Dict[str, float] = {}
        _score_tag("house", 10.0, scores, local)
        assert "house" in scores
        assert scores["house"] > 0
        assert local["house"] == scores["house"]

    def test_noise_filtered(self):
        scores: Dict[str, float] = {}
        local: Dict[str, float] = {}
        _score_tag("charts", 10.0, scores, local)
        assert len(scores) == 0

    def test_count_weighted(self):
        scores1: Dict[str, float] = {}
        local1: Dict[str, float] = {}
        _score_tag("house", 10.0, scores1, local1, count=0)

        scores2: Dict[str, float] = {}
        local2: Dict[str, float] = {}
        _score_tag("house", 10.0, scores2, local2, count=100)

        # count=100 should produce higher score than count=0
        assert scores2["house"] > scores1["house"]

    def test_accumulates(self):
        scores: Dict[str, float] = {}
        local: Dict[str, float] = {}
        _score_tag("house", 10.0, scores, local)
        _score_tag("house", 5.0, scores, local)
        assert scores["house"] == pytest.approx(15.0)


# ============================================================================
# SourceScore dataclass
# ============================================================================

class TestSourceScore:
    def test_fields(self):
        ss = SourceScore(source="beatport", weight=10.0, tags={"house": 25.0})
        assert ss.source == "beatport"
        assert ss.weight == 10.0
        assert ss.tags == {"house": 25.0}


# ============================================================================
# GenreResolution dataclass
# ============================================================================

class TestGenreResolution:
    def test_fields(self):
        gr = GenreResolution(main="house", subs=["tech house"], confidence=0.8)
        assert gr.main == "house"
        assert gr.subs == ["tech house"]
        assert gr.confidence == 0.8
        assert gr.breakdown == []  # default_factory

    def test_with_breakdown(self):
        ss = SourceScore("beatport", 10.0, {"house": 25.0})
        gr = GenreResolution(main="house", subs=[], confidence=0.9, breakdown=[ss])
        assert len(gr.breakdown) == 1
        assert gr.breakdown[0].source == "beatport"


# ============================================================================
# resolve() — integration tests with mocked APIs
# ============================================================================

_BP_PREFIX = "djlib.metadata.genre_resolver._fetch_beatport"
_LFM_PREFIX = "djlib.metadata.genre_resolver._fetch_lastfm"
_MB_PREFIX = "djlib.metadata.genre_resolver._fetch_musicbrainz"
_SC_PREFIX = "djlib.metadata.genre_resolver._fetch_soundcloud"


class TestResolveIntegration:
    """Integration tests for resolve() with mocked external fetchers."""

    def test_empty_input_returns_none(self):
        assert resolve("", "") is None
        assert resolve("  ", "  ") is None

    @patch(_SC_PREFIX, return_value=None)
    @patch(_MB_PREFIX, return_value=None)
    @patch(_LFM_PREFIX, return_value=None)
    @patch(_BP_PREFIX, return_value=None)
    def test_no_sources_return_none(self, bp, lfm, mb, sc):
        result = resolve("Artist", "Title")
        assert result is None

    @patch(_SC_PREFIX, return_value=None)
    @patch(_MB_PREFIX, return_value=None)
    @patch(_LFM_PREFIX, return_value=None)
    @patch(_BP_PREFIX, return_value={"genre": "Tech House"})
    def test_beatport_electronic_early_exit(self, bp, lfm, mb, sc):
        """Beatport returning single specific EDM genre → early exit, high confidence."""
        result = resolve("Artist", "Title")
        assert result is not None
        assert result.main == "tech house"
        assert result.confidence == pytest.approx(0.8)
        assert len(result.breakdown) == 1
        assert result.breakdown[0].source == "beatport"
        # LFM/MB/SC should not be called when Beatport early-exits
        lfm.assert_not_called()
        mb.assert_not_called()

    @patch(_SC_PREFIX, return_value=None)
    @patch(_MB_PREFIX, return_value=None)
    @patch(_LFM_PREFIX, return_value=[("rock", 100), ("indie rock", 80)])
    @patch(_BP_PREFIX, return_value=None)
    def test_lastfm_only(self, bp, lfm, mb, sc):
        """Only Last.fm returns data → score by log-weighted counts."""
        result = resolve("Artist", "Title")
        assert result is not None
        assert result.main in {"rock", "indie rock"}
        assert len(result.breakdown) >= 1
        sources = {s.source for s in result.breakdown}
        assert "lastfm" in sources

    @patch(_SC_PREFIX, return_value=None)
    @patch(_MB_PREFIX, return_value=["house", "electronic"])
    @patch(_LFM_PREFIX, return_value=[("house", 100), ("dance", 50)])
    @patch(_BP_PREFIX, return_value=None)
    def test_mb_and_lfm_combined(self, bp, lfm, mb, sc):
        """MB + LFM agree on house → should be main."""
        result = resolve("Artist", "Title")
        assert result is not None
        assert result.main == "house"

    @patch(_SC_PREFIX, return_value=None)
    @patch(_MB_PREFIX, return_value=None)
    @patch(_LFM_PREFIX, return_value=None)
    @patch(_BP_PREFIX, return_value={"genre": "House"})
    def test_sources_param_disables_beatport(self, bp, lfm, mb, sc):
        """sources= parameter controls which APIs are called."""
        result = resolve("Artist", "Title", sources={"lastfm", "mb"})
        # Beatport should not be called
        bp.assert_not_called()
        assert result is None  # nothing returned from LFM/MB

    @patch(_SC_PREFIX, return_value={"tags": ["tech house", "deep house"]})
    @patch(_MB_PREFIX, return_value=None)
    @patch(_LFM_PREFIX, return_value=None)
    @patch(_BP_PREFIX, return_value=None)
    def test_remix_uses_soundcloud(self, bp, lfm, mb, sc):
        """Remixes trigger SoundCloud fetch."""
        result = resolve("Artist", "Title", version="Solardo Remix")
        assert result is not None
        sources = {s.source for s in result.breakdown}
        assert "soundcloud" in sources

    @patch(_SC_PREFIX, return_value=None)
    @patch(_MB_PREFIX, return_value=None)
    @patch(_LFM_PREFIX, return_value=None)
    @patch(_BP_PREFIX, return_value={"genre": "Afro House"})
    def test_subgenre_beats_parent(self, bp, lfm, mb, sc):
        """Specific subgenre with boost should be the main result."""
        result = resolve("Artist", "Title")
        assert result is not None
        assert result.main == "afro house"


# ============================================================================
# Golden-file regression tests — known tracks with expected main genre
# ============================================================================

class TestGoldenCases:
    """Regression tests using mocked source data for known tracks.
    
    These ensure weight tuning doesn't silently change classification
    for well-known edge cases.
    """

    @patch(_SC_PREFIX, return_value=None)
    @patch(_MB_PREFIX, return_value=["electronic", "house", "dance"])
    @patch(_LFM_PREFIX, return_value=[("house", 100), ("electronic", 80), ("dance", 40)])
    @patch(_BP_PREFIX, return_value={"genre": "Afro House"})
    def test_afro_house_over_generic_house(self, bp, lfm, mb, sc):
        """Beatport says 'Afro House' → specific EDM early exit."""
        result = resolve("Black Coffee", "Drive")
        assert result is not None
        assert result.main == "afro house"

    @patch(_SC_PREFIX, return_value=None)
    @patch(_MB_PREFIX, return_value=["pop", "dance pop"])
    @patch(_LFM_PREFIX, return_value=[("pop", 200), ("dance", 100), ("electronic", 30)])
    @patch(_BP_PREFIX, return_value=None)
    def test_pop_track_stays_pop(self, bp, lfm, mb, sc):
        """Pop track without Beatport data → LFM+MB consensus on pop."""
        result = resolve("Dua Lipa", "Levitating")
        assert result is not None
        assert result.main == "pop"

    @patch(_SC_PREFIX, return_value={"tags": ["tech house", "remix"]})
    @patch(_MB_PREFIX, return_value=None)
    @patch(_LFM_PREFIX, return_value=None)
    @patch(_BP_PREFIX, return_value=None)
    def test_remix_soundcloud_dominates(self, bp, lfm, mb, sc):
        """Remix where only SoundCloud has tags → SC result used."""
        result = resolve("Artist", "Track", version="Remix")
        assert result is not None
        assert "tech house" in result.main or "remix" in result.main
