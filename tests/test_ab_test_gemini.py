"""Tests for Gemini Audio analysis functions in ab_test_genre.py.

Critical invariant: the framing of GA and EI in the classification prompt
must be SYMMETRIC. Different categories (Bass/Drums/... vs Tempo/Rhythm/...)
are the experimental variable — everything else must match, or the AB test
isn't measuring what we claim.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ab_test_genre import (
    describe_gemini_audio,
    run_gemini_audio_analysis,
    GEMINI_AUDIO_SYSTEM_PROMPT,
    ALL_VARIANTS,
    build_prompt,
    load_genre_labels,
)


# ── Variant registration ─────────────────────────────────────────────────────


def test_ga_variants_registered():
    """nano+GA and nano+GA+WS are in ALL_VARIANTS."""
    assert "nano+GA" in ALL_VARIANTS
    assert "nano+GA+WS" in ALL_VARIANTS


# ── System prompt ─────────────────────────────────────────────────────────────


def test_gemini_system_prompt_describes_not_classifies():
    """System prompt instructs Gemini to DESCRIBE, not classify."""
    assert "NEVER mention genre names" in GEMINI_AUDIO_SYSTEM_PROMPT
    assert "Bass:" in GEMINI_AUDIO_SYSTEM_PROMPT
    assert "Drums:" in GEMINI_AUDIO_SYSTEM_PROMPT
    assert "Melody:" in GEMINI_AUDIO_SYSTEM_PROMPT
    assert "Energy:" in GEMINI_AUDIO_SYSTEM_PROMPT
    assert "Production:" in GEMINI_AUDIO_SYSTEM_PROMPT


def test_gemini_system_prompt_word_limit():
    """System prompt asks for under 100 words."""
    assert "100 words" in GEMINI_AUDIO_SYSTEM_PROMPT


# ── describe_gemini_audio ─────────────────────────────────────────────────────


def test_describe_gemini_audio_prefix_symmetric_with_ei():
    """GA prefix is structurally symmetric with EI: 'Audio character (X → interpreted):'."""
    desc = "Bass: Deep sub-bass with slow filter sweep.\nDrums: Four-on-floor."
    result = describe_gemini_audio(desc)
    assert result.startswith("Audio character (Gemini → interpreted):")
    assert "Deep sub-bass" in result


def test_describe_gemini_audio_empty():
    """Empty input returns empty string."""
    assert describe_gemini_audio("") == ""
    assert describe_gemini_audio(None) == ""


# ── run_gemini_audio_analysis (mocked) ────────────────────────────────────────


@patch("ab_test_genre._get_gemini_client")
def test_run_gemini_no_client(mock_client):
    """Returns None when Gemini client can't be created."""
    mock_client.return_value = None
    result = run_gemini_audio_analysis("/fake/path.mp3")
    assert result is None


@patch("ab_test_genre._extract_audio_clip")
@patch("ab_test_genre._get_gemini_client")
def test_run_gemini_no_audio(mock_client, mock_clip):
    """Returns None when audio clip extraction fails."""
    mock_client.return_value = MagicMock()
    mock_clip.return_value = None
    result = run_gemini_audio_analysis("/fake/path.mp3")
    assert result is None


# ── Prompt framing symmetry (the critical regression test) ────────────────────


def _audio_section(system_msg: str) -> str:
    """Extract the audio-signal line from a system prompt (for comparison)."""
    # The audio line starts with "* AUDIO CHARACTER" and ends at the next \n\n
    lines = system_msg.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("* AUDIO CHARACTER"):
            return line
    return ""


def test_ga_and_ei_framing_is_symmetric():
    """The single experimental variable between +GA and +EI is which categories
    are described. Everything else in the prompt — verb, structure, quality
    claims, emphasis — must be identical. This test will fail if anyone adds
    editorial bias to one side (e.g. 'most detailed', 'LISTENED', 'superior').
    """
    genre_labels = load_genre_labels()
    ctx = {"artist": "Test", "title": "Track", "bpm": "128"}

    ei_desc = "Audio character (Essentia → interpreted):\nTempo: Fast."
    ga_desc = "Audio character (Gemini → interpreted):\nBass: Heavy."

    ei_prompt = json.loads(build_prompt(ctx, genre_labels, audio_desc=ei_desc))
    ga_prompt = json.loads(build_prompt(ctx, genre_labels, audio_desc=ga_desc))

    ei_line = _audio_section(ei_prompt[0]["content"])
    ga_line = _audio_section(ga_prompt[0]["content"])

    assert ei_line, "EI audio line missing from system prompt"
    assert ga_line, "GA audio line missing from system prompt"

    # Same verb/structure — both must use 'Use this to inform the genre decision'
    assert "Use this to inform the genre decision" in ei_line
    assert "Use this to inform the genre decision" in ga_line

    # No quality claims on either side
    forbidden = ["most detailed", "LISTENED", "best", "superior", "strongest audio"]
    for phrase in forbidden:
        assert phrase not in ga_line, f"GA framing contains bias: {phrase!r}"
        assert phrase not in ei_line, f"EI framing contains bias: {phrase!r}"

    # Same framing verb for both: 'SOUNDS LIKE' (describes, doesn't classify)
    assert "SOUNDS LIKE" in ei_line
    assert "SOUNDS LIKE" in ga_line

    # Same "WITHOUT naming genres" guard
    assert "WITHOUT naming genres" in ei_line
    assert "WITHOUT naming genres" in ga_line


def test_ga_framing_labels_source_without_quality_claim():
    """GA prompt says 'Gemini → interpreted' (sources it) without elevating it."""
    genre_labels = load_genre_labels()
    ctx = {"artist": "Test", "title": "Track"}
    ga_desc = "Audio character (Gemini → interpreted):\nBass: Heavy."

    prompt = json.loads(build_prompt(ctx, genre_labels, audio_desc=ga_desc))
    system_msg = prompt[0]["content"]

    assert "(Gemini → interpreted)" in system_msg
    # Descriptor vocabulary from Gemini's 5 categories
    assert "bass" in system_msg.lower()
    assert "drums" in system_msg.lower()
    assert "production" in system_msg.lower()


def test_build_prompt_ga_with_ws_both_signals_present():
    """GA + WS: both signals appear in the user prompt."""
    genre_labels = load_genre_labels()
    ctx = {"artist": "Test", "title": "Track"}
    ga_desc = "Audio character (Gemini → interpreted):\nBass: Heavy."
    ws_ctx = "Beatport: House\nDiscogs: Electronic"
    prompt = json.loads(build_prompt(ctx, genre_labels, audio_desc=ga_desc, web_search_context=ws_ctx))
    user_msg = prompt[1]["content"]
    assert "Gemini" in user_msg
    assert "Web search" in user_msg
