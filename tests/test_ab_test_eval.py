"""Tests for ab_test_genre.py evaluation metrics and variant parsing."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List

import pytest

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


# ── Import evaluation functions ──────────────────────────────────────────────

from ab_test_genre import (
    _build_confusion_matrix,
    _detect_genre_drift,
    _find_disagreements,
    _parse_variant_ws_backend,
    build_prompt,
    EXPERIMENT_VARIANTS,
    RUN_PRESETS,
    WS_BACKENDS,
)


# ── Sample test data ─────────────────────────────────────────────────────────

SAMPLE_RESULTS = [
    {"filename": "track1.mp3", "expected_genre": "Tech House", "predicted_genre": "Tech House",
     "correct": True, "variant": "nano", "confidence": 0.9},
    {"filename": "track1.mp3", "expected_genre": "Tech House", "predicted_genre": "House",
     "correct": False, "variant": "nano+E", "confidence": 0.7},
    {"filename": "track2.mp3", "expected_genre": "Deep House", "predicted_genre": "Melodic Techno",
     "correct": False, "variant": "nano", "confidence": 0.6},
    {"filename": "track2.mp3", "expected_genre": "Deep House", "predicted_genre": "Deep House",
     "correct": True, "variant": "nano+E", "confidence": 0.85},
    {"filename": "track3.mp3", "expected_genre": "House", "predicted_genre": "Disco House",
     "correct": False, "variant": "nano", "confidence": 0.5},
    {"filename": "track3.mp3", "expected_genre": "House", "predicted_genre": "Disco House",
     "correct": False, "variant": "nano+E", "confidence": 0.55},
    {"filename": "track4.mp3", "expected_genre": "Techno", "predicted_genre": "UNKNOWN",
     "correct": False, "variant": "nano", "confidence": 0.2},
]


# ── Variant parsing ─────────────────────────────────────────────────────────

def test_parse_variant_ws_backend_ddg():
    assert _parse_variant_ws_backend("nano+WS-ddg") == "ddg"


def test_parse_variant_ws_backend_serper():
    assert _parse_variant_ws_backend("nano+WS-serper") == "serper"


def test_parse_variant_ws_backend_combined():
    assert _parse_variant_ws_backend("nano+WS-ddg+E") == "ddg"


def test_parse_variant_ws_backend_none():
    assert _parse_variant_ws_backend("nano+E") is None
    assert _parse_variant_ws_backend("nano") is None


def test_experiment_variants_mapping():
    """All 5 experiment variants (A-E) should be mapped."""
    assert len(EXPERIMENT_VARIANTS) == 5
    for letter in "ABCDE":
        assert letter in EXPERIMENT_VARIANTS


def test_run_presets_exist():
    """RUN 1/2/3 presets should exist."""
    assert "run1" in RUN_PRESETS
    assert "run2" in RUN_PRESETS
    assert "run3" in RUN_PRESETS


def test_run_preset_run2_has_all_search_backends():
    """RUN 2 should compare all 3 web search backends (DDG removed)."""
    run2 = RUN_PRESETS["run2"]
    backends_in_run2 = set()
    for v in run2:
        b = _parse_variant_ws_backend(v)
        if b:
            backends_in_run2.add(b)
    assert backends_in_run2 == {"serper", "searxng", "brave"}


# ── Confusion matrix ─────────────────────────────────────────────────────────

def test_confusion_matrix_basic():
    matrix = _build_confusion_matrix(SAMPLE_RESULTS)
    assert "Tech House" in matrix
    assert matrix["Tech House"]["Tech House"] == 1
    assert matrix["Tech House"]["House"] == 1


def test_confusion_matrix_variant_filter():
    matrix = _build_confusion_matrix(SAMPLE_RESULTS, variant_filter="nano")
    assert "Tech House" in matrix
    assert matrix["Tech House"]["Tech House"] == 1
    # nano+E result for track1 should be excluded
    assert "House" not in matrix.get("Tech House", {})


def test_confusion_matrix_empty():
    matrix = _build_confusion_matrix([])
    assert matrix == {}


# ── Genre drift ──────────────────────────────────────────────────────────────

def test_genre_drift_detects_pattern():
    """House→Disco House appears 2x (across nano and nano+E) — should be detected."""
    drifts = _detect_genre_drift(SAMPLE_RESULTS, min_count=2)
    patterns = [d["pattern"] for d in drifts]
    assert "House → Disco House" in patterns


def test_genre_drift_min_count():
    """With min_count=3, House→Disco House (count=2) should NOT appear."""
    drifts = _detect_genre_drift(SAMPLE_RESULTS, min_count=3)
    assert drifts == []


def test_genre_drift_empty():
    all_correct = [
        {"expected_genre": "House", "predicted_genre": "House", "correct": True, "variant": "nano"},
    ]
    drifts = _detect_genre_drift(all_correct)
    assert drifts == []


# ── Disagreement analysis ────────────────────────────────────────────────────

def test_disagreement_track1():
    """track1: nano predicts Tech House, nano+E predicts House — disagreement."""
    disagreements = _find_disagreements(SAMPLE_RESULTS)
    filenames = [d["filename"] for d in disagreements]
    assert "track1.mp3" in filenames

    track1_dis = next(d for d in disagreements if d["filename"] == "track1.mp3")
    assert track1_dis["predictions"]["nano"] == "Tech House"
    assert track1_dis["predictions"]["nano+E"] == "House"
    assert track1_dis["distinct_count"] == 2


def test_disagreement_track3_agrees():
    """track3: both variants predict Disco House — no disagreement."""
    disagreements = _find_disagreements(SAMPLE_RESULTS)
    filenames = [d["filename"] for d in disagreements]
    assert "track3.mp3" not in filenames


def test_disagreement_empty():
    disagreements = _find_disagreements([])
    assert disagreements == []


def test_disagreement_sorted_by_count():
    """Tracks with more distinct predictions should come first."""
    results = [
        {"filename": "a.mp3", "expected_genre": "X", "predicted_genre": "A", "variant": "v1"},
        {"filename": "a.mp3", "expected_genre": "X", "predicted_genre": "B", "variant": "v2"},
        {"filename": "b.mp3", "expected_genre": "Y", "predicted_genre": "A", "variant": "v1"},
        {"filename": "b.mp3", "expected_genre": "Y", "predicted_genre": "B", "variant": "v2"},
        {"filename": "b.mp3", "expected_genre": "Y", "predicted_genre": "C", "variant": "v3"},
    ]
    disagreements = _find_disagreements(results)
    assert len(disagreements) == 2
    # b.mp3 has 3 distinct, a.mp3 has 2 — b first
    assert disagreements[0]["filename"] == "b.mp3"
    assert disagreements[0]["distinct_count"] == 3


# ── build_prompt() JSON structure ────────────────────────────────────────────

SAMPLE_CTX = {"artist": "Bicep", "title": "Glue", "version": "Original Mix", "bpm": "130", "key": "Am"}
SAMPLE_GENRES = ["House", "Techno", "Deep House", "Tech House"]


def test_build_prompt_returns_valid_json():
    """build_prompt() should return valid JSON array of messages."""
    import json
    raw = build_prompt(SAMPLE_CTX, SAMPLE_GENRES)
    messages = json.loads(raw)
    assert isinstance(messages, list)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


def test_build_prompt_user_content_is_structured():
    """User content should be a JSON object with task spec fields."""
    import json
    messages = json.loads(build_prompt(SAMPLE_CTX, SAMPLE_GENRES))
    spec = json.loads(messages[1]["content"])
    assert spec["task"] == "genre_classification"
    assert isinstance(spec["instructions"], list)
    assert isinstance(spec["signal_priority"], dict)
    assert spec["track"]["artist"] == "Bicep"
    assert "bpm_guide" in spec
    assert spec["allowed_genres"] == SAMPLE_GENRES
    assert "genre" in spec["output_format"]
    assert "reasoning" in spec["output_format"]
    assert "confidence" in spec["output_format"]


def test_build_prompt_no_enrichments():
    """Without enrichments, no audio/search/discogs sections."""
    import json
    messages = json.loads(build_prompt(SAMPLE_CTX, SAMPLE_GENRES))
    spec = json.loads(messages[1]["content"])
    assert "audio_features" not in spec
    assert "discogs_predictions" not in spec
    assert "search_results" not in spec
    # Priority: artist/remixer, metadata/BPM only
    assert len(spec["signal_priority"]) == 2


def test_build_prompt_with_essentia():
    """Essentia adds audio_features and priority entry."""
    import json
    messages = json.loads(build_prompt(SAMPLE_CTX, SAMPLE_GENRES, audio_desc="BPM: 130, Energy: 0.7"))
    spec = json.loads(messages[1]["content"])
    assert "audio_features" in spec
    assert "discogs_predictions" not in spec
    assert "search_results" not in spec
    # Essentia is last priority
    priorities = list(spec["signal_priority"].values())
    assert any("Essentia" in p for p in priorities)


def test_build_prompt_with_web_search():
    """Web search adds search_results and Beatport becomes top priority."""
    import json
    ws = "Beatport: Bicep - Glue classified as Melodic House & Techno"
    messages = json.loads(build_prompt(SAMPLE_CTX, SAMPLE_GENRES, web_search_context=ws))
    spec = json.loads(messages[1]["content"])
    assert "search_results" in spec
    # Beatport-specific priority is first when web search is present
    assert "Beatport" in spec["signal_priority"]["1"]


def test_build_prompt_combined_essentia_discogs():
    """E+D400: both audio_features and discogs_predictions present."""
    import json
    messages = json.loads(build_prompt(
        SAMPLE_CTX, SAMPLE_GENRES,
        audio_desc="BPM: 130, Energy: 0.7",
        discogs_desc="Top: Electronic---House (0.8)",
    ))
    spec = json.loads(messages[1]["content"])
    assert "audio_features" in spec
    assert "discogs_predictions" in spec


def test_build_prompt_all_enrichments():
    """WS+E+D400: all enrichment sections present (D400 still supported in prompt)."""
    import json
    ws = "Beatport: Bicep - Glue — Melodic House & Techno"
    messages = json.loads(build_prompt(
        SAMPLE_CTX, SAMPLE_GENRES,
        audio_desc="BPM: 130, Energy: 0.7",
        web_search_context=ws,
        discogs_desc="Top: Electronic---House (0.8)",
    ))
    spec = json.loads(messages[1]["content"])
    assert "audio_features" in spec
    assert "discogs_predictions" in spec
    assert "search_results" in spec
    # All priority levels (Beatport, SoundCloud, Discogs+web, artist, discogs400, metadata, essentia)
    assert len(spec["signal_priority"]) == 7


def test_build_prompt_remix_rule_always_present():
    """Remix rule should be in instructions even without remix in version."""
    import json
    ctx = {"artist": "Aphex Twin", "title": "Windowlicker", "bpm": "140"}
    messages = json.loads(build_prompt(ctx, SAMPLE_GENRES))
    spec = json.loads(messages[1]["content"])
    remix_instructions = [i for i in spec["instructions"] if "remixer" in i.lower()]
    assert len(remix_instructions) >= 1


def test_build_prompt_fallback_unknown():
    """Fallback rule should mention UNKNOWN."""
    import json
    messages = json.loads(build_prompt(SAMPLE_CTX, SAMPLE_GENRES))
    spec = json.loads(messages[1]["content"])
    assert "UNKNOWN" in spec["fallback_rule"]


def test_build_prompt_empty_ws_not_included():
    """Empty or 'no results' web search should not add search_results."""
    import json
    for ws in ["", "  ", "(No web search results found)"]:
        messages = json.loads(build_prompt(SAMPLE_CTX, SAMPLE_GENRES, web_search_context=ws))
        spec = json.loads(messages[1]["content"])
        assert "search_results" not in spec


# ── RUN 3 variants ────────────────────────────────────────────────────────────

def test_run3_all_ws_backends_with_essentia():
    """RUN 3 should have all 3 WS backends + Essentia (DDG removed)."""
    run3 = RUN_PRESETS["run3"]
    assert len(run3) == 3
    backends = set()
    for v in run3:
        assert "+E" in v
        b = _parse_variant_ws_backend(v)
        assert b is not None
        backends.add(b)
    assert backends == {"serper", "searxng", "brave"}


def test_parse_variant_ws_backend_combined_d400():
    """WS backend parsing works for combined WS+E+D400 variants."""
    assert _parse_variant_ws_backend("nano+WS-ddg+E+D400") == "ddg"
    assert _parse_variant_ws_backend("nano+WS-serper+E+D400") == "serper"
    assert _parse_variant_ws_backend("nano+WS-brave+E") == "brave"


# ── Variant → enrichment flag integration ────────────────────────────────────

def _simulate_enrichment_flags(variant: str):
    """Reproduce the variant parsing logic from run_ab_test()."""
    use_d400 = "+D400" in variant
    use_essentia_v2 = "+E2" in variant
    use_essentia = "+E" in variant and not use_essentia_v2
    ws_backend = _parse_variant_ws_backend(variant)
    return {"essentia": use_essentia, "essentia_v2": use_essentia_v2,
            "d400": use_d400, "ws": ws_backend}


def test_variant_flags_nano_baseline():
    flags = _simulate_enrichment_flags("nano")
    assert flags == {"essentia": False, "essentia_v2": False, "d400": False, "ws": None}


def test_variant_flags_nano_e():
    flags = _simulate_enrichment_flags("nano+E")
    assert flags == {"essentia": True, "essentia_v2": False, "d400": False, "ws": None}


def test_variant_flags_nano_e_d400():
    """E+D400: BOTH essentia and d400 must be active simultaneously."""
    flags = _simulate_enrichment_flags("nano+E+D400")
    assert flags["essentia"] is True
    assert flags["d400"] is True
    assert flags["ws"] is None


def test_variant_flags_nano_d400_only():
    """D400-only variant should NOT activate essentia."""
    flags = _simulate_enrichment_flags("nano+D400")
    assert flags["essentia"] is False
    assert flags["d400"] is True


def test_variant_flags_ws_e_d400_full_stack():
    """Full stack: WS + E + D400 all active."""
    flags = _simulate_enrichment_flags("nano+WS-ddg+E+D400")
    assert flags["essentia"] is True
    assert flags["d400"] is True
    assert flags["ws"] == "ddg"


def test_variant_flags_ws_only():
    """WS-only variant has no audio enrichments."""
    flags = _simulate_enrichment_flags("nano+WS-serper")
    assert flags["essentia"] is False
    assert flags["d400"] is False
    assert flags["ws"] == "serper"


def test_variant_flags_ws_e():
    """WS+E: web search + essentia, no d400."""
    flags = _simulate_enrichment_flags("nano+WS-brave+E")
    assert flags["essentia"] is True
    assert flags["d400"] is False
    assert flags["ws"] == "brave"


def test_build_prompt_signal_priority_order_no_enrichments():
    """Baseline: artist first, metadata second."""
    import json
    spec = json.loads(json.loads(build_prompt(SAMPLE_CTX, SAMPLE_GENRES))[1]["content"])
    assert spec["signal_priority"]["1"] == "artist/remixer scene knowledge"
    assert "track metadata" in spec["signal_priority"]["2"]


def test_build_prompt_signal_priority_order_ws_e_d400():
    """Full stack: Beatport > SoundCloud > Discogs+web > artist > discogs400 > metadata > essentia."""
    import json
    spec = json.loads(json.loads(build_prompt(
        SAMPLE_CTX, SAMPLE_GENRES,
        audio_desc="X", web_search_context="Y", discogs_desc="Z",
    ))[1]["content"])
    prio = spec["signal_priority"]
    # Web search splits into Beatport, SoundCloud, and Discogs+web evidence
    assert "Beatport" in prio["1"]
    assert "SoundCloud" in prio["2"]
    assert "Discogs" in prio["3"]
    assert prio["4"] == "artist/remixer scene knowledge"
    assert "Discogs400" in prio["5"]
    assert "track metadata" in prio["6"]
    assert "Essentia" in prio["7"]
