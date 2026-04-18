"""Tests for Gemini Audio analysis functions in ab_test_genre.py."""
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


def test_describe_gemini_audio_basic():
    """Wraps description with correct prefix."""
    desc = "Bass: Deep sub-bass with slow filter sweep.\nDrums: Four-on-floor."
    result = describe_gemini_audio(desc)
    assert result.startswith("Audio perception (Gemini")
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


# ── Prompt framing ───────────────────────────────────────────────────────────


def test_build_prompt_ga_framing():
    """build_prompt detects Gemini Audio prefix and adds correct framing."""
    genre_labels = load_genre_labels()
    ctx = {"artist": "Floating Points", "title": "Nuits Sonores", "bpm": "125"}
    ga_desc = (
        "Audio perception (Gemini — AI listened to the actual audio):\n"
        "Bass: Deep sub-bass with slow filter sweep.\n"
        "Drums: Four-on-floor kick, open hi-hats on upbeats.\n"
        "Melody: Minor key pad chords, no clear melody.\n"
        "Energy: Constant medium-high energy, building.\n"
        "Production: Clean digital, heavy sidechain compression."
    )
    prompt_json = build_prompt(ctx, genre_labels, audio_desc=ga_desc)
    prompt_data = json.loads(prompt_json)
    system_msg = prompt_data[0]["content"]
    assert "AUDIO PERCEPTION" in system_msg
    assert "Gemini" in system_msg
    assert "bass type" in system_msg.lower() or "bass types" in system_msg.lower()


def test_build_prompt_ga_with_ws():
    """GA + WS: both signals appear in prompt."""
    genre_labels = load_genre_labels()
    ctx = {"artist": "Test", "title": "Track"}
    ga_desc = "Audio perception (Gemini — AI listened to the actual audio):\nBass: Heavy."
    ws_ctx = "Beatport: House\nDiscogs: Electronic"
    prompt_json = build_prompt(ctx, genre_labels, audio_desc=ga_desc, web_search_context=ws_ctx)
    prompt_data = json.loads(prompt_json)
    user_msg = prompt_data[1]["content"]
    assert "Gemini" in user_msg
    assert "Web search" in user_msg


def test_build_prompt_ga_vs_ei_different_framing():
    """GA and EI get different prompt framings."""
    genre_labels = load_genre_labels()
    ctx = {"artist": "Test", "title": "Track"}

    ei_desc = "Audio character (Essentia → interpreted):\nTempo: Fast."
    ga_desc = "Audio perception (Gemini — AI listened to the actual audio):\nBass: Heavy."

    prompt_ei = json.loads(build_prompt(ctx, genre_labels, audio_desc=ei_desc))
    prompt_ga = json.loads(build_prompt(ctx, genre_labels, audio_desc=ga_desc))

    assert "AUDIO CHARACTER" in prompt_ei[0]["content"]
    assert "AUDIO PERCEPTION" in prompt_ga[0]["content"]
