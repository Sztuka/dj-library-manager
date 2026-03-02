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
    _genre_hints_from_version,
    _get_remix_allowed_genres,
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

    # --- New cases: boot, flip, producer mixes ---

    def test_boot_keyword(self):
        assert _detect_remix("GOTTI Afro Boot", "Track", "Artist") is True

    def test_boot_in_title_parenthetical(self):
        assert _detect_remix("", "Love (GOTTI Afro Boot)", "INNA") is True

    def test_flip_keyword(self):
        assert _detect_remix("DJ Flip", "Track", "Artist") is True

    def test_producer_mix_afro_tech(self):
        """'Afro Tech Mix' is a producer mix → remix."""
        assert _detect_remix("ModeFlick Afro Tech Mix", "Track", "Artist") is True

    def test_producer_mix_deep_afro(self):
        assert _detect_remix("Silvio Luz Deep Afro Mix", "Track", "Artist") is True

    def test_original_mix_not_remix(self):
        assert _detect_remix("Original Mix", "Track", "Artist") is False

    def test_extended_mix_not_remix(self):
        assert _detect_remix("Extended Mix", "Track", "Artist") is False

    def test_club_mix_not_remix(self):
        assert _detect_remix("Club Mix", "Track", "Artist") is False

    def test_live_mix_not_remix(self):
        assert _detect_remix("Live Mix", "Track", "Artist") is False

    def test_instrumental_mix_not_remix(self):
        assert _detect_remix("Instrumental Mix", "Track", "Artist") is False

    def test_title_parenthetical_producer_mix(self):
        """Producer mix in title parenthetical when version is empty."""
        assert _detect_remix("", "Song (DJ Karr Mix)", "Artist") is True

    def test_title_parenthetical_original_mix_not_remix(self):
        assert _detect_remix("", "Song (Original Mix)", "Artist") is False


# ============================================================================
# _genre_hints_from_version()
# ============================================================================

class TestGenreHintsFromVersion:
    def test_afro_house_remix(self):
        hints = _genre_hints_from_version("DJ Davy Afro House Remix")
        assert "afro house" in hints

    def test_afro_tech_mix(self):
        hints = _genre_hints_from_version("ModeFlick Afro Tech Mix")
        assert any("afro" in h for h in hints)

    def test_deep_house_in_version(self):
        hints = _genre_hints_from_version("Silvio Luz Deep House Remix")
        assert "deep house" in hints

    def test_standalone_afro_becomes_afro_house(self):
        """Standalone 'afro' in remix context → afro house."""
        hints = _genre_hints_from_version("GOTTI Afro Boot")
        assert "afro house" in hints

    def test_empty_version_returns_empty(self):
        assert _genre_hints_from_version("") == []

    def test_no_genre_in_version(self):
        assert _genre_hints_from_version("Solardo Remix") == []

    def test_title_parenthetical_fallback(self):
        """When version is empty, scan title parenthetical."""
        hints = _genre_hints_from_version("", "Love (GOTTI Afro House Edit)")
        assert "afro house" in hints

    def test_title_not_scanned_when_version_present(self):
        """When version is present, title parenthetical is NOT scanned."""
        hints = _genre_hints_from_version("Plain Remix", "Song (Afro House Edit)")
        assert "afro house" not in hints

    def test_longer_match_preferred(self):
        """'afro house' should match before 'house'."""
        hints = _genre_hints_from_version("DJ Test Afro House Remix")
        assert hints[0] == "afro house"
        # "house" should NOT appear separately
        assert "house" not in hints

    def test_extended_mix_no_hints(self):
        """Standard version strings don't produce false genre hints."""
        assert _genre_hints_from_version("Extended Mix") == []


# ============================================================================
# _get_remix_allowed_genres()
# ============================================================================

class TestGetRemixAllowedGenres:
    def test_electronic_genres_included(self):
        allowed = _get_remix_allowed_genres()
        assert "house" in allowed
        assert "afro house" in allowed
        assert "tech house" in allowed
        assert "deep house" in allowed
        assert "techno" in allowed

    def test_non_electronic_excluded(self):
        allowed = _get_remix_allowed_genres()
        assert "hip hop" not in allowed
        assert "pop" not in allowed
        assert "rock" not in allowed
        assert "r b" not in allowed  # normalized "R&B"

    def test_umbrella_terms_included(self):
        allowed = _get_remix_allowed_genres()
        assert "dance" in allowed
        assert "electronic" in allowed
        assert "club" in allowed
        assert "edm" in allowed

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
        assert result.main == "Tech House"
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
        assert result.main in {"Rock", "Indie Rock"}
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
        assert result.main == "House"

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
        assert result.main == "Afro House"


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
        assert result.main == "Afro House"

    @patch(_SC_PREFIX, return_value=None)
    @patch(_MB_PREFIX, return_value=["pop", "dance pop"])
    @patch(_LFM_PREFIX, return_value=[("pop", 200), ("dance", 100), ("electronic", 30)])
    @patch(_BP_PREFIX, return_value=None)
    def test_pop_track_stays_pop(self, bp, lfm, mb, sc):
        """Pop track without Beatport data → LFM+MB consensus on pop."""
        result = resolve("Dua Lipa", "Levitating")
        assert result is not None
        assert result.main == "Pop"

    @patch(_SC_PREFIX, return_value={"tags": ["tech house", "remix"]})
    @patch(_MB_PREFIX, return_value=None)
    @patch(_LFM_PREFIX, return_value=None)
    @patch(_BP_PREFIX, return_value=None)
    def test_remix_soundcloud_dominates(self, bp, lfm, mb, sc):
        """Remix where only SoundCloud has tags → SC result used."""
        result = resolve("Artist", "Track", version="Remix")
        assert result is not None
        assert "Tech House" in result.main or "Remix" in result.main


# ============================================================================
# resolve() — tag_genre fallback
# ============================================================================

class TestResolveTagGenreFallback:
    """Tests for the tag_genre weak fallback signal in resolve()."""

    @patch(_SC_PREFIX, return_value=None)
    @patch(_MB_PREFIX, return_value=None)
    @patch(_LFM_PREFIX, return_value=None)
    @patch(_BP_PREFIX, return_value=None)
    def test_tag_genre_used_when_no_other_data(self, bp, lfm, mb, sc):
        """When all sources return nothing, tag_genre becomes the only signal."""
        result = resolve("Artist", "Title", tag_genre="House")
        assert result is not None
        assert result.main == "House"
        sources = {s.source for s in result.breakdown}
        assert "tag_genre" in sources

    @patch(_SC_PREFIX, return_value=None)
    @patch(_MB_PREFIX, return_value=None)
    @patch(_LFM_PREFIX, return_value=None)
    @patch(_BP_PREFIX, return_value={"genre": "Afro House"})
    def test_tag_genre_ignored_when_strong_signal(self, bp, lfm, mb, sc):
        """When Beatport returns strong data, tag_genre is not used."""
        result = resolve("Artist", "Title", tag_genre="Pop")
        assert result is not None
        assert result.main == "Afro House"
        # tag_genre should not appear in breakdown
        sources = {s.source for s in result.breakdown}
        assert "tag_genre" not in sources

    @patch(_SC_PREFIX, return_value=None)
    @patch(_MB_PREFIX, return_value=None)
    @patch(_LFM_PREFIX, return_value=None)
    @patch(_BP_PREFIX, return_value=None)
    def test_empty_tag_genre_not_used(self, bp, lfm, mb, sc):
        """Empty tag_genre string → no fallback triggered."""
        result = resolve("Artist", "Title", tag_genre="")
        assert result is None


# ============================================================================
# resolve() — version hint scoring
# ============================================================================

class TestResolveVersionHint:
    """Tests for version genre hint scoring in resolve()."""

    @patch(_SC_PREFIX, return_value=None)
    @patch(_MB_PREFIX, return_value=["hip hop", "rap"])
    @patch(_LFM_PREFIX, return_value=[("hip hop", 200), ("rap", 100)])
    @patch(_BP_PREFIX, return_value=None)
    def test_version_hint_overrides_lfm_mb(self, bp, lfm, mb, sc):
        """Version hint 'Afro House' should beat LFM/MB hip-hop for remixes."""
        result = resolve("Kanye West", "Stronger", version="DJ Davy Afro House Remix")
        assert result is not None
        assert result.main == "Afro House"
        sources = {s.source for s in result.breakdown}
        assert "version_hint" in sources


# ============================================================================
# resolve() — remix LFM/MB filtering
# ============================================================================

class TestResolveRemixFiltering:
    """Tests for remix-mode LFM/MB tag filtering."""

    @patch(_SC_PREFIX, return_value=None)
    @patch(_MB_PREFIX, return_value=["hip hop", "rap", "house"])
    @patch(_LFM_PREFIX, return_value=[("hip hop", 200), ("pop", 100), ("house", 50)])
    @patch(_BP_PREFIX, return_value=None)
    def test_remix_filters_non_electronic_from_lfm_mb(self, bp, lfm, mb, sc):
        """For remixes, hip-hop/pop from LFM/MB should be filtered out."""
        result = resolve("Artist", "Track", version="DJ Test Remix")
        assert result is not None
        # "house" should survive; hip-hop/pop should be filtered
        assert result.main == "House"
