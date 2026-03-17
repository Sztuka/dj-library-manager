"""Tests for CLAP zero-shot genre analysis functions in ab_test_genre.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

# Check if torch is available (optional dependency for CLAP)
try:
    import torch
    _has_torch = True
except ImportError:
    _has_torch = False

from ab_test_genre import (
    _load_clap_genre_descriptions,
    describe_clap_features,
    run_clap_analysis,
    ALL_VARIANTS,
    build_prompt,
    load_genre_labels,
)


# ── Genre descriptions ───────────────────────────────────────────────────────


def test_clap_genre_descriptions_file_exists():
    """clap_genre_descriptions.json exists and has 48 entries."""
    desc_file = Path(__file__).resolve().parent.parent / "data" / "ab_test" / "clap_genre_descriptions.json"
    assert desc_file.exists(), f"Missing: {desc_file}"
    data = json.loads(desc_file.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert len(data) >= 48, f"Expected 48 genres, got {len(data)}"


def test_clap_genre_descriptions_match_genres_yml():
    """Every genre in genres.yml has a CLAP description."""
    genre_labels = load_genre_labels()
    desc_file = Path(__file__).resolve().parent.parent / "data" / "ab_test" / "clap_genre_descriptions.json"
    data = json.loads(desc_file.read_text(encoding="utf-8"))
    for label in genre_labels:
        assert label in data, f"Genre '{label}' missing from clap_genre_descriptions.json"


def test_clap_genre_descriptions_no_empty_values():
    """No empty descriptions in the file."""
    desc_file = Path(__file__).resolve().parent.parent / "data" / "ab_test" / "clap_genre_descriptions.json"
    data = json.loads(desc_file.read_text(encoding="utf-8"))
    for genre, desc in data.items():
        assert desc.strip(), f"Empty description for genre '{genre}'"
        assert len(desc) > 20, f"Description too short for '{genre}': {desc}"


# ── describe_clap_features ───────────────────────────────────────────────────


def test_describe_clap_features_basic():
    """Format CLAP results with prefix and scores."""
    analysis = {
        "similarities": [
            ("Tech House", 0.452),
            ("House", 0.423),
            ("Deep House", 0.401),
        ],
        "top1_genre": "Tech House",
        "top1_score": 0.452,
    }
    result = describe_clap_features(analysis)
    assert result.startswith("Audio similarity analysis (CLAP zero-shot")
    assert "Tech House" in result
    assert "0.452" in result
    assert "House" in result


def test_describe_clap_features_empty():
    """Empty analysis returns empty string."""
    assert describe_clap_features({}) == ""
    assert describe_clap_features({"similarities": []}) == ""


def test_describe_clap_features_top10_limit():
    """Only top 10 shown even if more in input."""
    sims = [(f"Genre{i}", 0.5 - i * 0.01) for i in range(15)]
    analysis = {"similarities": sims, "top1_genre": "Genre0", "top1_score": 0.5}
    result = describe_clap_features(analysis)
    assert "Genre9" in result
    assert "Genre10" not in result


# ── Variant registration ─────────────────────────────────────────────────────


def test_clap_variants_registered():
    """nano+CLAP and nano+CLAP+WS are in ALL_VARIANTS."""
    assert "nano+CLAP" in ALL_VARIANTS
    assert "nano+CLAP+WS" in ALL_VARIANTS


# ── Prompt framing ───────────────────────────────────────────────────────────


def test_build_prompt_clap_framing():
    """build_prompt detects CLAP prefix and adds correct system prompt framing."""
    genre_labels = load_genre_labels()
    ctx = {"artist": "Bicep", "title": "Glue", "bpm": "130", "key": "3A"}
    clap_desc = (
        "Audio similarity analysis (CLAP zero-shot — audio matched against genre descriptions):\n"
        "    Melodic Techno              0.450 █████████\n"
        "    Techno                      0.420 ████████\n"
    )
    prompt_json = build_prompt(ctx, genre_labels, audio_desc=clap_desc)
    prompt_data = json.loads(prompt_json)
    system_msg = prompt_data[0]["content"]
    assert "CLAP zero-shot" in system_msg
    assert "audio SOUNDS MORE LIKE" in system_msg


def test_build_prompt_clap_with_ws():
    """CLAP + WS: both signals appear in prompt."""
    genre_labels = load_genre_labels()
    ctx = {"artist": "Test", "title": "Track"}
    clap_desc = "Audio similarity analysis (CLAP zero-shot — audio matched against genre descriptions):\n    House 0.5"
    ws_ctx = "Beatport: House\nDiscogs: Electronic"
    prompt_json = build_prompt(ctx, genre_labels, audio_desc=clap_desc, web_search_context=ws_ctx)
    prompt_data = json.loads(prompt_json)
    user_msg = prompt_data[1]["content"]
    assert "CLAP" in user_msg
    assert "Web search" in user_msg


# ── run_clap_analysis (mocked) ───────────────────────────────────────────────


@patch("ab_test_genre._load_clap_model")
def test_run_clap_analysis_returns_none_when_no_model(mock_load):
    """Returns None when CLAP model can't be loaded."""
    mock_load.return_value = None
    result = run_clap_analysis("/fake/path.mp3", {"House": "four on the floor"})
    assert result is None


@pytest.mark.skipif(
    not _has_torch, reason="torch not installed (optional CLAP dependency)"
)
@patch("ab_test_genre._load_clap_model")
def test_run_clap_analysis_happy_path(mock_load):
    """Happy path with mocked CLAP model."""
    import torch

    # Create mock model
    mock_model = MagicMock()
    # Audio embedding: (1, 512)
    audio_emb = torch.randn(1, 512)
    audio_emb = audio_emb / audio_emb.norm(dim=-1, keepdim=True)
    mock_model.get_audio_embeddings.return_value = audio_emb

    # Text embeddings: (3, 512) for 3 genres
    text_emb = torch.randn(3, 512)
    text_emb = text_emb / text_emb.norm(dim=-1, keepdim=True)
    mock_model.get_text_embeddings.return_value = text_emb

    mock_load.return_value = mock_model

    descriptions = {
        "House": "four on the floor kick drum",
        "Techno": "industrial kick drum",
        "Rock": "distorted guitar riffs",
    }
    result = run_clap_analysis("/fake/path.mp3", descriptions)

    assert result is not None
    assert "similarities" in result
    assert len(result["similarities"]) == 3
    assert result["top1_genre"] in descriptions
    assert 0 <= result["top1_score"] <= 1

    # Verify all genres present in similarities
    sim_genres = [g for g, _ in result["similarities"]]
    assert set(sim_genres) == set(descriptions.keys())

    # Verify sorted descending
    scores = [s for _, s in result["similarities"]]
    assert scores == sorted(scores, reverse=True)
