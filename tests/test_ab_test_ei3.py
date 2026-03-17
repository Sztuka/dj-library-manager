"""Tests for Essentia Interpreter v3 (diagnostic questions) in ab_test_genre.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict
from unittest.mock import patch, MagicMock

import pytest

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ab_test_genre import (
    interpret_audio_features_v3,
    ALL_VARIANTS,
    build_prompt,
    load_genre_labels,
)


# ── Variant registration ─────────────────────────────────────────────────────


def test_ei3_variant_registered():
    """nano+EI3 is in ALL_VARIANTS."""
    assert "nano+EI3" in ALL_VARIANTS


# ── Interpreter v3 prompt file ────────────────────────────────────────────────


def test_interpreter_v3_prompt_exists():
    """interpreter_essentia_v3.md exists."""
    prompt_file = (
        Path(__file__).resolve().parent.parent
        / "data" / "ab_test" / "prompts" / "interpreter_essentia_v3.md"
    )
    assert prompt_file.exists(), f"Missing: {prompt_file}"
    content = prompt_file.read_text(encoding="utf-8")
    assert "{audio_features_reference}" in content
    assert "Kick/drums:" in content
    assert "Production:" in content
    assert "Frequency:" in content
    assert "Texture:" in content
    assert "Harmony:" in content


def test_interpreter_v3_prompt_has_5_questions():
    """v3 prompt defines 5 diagnostic questions."""
    prompt_file = (
        Path(__file__).resolve().parent.parent
        / "data" / "ab_test" / "prompts" / "interpreter_essentia_v3.md"
    )
    content = prompt_file.read_text(encoding="utf-8")
    # Count Q1-Q5
    for q in ["Q1", "Q2", "Q3", "Q4", "Q5"]:
        assert q in content, f"Missing question {q}"


def test_interpreter_v3_prompt_no_genre_names():
    """v3 prompt tells interpreter not to use genre names."""
    prompt_file = (
        Path(__file__).resolve().parent.parent
        / "data" / "ab_test" / "prompts" / "interpreter_essentia_v3.md"
    )
    content = prompt_file.read_text(encoding="utf-8")
    assert "NEVER mention genre names" in content


# ── interpret_audio_features_v3 (mocked) ─────────────────────────────────────


SAMPLE_FEATURES = {
    "onset_rate": 5.2,
    "bpm_conf": 0.95,
    "danceability": 1.3,
    "dyn_complex": 2.1,
    "energy": 0.65,
    "lufs": -8.5,
    "spec_centroid": 2800.0,
    "spec_rolloff": 6500.0,
    "spec_flux_mean": 0.12,
    "hfc_mean": 45.0,
    "spec_flatness_mean": 0.08,
    "zero_crossing_rate": 0.06,
    "chords_changes_rate": 0.15,
    "tuning_diatonic_strength": 0.62,
    "mfcc_0": -250,
    "mfcc_1": 80,
    "mfcc_2": -20,
    "mfcc_3": 30,
    "mfcc_4": -10,
}


@patch("ab_test_genre._call_openai_text")
def test_interpret_v3_happy_path(mock_call):
    """v3 interpreter returns 5 diagnostic answers with correct prefix."""
    mock_call.return_value = (
        "Kick/drums: Steady four-on-floor pulse at moderate density with flat dynamics.\n"
        "Production: Loud, clean, compressed with consistent spectral character.\n"
        "Frequency: Bright with energy in upper-mids and highs.\n"
        "Texture: Moderate density, layered tonal and percussive elements.\n"
        "Harmony: Minimal harmonic movement, groove-focused."
    )
    result = interpret_audio_features_v3(
        SAMPLE_FEATURES, "128", "9A", "fake-key", "gpt-5-nano"
    )
    assert result.startswith("Audio diagnostic (Essentia → interpreted v3):")
    assert "Kick/drums:" in result
    assert "Production:" in result
    assert "Frequency:" in result


@patch("ab_test_genre._call_openai_text")
def test_interpret_v3_empty_features(mock_call):
    """v3 interpreter returns empty string for empty features."""
    result = interpret_audio_features_v3({}, "128", "9A", "fake-key")
    assert result == ""
    mock_call.assert_not_called()


@patch("ab_test_genre._call_openai_text")
def test_interpret_v3_partial_response(mock_call):
    """v3 interpreter handles partial response (less than 3/5 answers)."""
    mock_call.return_value = "Kick/drums: Something.\nProduction: Something else."
    result = interpret_audio_features_v3(
        SAMPLE_FEATURES, "128", "9A", "fake-key"
    )
    # Should still return something (with a warning printed)
    assert result.startswith("Audio diagnostic (Essentia → interpreted v3):")


# ── Prompt framing ───────────────────────────────────────────────────────────


def test_build_prompt_ei3_framing():
    """build_prompt detects EI3 prefix and adds correct framing."""
    genre_labels = load_genre_labels()
    ctx = {"artist": "Test", "title": "Track", "bpm": "128"}
    ei3_desc = (
        "Audio diagnostic (Essentia → interpreted v3):\n"
        "Kick/drums: Steady four-on-floor pulse.\n"
        "Production: Loud and compressed.\n"
        "Frequency: Bright, upper-mids dominant.\n"
        "Texture: Moderate density.\n"
        "Harmony: Minimal chord movement."
    )
    prompt_json = build_prompt(ctx, genre_labels, audio_desc=ei3_desc)
    prompt_data = json.loads(prompt_json)
    system_msg = prompt_data[0]["content"]
    assert "AUDIO DIAGNOSTIC" in system_msg
    assert "interpreted v3" in system_msg
    assert "kick pattern" in system_msg.lower() or "kick/drum" in system_msg.lower()

    # User message should contain the diagnostic answers
    user_msg = prompt_data[1]["content"]
    assert "Kick/drums:" in user_msg


def test_build_prompt_ei3_vs_ei_different_framing():
    """EI3 and EI get different prompt framings."""
    genre_labels = load_genre_labels()
    ctx = {"artist": "Test", "title": "Track"}

    ei_desc = "Audio character (Essentia → interpreted):\nTempo: Fast."
    ei3_desc = "Audio diagnostic (Essentia → interpreted v3):\nKick/drums: Steady."

    prompt_ei = json.loads(build_prompt(ctx, genre_labels, audio_desc=ei_desc))
    prompt_ei3 = json.loads(build_prompt(ctx, genre_labels, audio_desc=ei3_desc))

    # Different framing text
    assert "AUDIO CHARACTER" in prompt_ei[0]["content"]
    assert "AUDIO DIAGNOSTIC" in prompt_ei3[0]["content"]
