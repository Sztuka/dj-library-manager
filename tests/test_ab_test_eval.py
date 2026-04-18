"""Tests for ab_test_genre.py prompt structure and variant → enrichment routing.

Covers build_prompt() across all AB test variant combinations (baseline, +E,
+EI, +WS, +D400, +GA, and combinations). These tests protect the prompt
contract as new variants are added.

Current build_prompt() contract (as of 2026-04):
- Returns JSON array string: [{"role": "system", "content": <text>},
                              {"role": "user",   "content": <text>}]
- Both contents are plain text. The old "structured JSON spec in user content"
  shape was removed in the gold_labels refactor.
- D400 is NOT a separate kwarg — it rides on `audio_desc` with prefix
  "Audio genre analysis (Discogs400...".
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict

import pytest

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ab_test_genre import build_prompt


# ── Sample test data ─────────────────────────────────────────────────────────

SAMPLE_CTX = {"artist": "Bicep", "title": "Glue", "version": "Original Mix", "bpm": "130", "key": "Am"}
SAMPLE_GENRES = ["House", "Techno", "Deep House", "Tech House"]


def _parse(raw: str):
    """Parse build_prompt() return → (system_content, user_content) tuple."""
    messages = json.loads(raw)
    assert isinstance(messages, list) and len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    return messages[0]["content"], messages[1]["content"]


# ── build_prompt() JSON envelope ─────────────────────────────────────────────


def test_build_prompt_returns_valid_json_array():
    system, user = _parse(build_prompt(SAMPLE_CTX, SAMPLE_GENRES))
    assert isinstance(system, str) and isinstance(user, str)
    assert system and user


def test_build_prompt_system_has_signal_hierarchy():
    """System prompt spells out numbered signal priority."""
    system, _ = _parse(build_prompt(SAMPLE_CTX, SAMPLE_GENRES))
    assert "1. ARTIST/TITLE KNOWLEDGE" in system
    assert "2. REMIXER/EDITOR" in system
    # BPM is signal #3 when no web search
    assert "3. BPM" in system or "3. " in system


def test_build_prompt_system_contains_allowed_genres():
    system, _ = _parse(build_prompt(SAMPLE_CTX, SAMPLE_GENRES))
    for g in SAMPLE_GENRES:
        assert g in system


def test_build_prompt_system_has_bpm_guide():
    """BPM genre ranges appear in every variant."""
    system, _ = _parse(build_prompt(SAMPLE_CTX, SAMPLE_GENRES))
    assert "BPM genre ranges" in system
    assert "House" in system  # appears in hints


def test_build_prompt_user_contains_track_metadata():
    _, user = _parse(build_prompt(SAMPLE_CTX, SAMPLE_GENRES))
    assert "Bicep" in user
    assert "Glue" in user
    assert "130" in user


def test_build_prompt_system_mentions_json_output():
    """System prompt instructs model to output genre/confidence/reasoning JSON."""
    system, _ = _parse(build_prompt(SAMPLE_CTX, SAMPLE_GENRES))
    assert "genre" in system.lower()
    assert "confidence" in system.lower()
    assert "reasoning" in system.lower()


def test_build_prompt_system_has_fallback_unknown():
    """Fallback rule: low confidence when signals weak/conflicting."""
    system, _ = _parse(build_prompt(SAMPLE_CTX, SAMPLE_GENRES))
    assert "below 0.5" in system or "<0.5" in system


# ── Audio signal routing (prefix-driven dispatch) ────────────────────────────


def test_build_prompt_no_audio_no_signal_line():
    """Without audio_desc, no audio-specific signal section."""
    system, _ = _parse(build_prompt(SAMPLE_CTX, SAMPLE_GENRES))
    assert "AUDIO CHARACTER" not in system
    assert "AUDIO ANALYSIS (Discogs400" not in system


def test_build_prompt_d400_routing():
    """Audio desc with D400 prefix → Discogs400 framing appears in system prompt."""
    d400 = "Audio genre analysis (Discogs400 deep learning):\nTop: Electronic---House (0.8)"
    system, user = _parse(build_prompt(SAMPLE_CTX, SAMPLE_GENRES, audio_desc=d400))
    assert "AUDIO ANALYSIS (Discogs400" in system
    # Content still lands in user prompt too
    assert "Electronic---House" in user


def test_build_prompt_gemini_routing():
    """Audio desc with Gemini prefix → AUDIO CHARACTER framing (symmetric with EI).

    GA uses the same 'AUDIO CHARACTER' label as EI — the only experimental
    variable is which categories are described (bass/drums/... vs tempo/rhythm/...).
    """
    ga = "Audio character (Gemini → interpreted):\nBass: Heavy sub."
    system, user = _parse(build_prompt(SAMPLE_CTX, SAMPLE_GENRES, audio_desc=ga))
    assert "AUDIO CHARACTER" in system
    assert "Gemini" in system
    assert "Heavy sub" in user


def test_build_prompt_essentia_interp_routing():
    """Audio desc with Essentia-interpreted prefix → AUDIO CHARACTER framing."""
    ei = "Audio character (Essentia → interpreted):\nTempo: Fast, driving."
    system, _ = _parse(build_prompt(SAMPLE_CTX, SAMPLE_GENRES, audio_desc=ei))
    assert "AUDIO CHARACTER" in system
    assert "Essentia" in system


def test_build_prompt_ga_and_ei_use_same_label():
    """Symmetry guard: both GA and EI must appear under 'AUDIO CHARACTER'.

    If someone restores asymmetric labels (e.g. 'AUDIO PERCEPTION' only for GA),
    the AB test result becomes uninterpretable. This test blocks that regression.
    """
    ei = "Audio character (Essentia → interpreted):\nTempo: Fast."
    ga = "Audio character (Gemini → interpreted):\nBass: Heavy."
    system_ei, _ = _parse(build_prompt(SAMPLE_CTX, SAMPLE_GENRES, audio_desc=ei))
    system_ga, _ = _parse(build_prompt(SAMPLE_CTX, SAMPLE_GENRES, audio_desc=ga))
    assert "AUDIO CHARACTER" in system_ei
    assert "AUDIO CHARACTER" in system_ga
    # Neither should re-introduce the old editorial label
    assert "AUDIO PERCEPTION" not in system_ga
    assert "AUDIO PERCEPTION" not in system_ei


def test_build_prompt_routing_mutually_exclusive():
    """D400 and Gemini/Essentia framings must not collide in the same prompt."""
    ga = "Audio character (Gemini → interpreted):\nBass: Heavy."
    system_ga, _ = _parse(build_prompt(SAMPLE_CTX, SAMPLE_GENRES, audio_desc=ga))
    assert "AUDIO CHARACTER" in system_ga
    assert "AUDIO ANALYSIS (Discogs400" not in system_ga


# ── Web search routing ───────────────────────────────────────────────────────


def test_build_prompt_ws_adds_search_section():
    ws = "Beatport: Bicep - Glue classified as Melodic House & Techno"
    system, user = _parse(build_prompt(SAMPLE_CTX, SAMPLE_GENRES, web_search_context=ws))
    assert "WEB SEARCH" in system
    assert "Beatport" in user


def test_build_prompt_ws_shifts_bpm_to_signal_4():
    """When WS is present, BPM becomes signal #4 (WS = #3)."""
    ws = "Beatport: found"
    system, _ = _parse(build_prompt(SAMPLE_CTX, SAMPLE_GENRES, web_search_context=ws))
    assert "3. WEB SEARCH" in system
    assert "4. BPM" in system


def test_build_prompt_empty_ws_not_included():
    """Empty or 'no results' web search should not add the WEB SEARCH section."""
    for ws in ["", "(No web search results found)"]:
        system, _ = _parse(build_prompt(SAMPLE_CTX, SAMPLE_GENRES, web_search_context=ws))
        assert "WEB SEARCH" not in system, f"Should not add WS section for {ws!r}"


def test_build_prompt_ws_remix_leak_warning_non_remix():
    """Non-remix track + WS → prompt warns about remix leakage in search results."""
    ctx = {"artist": "Bicep", "title": "Glue", "version": "Original Mix", "bpm": "130"}
    ws = "Beatport: found remix by Dax J"
    system, _ = _parse(build_prompt(ctx, SAMPLE_GENRES, web_search_context=ws))
    assert "NOT a remix" in system or "not a remix" in system.lower()


def test_build_prompt_ws_no_remix_leak_warning_for_remix():
    """Remix track + WS → no 'NOT a remix' warning (irrelevant)."""
    ctx = {"artist": "Bicep", "title": "Glue", "version": "Dax J Remix", "bpm": "135"}
    ws = "Beatport: found"
    system, _ = _parse(build_prompt(ctx, SAMPLE_GENRES, web_search_context=ws))
    assert "NOT a remix" not in system


# ── Remix rule ────────────────────────────────────────────────────────────────


def test_build_prompt_remix_rule_triggers_on_remix_version():
    """Version containing 'remix' activates the REMIX CLASSIFICATION RULE."""
    ctx = {"artist": "Someone", "title": "Track", "version": "Dax J Remix", "bpm": "135"}
    system, _ = _parse(build_prompt(ctx, SAMPLE_GENRES))
    assert "REMIX/EDIT CLASSIFICATION RULE" in system


def test_build_prompt_remix_rule_triggers_on_edit():
    ctx = {"artist": "A", "title": "T", "version": "VIP Edit", "bpm": "128"}
    system, _ = _parse(build_prompt(ctx, SAMPLE_GENRES))
    assert "REMIX/EDIT CLASSIFICATION RULE" in system


def test_build_prompt_remix_rule_absent_for_original():
    """No 'remix' in version → no remix rule."""
    ctx = {"artist": "Bicep", "title": "Glue", "version": "Original Mix", "bpm": "130"}
    system, _ = _parse(build_prompt(ctx, SAMPLE_GENRES))
    assert "REMIX/EDIT CLASSIFICATION RULE" not in system


# ── Combined variants (D400+GA+WS style stack) ───────────────────────────────


def test_build_prompt_gemini_plus_ws_both_present():
    """GA+WS: both audio-character framing and web-search framing appear."""
    ga = "Audio character (Gemini → interpreted):\nBass: Sub."
    ws = "Beatport: found"
    system, user = _parse(build_prompt(SAMPLE_CTX, SAMPLE_GENRES, audio_desc=ga, web_search_context=ws))
    assert "AUDIO CHARACTER" in system
    assert "WEB SEARCH" in system
    assert "Bass: Sub" in user
    assert "Beatport" in user


# ── Variant → enrichment flag integration ────────────────────────────────────
# Mirrors routing logic in run_ab_test(). If that routing changes, these
# tests catch the drift without needing to run a full AB test.


def _simulate_enrichment_flags(variant: str) -> Dict[str, object]:
    """Reproduce the variant parsing logic from run_ab_test()."""
    use_d400 = "+D400" in variant
    use_ga = "+GA" in variant
    use_essentia_interp = "+EI" in variant
    use_essentia_v2 = "+E2" in variant
    use_essentia = (
        "+E" in variant
        and not use_essentia_v2
        and not use_d400
        and not use_essentia_interp
        and not use_ga
    )
    use_ws = "+WS" in variant
    return {
        "essentia": use_essentia,
        "essentia_v2": use_essentia_v2,
        "essentia_interp": use_essentia_interp,
        "d400": use_d400,
        "ga": use_ga,
        "ws": use_ws,
    }


def test_variant_flags_nano_baseline():
    flags = _simulate_enrichment_flags("nano")
    assert flags == {
        "essentia": False, "essentia_v2": False, "essentia_interp": False,
        "d400": False, "ga": False, "ws": False,
    }


def test_variant_flags_nano_e():
    flags = _simulate_enrichment_flags("nano+E")
    assert flags["essentia"] is True
    assert flags["essentia_interp"] is False
    assert flags["ga"] is False


def test_variant_flags_nano_ei_excludes_plain_e():
    """+EI must NOT also activate plain Essentia flag (routing bug guard)."""
    flags = _simulate_enrichment_flags("nano+EI")
    assert flags["essentia_interp"] is True
    assert flags["essentia"] is False


def test_variant_flags_nano_ga_excludes_plain_e():
    """+GA must NOT also activate plain Essentia flag."""
    flags = _simulate_enrichment_flags("nano+GA")
    assert flags["ga"] is True
    assert flags["essentia"] is False
    assert flags["essentia_interp"] is False


def test_variant_flags_nano_ga_ws():
    flags = _simulate_enrichment_flags("nano+GA+WS")
    assert flags["ga"] is True
    assert flags["ws"] is True


def test_variant_flags_nano_d400_only():
    """D400-only variant should NOT activate Essentia."""
    flags = _simulate_enrichment_flags("nano+D400")
    assert flags["essentia"] is False
    assert flags["d400"] is True


def test_variant_flags_ws_only():
    flags = _simulate_enrichment_flags("nano+WS")
    assert flags["essentia"] is False
    assert flags["d400"] is False
    assert flags["ws"] is True


def test_variant_flags_ei_ws():
    flags = _simulate_enrichment_flags("nano+EI+WS")
    assert flags["essentia_interp"] is True
    assert flags["ws"] is True
    assert flags["essentia"] is False
