#!/usr/bin/env python3
"""
A/B Test: Genre classification accuracy with/without Essentia audio features.

Usage:
    .venv/bin/python scripts/ab_test_genre.py              # run full test
    .venv/bin/python scripts/ab_test_genre.py --scan       # just list found tracks
    .venv/bin/python scripts/ab_test_genre.py --essentia   # run Essentia analysis only
    .venv/bin/python scripts/ab_test_genre.py --variants nano+E full+E  # specific variants
    .venv/bin/python scripts/ab_test_genre.py --resume     # skip already-tested tracks

Structure:
    data/ab_test/<Genre Label>/track.mp3
    The folder name IS the expected genre (must match a label in genres.yml).

Results saved to: data/ab_test/results.json (append-safe, keyed by track+variant)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import yaml
import requests

from djlib.config import get_openai_api_key
from djlib.metadata.web_search import create_searcher, search_track_genre

# ── Constants ────────────────────────────────────────────────────────────────

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aiff", ".aif", ".m4a", ".ogg"}
AB_DIR = PROJECT_ROOT / "data" / "ab_test"
RESULTS_FILE = AB_DIR / "results.json"

MODEL_TIERS = {
    "nano": "gpt-5-nano",
    "mini": "gpt-5-mini",
    "full": "gpt-5",
}

TF_MODELS_DIR = PROJECT_ROOT / "models" / "essentia-tf"

ALL_VARIANTS = ["nano", "nano+E", "mini", "mini+E", "full", "full+E",
                "nano+E2", "mini+E2", "full+E2",
                "nano+EI",
                "nano+WS", "nano+EI+WS",
                "nano+D400", "mini+D400", "full+D400"]

# Default run preset: the 4 variants we care about
# nano        = filename metadata (artist/title/version/bpm/key) only
# nano+EI     = filename + Essentia interpreter (sonic description)
# nano+WS     = filename + web search (SearXNG → Beatport, Discogs, etc.)
# nano+EI+WS  = filename + both signals
# NO enrichment metadata (genres_musicbrainz, genres_lastfm, etc.) — the
# winning variant will REPLACE enrich-online, so it must work without it.
#
# v2 = symmetric prompt framing (commit 376e510), replaces old run1/run2/run3
V2_VARIANTS = ["nano", "nano+EI", "nano+WS", "nano+EI+WS"]


# ── Track discovery ──────────────────────────────────────────────────────────

def discover_tracks() -> List[Dict[str, str]]:
    """Discover tracks from gold_labels.json (single source of truth).

    Scans data/ab_test/ recursively for audio files, then filters to only
    those present in gold_labels.json.  Expected genre comes from gold labels,
    NOT from folder names (folders are outdated / unsorted).

    Returns list of dicts with keys: path, expected_genre, filename.
    """
    if not AB_DIR.exists():
        return []

    gold_file = AB_DIR / "gold_labels.json"
    if not gold_file.exists():
        print("  ⚠️  No gold_labels.json — cannot discover tracks")
        return []

    gold = json.loads(gold_file.read_text(encoding="utf-8"))

    # Build filename → path index (recursive scan)
    file_index: Dict[str, Path] = {}
    for p in sorted(AB_DIR.rglob("*")):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS and not p.name.startswith("."):
            file_index[p.name] = p

    tracks = []
    missing = []
    for filename, info in sorted(gold.items()):
        if filename in file_index:
            tracks.append({
                "path": str(file_index[filename]),
                "expected_genre": info["genre"],
                "filename": filename,
            })
        else:
            missing.append(filename)

    if missing:
        print(f"  ⚠️  {len(missing)} gold-labeled tracks not found on disk")

    return tracks


# ── Metadata extraction from filename ────────────────────────────────────────

def extract_metadata_from_filename(filename: str) -> Dict[str, str]:
    """Parse 'Artist - Title (Version).ext' or 'Artist - Title - Version.ext'."""
    stem = Path(filename).stem

    # Remove key/bpm tags like [9A 120], [6A --], [-- --]
    key_bpm_match = re.search(r'\[(\d{1,2}[AB]|--)\s+(--|\d{2,3})\]', stem)
    key = key_bpm_match.group(1) if key_bpm_match and key_bpm_match.group(1) != "--" else ""
    bpm = key_bpm_match.group(2) if key_bpm_match and key_bpm_match.group(2) != "--" else ""
    stem_clean = re.sub(r'\s*\[(?:\d{1,2}[AB]|--)\s+(?:--|\d{2,3})\]\s*', '', stem).strip()

    # Split by ' - '
    parts = [p.strip() for p in stem_clean.split(" - ")]

    artist = parts[0] if len(parts) >= 1 else ""
    title = parts[1] if len(parts) >= 2 else stem_clean
    version = ""

    if len(parts) >= 3:
        version = " - ".join(parts[2:])

    # Extract version from parentheses in title
    paren_match = re.search(r'\(([^)]+(?:Remix|Edit|Mix|Version|Bootleg|Rework|Refix|Flip)[^)]*)\)', title, re.IGNORECASE)
    if paren_match and not version:
        version = paren_match.group(1)
        title = title[:paren_match.start()].strip()

    return {
        "artist": artist,
        "title": title,
        "version": version,
        "bpm": bpm,
        "key": key,
    }


# ── Audio metadata readers ───────────────────────────────────────────────────

# Module-level Rekordbox index: stem → {bpm, key_camelot}. Built once, reused.
_rekordbox_index: Optional[Dict[str, Dict[str, str]]] = None


def _build_rekordbox_index() -> Dict[str, Dict[str, str]]:
    """Build a lookup index from Rekordbox DB: filename stem → {bpm, key}.

    Rekordbox stores BPM as integer × 100 (e.g. 12400 = 124.00 BPM).
    This is the authoritative source for BPM/key — Rekordbox analyzes every
    imported track but doesn't always write BPM back to audio file tags.
    """
    global _rekordbox_index
    if _rekordbox_index is not None:
        return _rekordbox_index

    _rekordbox_index = {}
    try:
        from pyrekordbox import Rekordbox6Database
        db = Rekordbox6Database()
        for content in db.get_content():
            path = content.FolderPath
            if not path:
                continue
            fname = os.path.basename(path)
            # Stem: strip [key bpm] tag and extension for fuzzy matching
            stem = re.sub(r'\s*\[.*?\]\s*', '', os.path.splitext(fname)[0]).strip().lower()
            bpm_raw = content.BPM
            bpm_str = ""
            if bpm_raw and bpm_raw > 0:
                bpm_val = bpm_raw / 100.0
                if 50 <= bpm_val <= 250:
                    bpm_str = str(int(round(bpm_val)))
            _rekordbox_index[stem] = {"bpm": bpm_str}
        print(f"  📀 Rekordbox index: {len(_rekordbox_index)} tracks loaded")
    except Exception as e:
        print(f"  ⚠️  Rekordbox DB not available: {e}")
        _rekordbox_index = {}
    return _rekordbox_index


def read_bpm_from_audio_tag(file_path: str) -> str:
    """Read BPM from audio file tags (ID3/Vorbis/MP4). Rekordbox writes these.

    Returns BPM as string (e.g. '125') or '' if not found.
    Priority: audio tag > Rekordbox DB > empty.
    """
    try:
        import mutagen
        audio = mutagen.File(file_path, easy=True)
        if audio and "bpm" in audio:
            raw = str(audio["bpm"][0]).strip()
            try:
                val = float(raw)
                if 50 <= val <= 250:
                    return str(int(round(val)))
            except ValueError:
                pass
        # Fallback: try raw ID3 TBPM frame
        audio2 = mutagen.File(file_path)
        if audio2 and hasattr(audio2, "tags") and audio2.tags:
            for key in audio2.tags:
                if "BPM" in str(key).upper() or "TBPM" in str(key).upper():
                    raw = str(audio2.tags[key]).strip()
                    try:
                        val = float(raw)
                        if 50 <= val <= 250:
                            return str(int(round(val)))
                    except ValueError:
                        pass
    except Exception:
        pass

    # Fallback: Rekordbox DB (has BPM for tracks where tag wasn't written)
    rb_index = _build_rekordbox_index()
    if rb_index:
        fname = os.path.basename(file_path)
        stem = re.sub(r'\s*\[.*?\]\s*', '', os.path.splitext(fname)[0]).strip().lower()
        entry = rb_index.get(stem, {})
        if entry.get("bpm"):
            return entry["bpm"]

    return ""

def read_key_from_audio_tag(file_path: str) -> str:
    """Read musical key from audio file tags. Rekordbox writes TKEY (ID3) / initialkey (Vorbis).

    Returns key in Camelot notation (e.g. '9A', '3B') or '' if not found.
    Priority: raw TKEY/initialkey tag > easy mode 'initialkey'.
    """
    try:
        import mutagen
        # Try raw tags first — Rekordbox writes TKEY (ID3) or initialkey (Vorbis/FLAC)
        audio = mutagen.File(file_path)
        if audio and hasattr(audio, "tags") and audio.tags:
            for tag_name in audio.tags:
                tag_str = str(tag_name).upper()
                if tag_str in ("TKEY", "INITIALKEY"):
                    raw = str(audio.tags[tag_name]).strip()
                    # Strip list markers from Vorbis tags like "['12A']"
                    if raw.startswith("[") and raw.endswith("]"):
                        raw = raw.strip("[]").strip("' \"")
                    if raw and raw != "--":
                        return raw
        # Fallback: easy mode
        audio2 = mutagen.File(file_path, easy=True)
        if audio2:
            for k in ("initialkey", "key"):
                if k in audio2 and audio2[k][0].strip():
                    return audio2[k][0].strip()
    except Exception:
        pass
    return ""


def run_essentia_analysis(file_path: str, cache_only: bool = False) -> Optional[Dict[str, Any]]:
    """Run Essentia analysis on a file, using cache if available.

    Args:
        file_path: Path to audio file.
        cache_only: If True, only return cached results (no new analysis).
                    Use this to avoid deadlocks on problematic files.
    """
    try:
        from djlib.audio.cache import compute_audio_id, get_analysis, upsert_analysis
        from djlib.audio.essentia_backend import analyze
    except ImportError as e:
        print(f"  ⚠ Essentia not available: {e}")
        return None

    p = Path(file_path)
    if not p.exists():
        return None

    audio_id = compute_audio_id(p)
    cached = get_analysis(audio_id)
    if cached:
        return cached

    if cache_only:
        return None

    print(f"  🔬 Analyzing ({p.name})...", end=" ", flush=True)
    t0 = time.time()
    features = analyze(str(p))
    elapsed = time.time() - t0
    print(f"{elapsed:.1f}s")

    if features:
        upsert_analysis(audio_id, features)

    return features


def describe_audio_features(features: Dict[str, Any]) -> str:
    """Translate Essentia features to natural language description (v1 — biased labels)."""
    lines: List[str] = []

    def fmt(val: Any, decimals: int = 2) -> str:
        if isinstance(val, float):
            return f"{val:.{decimals}f}"
        return str(val)

    v = features.get("onset_rate")
    if v is not None:
        label = (
            "very dense percussion" if v > 10
            else "dense, active percussion" if v > 7
            else "moderate percussion density" if v > 4
            else "sparse, minimal percussion"
        )
        lines.append(f"Onset rate: {fmt(v)} ({label})")

    v = features.get("energy")
    if v is not None:
        label = (
            "very high energy (driving, intense)" if v > 0.8
            else "high energy" if v > 0.6
            else "moderate energy" if v > 0.4
            else "moderate-low energy" if v > 0.03
            else "low energy (chill, ambient)"
        )
        lines.append(f"Energy: {fmt(v)} ({label})")

    v = features.get("danceability")
    if v is not None:
        label = (
            "very danceable" if v > 1.5
            else "danceable" if v > 1.0
            else "moderately danceable" if v > 0.5
            else "low danceability"
        )
        lines.append(f"Danceability: {fmt(v)} ({label})")

    v = features.get("spec_centroid")
    if v is not None:
        label = (
            "bright, sharp tone (hi-hats, synths prominent)" if v > 3000
            else "mid-bright tone" if v > 2000
            else "warm, balanced tone (organic instruments)" if v > 1000
            else "deep, dark tonal character (sub-bass, warm pads)"
        )
        lines.append(f"Spectral centroid: {fmt(v, 0)} Hz ({label})")

    v = features.get("spec_rolloff")
    if v is not None:
        label = (
            "high-frequency content dominant (crisp, airy)" if v > 8000
            else "mid-high frequency focus" if v > 4000
            else "mid-range focus" if v > 2000
            else "warm, low-mid focused (deep bass, soft highs)" if v > 1000
            else "very bass-heavy, dark production"
        )
        lines.append(f"Spectral rolloff: {fmt(v, 0)} Hz ({label})")

    v = features.get("dyn_complex")
    if v is not None:
        label = (
            "very dynamic, organic, live-feeling sound" if v > 6
            else "dynamic, textured production" if v > 4
            else "moderately dynamic" if v > 2.5
            else "compressed, flat, mechanical dynamics"
        )
        lines.append(f"Dynamic complexity: {fmt(v)} ({label})")

    v = features.get("chords_changes_rate")
    if v is not None:
        label = (
            "frequent chord changes (harmonically rich, musical)" if v > 0.3
            else "moderate harmonic changes" if v > 0.08
            else "minimal chord changes (groove-driven, percussive, repetitive)"
        )
        lines.append(f"Chord changes rate: {fmt(v)} ({label})")

    v = features.get("tuning_diatonic_strength")
    if v is not None:
        label = (
            "strongly tonal/melodic" if v > 0.7
            else "moderately tonal" if v > 0.5
            else "weak tonality (more rhythmic/percussive than melodic)"
        )
        lines.append(f"Diatonic strength: {fmt(v)} ({label})")

    v = features.get("zero_crossing_rate")
    if v is not None:
        label = (
            "high noise/percussion texture" if v > 0.1
            else "balanced texture" if v > 0.05
            else "smooth, tonal, clean texture"
        )
        lines.append(f"Zero crossing rate: {fmt(v, 4)} ({label})")

    if not lines:
        return ""

    return (
        "Audio characteristics (from computational analysis of the actual audio waveform — "
        "THIS IS OBJECTIVE SONIC DATA, not metadata):\n"
        + "\n".join(f"  * {l}" for l in lines)
    )


def describe_audio_features_v2(features: Dict[str, Any]) -> str:
    """Translate Essentia features to concise, unbiased description (v2).

    Key changes vs v1:
    - Raw numeric values without interpretive labels (let LLM decide)
    - No BPM from Essentia (Rekordbox is more reliable)
    - MFCC timbral summary (acoustic vs synthetic fingerprint)
    - Spectral variability (std) as production style indicator
    - Removed noisy features (zero_crossing_rate, diatonic_strength)
    """
    parts: List[str] = []

    def fv(key: str, decimals: int = 2) -> Optional[str]:
        v = features.get(key)
        if v is None:
            return None
        if isinstance(v, float):
            return f"{v:.{decimals}f}"
        return str(v)

    # Core rhythm & energy features — raw values
    onset = fv("onset_rate")
    energy = fv("energy", 3)
    dance = fv("danceability")
    dyn = fv("dyn_complex")
    if any([onset, energy, dance, dyn]):
        items = []
        if onset:
            items.append(f"onset_rate={onset}")
        if energy:
            items.append(f"energy={energy}")
        if dance:
            items.append(f"danceability={dance}")
        if dyn:
            items.append(f"dynamic_complexity={dyn}")
        parts.append("Rhythm/Energy: " + ", ".join(items))

    # Spectral features — brightness + variability
    centroid = fv("spec_centroid", 0)
    centroid_std = fv("spec_centroid_std", 0)
    rolloff = fv("spec_rolloff", 0)
    rolloff_std = fv("spec_rolloff_std", 0)
    if any([centroid, rolloff]):
        items = []
        if centroid:
            s = f"centroid={centroid}Hz"
            if centroid_std:
                s += f" (std={centroid_std})"
            items.append(s)
        if rolloff:
            s = f"rolloff={rolloff}Hz"
            if rolloff_std:
                s += f" (std={rolloff_std})"
            items.append(s)
        parts.append("Spectral: " + ", ".join(items))

    # Harmonic features
    chords = fv("chords_changes_rate", 3)
    lufs = fv("lufs", 1)
    if any([chords, lufs]):
        items = []
        if chords:
            items.append(f"chord_change_rate={chords}")
        if lufs:
            items.append(f"loudness={lufs} LUFS")
        parts.append("Harmonic: " + ", ".join(items))

    # MFCC timbral summary — first 5 coefficients capture instrument timbre
    # MFCC0 = overall energy, MFCC1-4 = spectral shape (acoustic fingerprint)
    mfccs = []
    for i in range(5):
        v = features.get(f"mfcc_{i}")
        if v is not None:
            mfccs.append(f"{v:.0f}")
    mfcc_stds = []
    for i in range(5):
        v = features.get(f"mfcc_std_{i}")
        if v is not None:
            mfcc_stds.append(f"{v:.0f}")

    kurtosis = features.get("mfcc_kurtosis_mean")
    skew = features.get("mfcc_skew_mean")

    if mfccs:
        s = f"Timbre (MFCC 0-4): [{', '.join(mfccs)}]"
        if mfcc_stds:
            s += f", std=[{', '.join(mfcc_stds)}]"
        if kurtosis is not None:
            s += f", kurtosis={kurtosis:.1f}"
        if skew is not None:
            s += f", skew={skew:.2f}"
        parts.append(s)

    # HFC — high frequency content (distinguishes bright electronic vs warm acoustic)
    hfc = fv("hfc_mean", 1)
    hfc_std = fv("hfc_std", 1)
    if hfc:
        s = f"High-freq content: mean={hfc}"
        if hfc_std:
            s += f", std={hfc_std}"
        parts.append(s)

    if not parts:
        return ""

    return (
        "Audio analysis (Essentia — computational signal features, no BPM):\n"
        + "\n".join(f"  {p}" for p in parts)
    )


# ── Essentia interpreter (LLM-based) ───────────────────────────────────────

def _call_openai_text(api_key: str, messages: List[Dict[str, str]], model: str) -> str:
    """Call OpenAI Responses API and return raw text (no JSON forcing)."""
    resp = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "input": messages,
            "temperature": 0,
            "max_output_tokens": 1024,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    text = ""
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    text = c.get("text", "")
    return text


def _prepare_interpreter_input(features: Dict[str, Any], bpm: str = "", key: str = "") -> str:
    """Extract curated ~20 features from Essentia output and format as text for interpreter.

    SA-3/DE-1 fix: BPM and Key are NOT passed to interpreter.
    They are already in the classifier prompt as metadata fields and in the BPM guide.
    Passing them here caused triple BPM signal (metadata + EI + BPM guide).
    The interpreter should describe AUDIO CHARACTER from Essentia features only,
    while BPM/Key reach the classifier through their own metadata lines.
    """
    lines: List[str] = []

    # BPM and key intentionally excluded — they reach the classifier separately
    # as metadata fields. Including them here caused triple BPM signal overweighting.

    def fv(k: str, decimals: int = 2) -> Optional[str]:
        v = features.get(k)
        if v is None:
            return None
        return f"{v:.{decimals}f}" if isinstance(v, float) else str(v)

    # Tempo / Rhythm
    for k, d in [("onset_rate", 2), ("bpm_conf", 2), ("danceability", 2), ("dyn_complex", 2)]:
        val = fv(k, d)
        if val:
            lines.append(f"{k}: {val}")

    # Energy
    for k, d in [("energy", 3), ("lufs", 1), ("spec_flux_mean", 3)]:
        val = fv(k, d)
        if val:
            unit = " LUFS" if k == "lufs" else ""
            lines.append(f"{k}: {val}{unit}")

    # Timbre — spectral
    for k, d, unit in [
        ("spec_centroid", 0, " Hz"), ("spec_centroid_std", 0, ""),
        ("spec_rolloff", 0, " Hz"), ("spec_rolloff_std", 0, ""),
        ("hfc_mean", 1, ""),
    ]:
        val = fv(k, d)
        if val:
            lines.append(f"{k}: {val}{unit}")

    # Timbre — MFCC 0-4
    for i in range(5):
        v = features.get(f"mfcc_{i}")
        if v is not None:
            lines.append(f"mfcc_{i}: {v:.0f}")
    kurtosis = features.get("mfcc_kurtosis_mean")
    skew = features.get("mfcc_skew_mean")
    if kurtosis is not None:
        lines.append(f"mfcc_kurtosis_mean: {kurtosis:.1f}")
    if skew is not None:
        lines.append(f"mfcc_skew_mean: {skew:.2f}")

    # Texture
    for k, d in [("spec_flatness_mean", 3), ("zero_crossing_rate", 3)]:
        val = fv(k, d)
        if val:
            lines.append(f"{k}: {val}")

    # Harmony
    for k, d in [("chords_changes_rate", 3), ("tuning_diatonic_strength", 3)]:
        val = fv(k, d)
        if val:
            lines.append(f"{k}: {val}")

    return "\n".join(lines)


def interpret_audio_features(
    features: Dict[str, Any],
    bpm: str,
    key: str,
    api_key: str,
    model: str = "gpt-5-nano",
) -> str:
    """Use LLM to interpret raw Essentia features into 6 semantic descriptors.

    Essentia Interpreter step: a separate LLM call that converts objective
    numerical audio features into plain-text sonic descriptions.
    Output is injected into the genre classifier prompt.

    Returns formatted text with Tempo, Rhythm, Energy, Timbre, Texture, Harmony.
    Returns empty string on failure.
    """
    feature_input = _prepare_interpreter_input(features, bpm, key)
    if not feature_input:
        return ""

    # Load interpreter prompt template
    prompt_path = AB_DIR / "prompts" / "interpreter_essentia.md"
    if not prompt_path.exists():
        print(f"  ⚠️  Interpreter prompt not found: {prompt_path}")
        return ""
    prompt_template = prompt_path.read_text(encoding="utf-8")

    # Load audio features reference sheet and inject
    ref_path = AB_DIR / "audio_features.md"
    if not ref_path.exists():
        print(f"  ⚠️  Audio features reference not found: {ref_path}")
        return ""
    reference = ref_path.read_text(encoding="utf-8")
    system_prompt = prompt_template.replace("{audio_features_reference}", reference)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Interpret these audio features:\n\n{feature_input}"},
    ]

    try:
        text = _call_openai_text(api_key, messages, model)
        # Validate we got at least 4/6 descriptors
        expected_keys = ["Tempo:", "Rhythm:", "Energy:", "Timbre:", "Texture:", "Harmony:"]
        found = sum(1 for k in expected_keys if k in text)
        if found < 4:
            print(f"  ⚠️  Interpreter returned only {found}/6 descriptors")
        # Wrap with standard prefix for build_prompt() detection
        return "Audio character (Essentia → interpreted):\n" + text.strip()
    except Exception as e:
        print(f"  ❌ Interpreter API error: {e}")
        return ""


# ── Discogs400 deep learning analysis ───────────────────────────────────────

# Module-level singletons for TF models (loaded once, reused across tracks)
_effnet_model = None
_genre_model = None
_binary_models: Dict[str, Any] = {}
_genre_labels: Optional[List[str]] = None


def _load_effnet():
    """Load EffNet embedding extractor (singleton)."""
    global _effnet_model
    if _effnet_model is not None:
        return _effnet_model
    import essentia.standard as es
    model_path = str(TF_MODELS_DIR / "discogs-effnet-bs64-1.pb")
    _effnet_model = es.TensorflowPredictEffnetDiscogs(
        graphFilename=model_path,
        output="PartitionedCall:1",
    )
    return _effnet_model


def _load_genre_model():
    """Load Discogs400 genre classification head (singleton)."""
    global _genre_model, _genre_labels
    if _genre_model is not None:
        return _genre_model, _genre_labels
    import essentia.standard as es
    model_path = str(TF_MODELS_DIR / "genre_discogs400-discogs-effnet-1.pb")
    meta_path = TF_MODELS_DIR / "genre_discogs400-discogs-effnet-1.json"
    _genre_model = es.TensorflowPredict2D(
        graphFilename=model_path,
        input="serving_default_model_Placeholder",
        output="PartitionedCall:0",
    )
    with open(meta_path) as f:
        _genre_labels = json.load(f)["classes"]
    return _genre_model, _genre_labels


def _load_binary_model(name: str):
    """Load a binary classification head (singleton per name)."""
    if name in _binary_models:
        return _binary_models[name]
    import essentia.standard as es
    model_path = str(TF_MODELS_DIR / f"{name}-discogs-effnet-1.pb")
    meta_path = TF_MODELS_DIR / f"{name}-discogs-effnet-1.json"
    if not Path(model_path).exists():
        return None, None
    model = es.TensorflowPredict2D(
        graphFilename=model_path,
        input="model/Placeholder",
        output="model/Softmax",
    )
    with open(meta_path) as f:
        classes = json.load(f)["classes"]
    _binary_models[name] = (model, classes)
    return model, classes


def run_discogs400_analysis(file_path: str) -> Optional[Dict[str, Any]]:
    """Run Discogs400 genre + mood/danceability analysis on a track.

    Returns dict with:
        genre_predictions: list of (label, score) top-15
        danceability: float 0-1
        moods: dict of mood_name -> score
        voice_instrumental: str 'voice' or 'instrumental'
        embeddings_shape: tuple (for debugging)
    """
    import essentia.standard as es

    try:
        # Load audio at 16kHz mono
        audio = es.MonoLoader(filename=file_path, sampleRate=16000)()
        # Use middle 30 seconds
        mid = len(audio) // 2
        start = max(0, mid - 16000 * 15)
        audio_clip = audio[start : start + 16000 * 30]

        # Extract EffNet embeddings (shared across all heads)
        effnet = _load_effnet()
        embeddings = effnet(audio_clip)

        result: Dict[str, Any] = {
            "embeddings_shape": list(embeddings.shape),
        }

        # Genre classification (Discogs400)
        genre_model, labels = _load_genre_model()
        predictions = genre_model(embeddings)
        avg_preds = np.mean(predictions, axis=0)
        top_indices = np.argsort(avg_preds)[::-1][:15]
        result["genre_predictions"] = [
            (labels[i], round(float(avg_preds[i]), 4))
            for i in top_indices
        ]

        # Binary classification heads
        binary_heads = [
            "danceability", "mood_happy", "mood_sad", "mood_aggressive",
            "mood_relaxed", "mood_party", "mood_electronic", "mood_acoustic",
            "voice_instrumental",
        ]
        moods = {}
        for head_name in binary_heads:
            model, classes = _load_binary_model(head_name)
            if model is None:
                continue
            preds = model(embeddings)
            avg = np.mean(preds, axis=0)
            if head_name == "voice_instrumental":
                # classes: ['instrumental', 'voice']
                result["voice_instrumental"] = classes[int(np.argmax(avg))]
                result["voice_score"] = round(float(max(avg)), 3)
            elif head_name == "danceability":
                # classes: ['danceable', 'not_danceable']
                result["danceability"] = round(float(avg[0]), 3)
            else:
                # Mood heads: take the positive class score
                # Classes vary: some have positive first, some second
                positive_idx = 0
                for idx, cls in enumerate(classes):
                    if not cls.startswith("non_") and not cls.startswith("not_"):
                        positive_idx = idx
                        break
                mood_short = head_name.replace("mood_", "")
                moods[mood_short] = round(float(avg[positive_idx]), 3)

        result["moods"] = moods
        return result

    except Exception as e:
        print(f"  ⚠ Discogs400 error: {e}")
        return None


def describe_discogs400_features(analysis: Dict[str, Any]) -> str:
    """Format Discogs400 analysis results as text for GPT prompt."""
    parts: List[str] = []

    # Genre predictions (top 10)
    genre_preds = analysis.get("genre_predictions", [])
    if genre_preds:
        genre_lines = []
        for label, score in genre_preds[:10]:
            # Format: "Electronic---House" -> "House (Electronic)" with score
            if "---" in label:
                category, subgenre = label.split("---", 1)
                genre_lines.append(f"{subgenre} [{category}] ({score:.3f})")
            else:
                genre_lines.append(f"{label} ({score:.3f})")
        parts.append("Audio genre analysis (Discogs400 deep learning, from actual sound):\n"
                     + "\n".join(f"    {g}" for g in genre_lines))

    # Danceability
    dance = analysis.get("danceability")
    if dance is not None:
        parts.append(f"  Danceability: {dance:.2f}")

    # Voice/Instrumental
    vi = analysis.get("voice_instrumental")
    if vi:
        parts.append(f"  Voice/Instrumental: {vi} ({analysis.get('voice_score', 0):.2f})")

    # Moods
    moods = analysis.get("moods", {})
    if moods:
        # Sort by score descending, show top ones
        sorted_moods = sorted(moods.items(), key=lambda x: x[1], reverse=True)
        mood_strs = [f"{name}={score:.2f}" for name, score in sorted_moods]
        parts.append(f"  Mood profile: {', '.join(mood_strs)}")

    if not parts:
        return ""

    return "\n".join(parts)


# ── Prompt builder ───────────────────────────────────────────────────────────

def load_genre_labels() -> List[str]:
    """Load genre labels from genres.yml."""
    with open(PROJECT_ROOT / "genres.yml") as f:
        data = yaml.safe_load(f)
    return [v["label"] for v in data.values() if isinstance(v, dict) and "label" in v]


def build_prompt(ctx: Dict[str, str], genre_labels: List[str], audio_desc: str = "",
                 web_search_context: str = "") -> str:
    """Build genre classification prompt for AB test.

    IMPORTANT: In AB test mode, ctx contains ONLY filename-derived metadata
    (artist, title, version, bpm, key) + folder hint. Enrichment fields
    (genres_musicbrainz, genres_lastfm, genres_soundcloud, genres_beatport)
    are INTENTIONALLY EXCLUDED — the winning variant will replace enrich-online.
    The genres_* blocks below exist for compatibility with server.py prompt
    structure but should never fire in AB test context.
    """
    genre_list = ", ".join(genre_labels)

    parts = []
    if ctx.get("artist"):
        parts.append(f"Artist: {ctx['artist']}")
    if ctx.get("title"):
        parts.append(f"Title: {ctx['title']}")
    if ctx.get("version"):
        parts.append(f"Version/Remix: {ctx['version']}")
    if ctx.get("bpm"):
        parts.append(f"BPM: {ctx['bpm']}")
    if ctx.get("key"):
        parts.append(f"Key: {ctx['key']}")
    # NOTE: folder is NOT included in prompt — it leaked expected_genre (#11).
    # Removed to ensure clean AB test measurement.
    # Enrichment metadata — intentionally excluded in AB test.
    # These blocks exist for server.py compatibility only.
    if ctx.get("genres_musicbrainz"):
        parts.append(f"MusicBrainz genres: {ctx['genres_musicbrainz']}")
    if ctx.get("genres_lastfm"):
        parts.append(f"Last.fm genres: {ctx['genres_lastfm']}")
    if ctx.get("genres_soundcloud"):
        parts.append(f"SoundCloud tags: {ctx['genres_soundcloud']}")
    if ctx.get("genres_beatport"):
        parts.append(f"Beatport genre: {ctx['genres_beatport']}")

    if audio_desc:
        parts.append("")
        parts.append(audio_desc)

    if web_search_context and not web_search_context.startswith("(No web search"):
        parts.append("")
        parts.append(f"Web search results:\n{web_search_context}")

    track_info = "\n".join(parts)

    version_str = ctx.get("version", "")
    is_remix = bool(re.search(
        r'\b(?:remix|edit|bootleg|rework|refix|mashup|flip)\b',
        version_str, re.IGNORECASE,
    ))

    remix_instruction = ""
    if is_remix:
        remix_instruction = (
            "\n\nCRITICAL — REMIX/EDIT CLASSIFICATION RULE:\n"
            "This track is a REMIX or EDIT. You MUST classify it by the REMIX STYLE, "
            "NOT by the original track's genre. The remixer transforms the track into a new genre.\n"
            "The REMIXER/EDITOR identity is the strongest signal for determining the specific "
            "dance subgenre. Use your knowledge of the remixer's scene and production style.\n"
            "The original artist's genre is almost always WRONG for the remix."
        )

    bpm_guide = (
        "\n\nBPM genre ranges (approximate — ONLY for electronic/dance productions, NOT for rock/pop/hip-hop/etc.):\n"
        "70-100: Hip-Hop, R&B, Reggaeton, Dancehall\n"
        "100-115: UK Garage, Afrobeats\n"
        "115-126: Deep House\n"
        "116-128: Afro House\n"
        "120-128: House, Tech House\n"
        "124-132: Melodic Techno, Progressive House\n"
        "128-140: Techno, Hard Techno, Trance\n"
        "140-150: Psytrance\n"
        "150-180: Drum & Bass\n"
        "\n"
        "WARNING: BPM is UNRELIABLE for non-electronic genres. A rock song at 153 BPM is still Rock, "
        "NOT Drum & Bass. When the artist is clearly non-electronic, IGNORE the BPM ranges above.\n"
        "BPM values >140 may be double-time detection errors (actual BPM = value/2). "
        "Consider this for hip-hop, reggaeton, rock, pop, and other non-fast genres.\n"
        "\n"
        "House subgenre hints (when BPM 118-130 and genre family is House):\n"
        "- Deep House: melodic, warm, often vocal, jazzy/soulful chords, slower groove\n"
        "- Tech House: groovy, minimal, repetitive, percussive, functional DJ tool\n"
        "- Afro House: African percussion patterns, tribal rhythms, organic textures\n"
        "- Disco House: disco samples, funky basslines, filtered disco loops\n"
        "- House (generic): only when no subgenre signal is strong enough"
    )

    audio_signal_line = ""
    if audio_desc and audio_desc.startswith("Audio genre analysis (Discogs400"):
        # D400 format — deep learning audio analysis
        audio_signal_line = (
            "\n* AUDIO ANALYSIS (Discogs400 deep learning) — genre predictions from a neural network "
            "trained on 4M tracks, analyzing the ACTUAL SOUND of this specific recording. "
            "The scores show how much the audio resembles each genre sonically. "
            "Use this to disambiguate subgenres (e.g., House vs Deep House vs Afro House vs Tech House). "
            "When the audio analysis and metadata agree, be confident. "
            "When they differ, consider that remixes transform the sound — trust the audio genre for the remix's actual style."
        )
    elif audio_desc and audio_desc.startswith("Audio character (Essentia"):
        # EI format — LLM-interpreted sonic description (no genre names, objective)
        # SK-1 fix: symmetric framing with WS — both use "inform the genre decision"
        audio_signal_line = (
            "\n* AUDIO CHARACTER (Essentia → interpreted) — objective sonic description derived "
            "from computational audio analysis: what this track SOUNDS LIKE (tempo feel, rhythm "
            "character, energy, brightness, texture, harmony) WITHOUT naming genres. "
            "Use this to inform the genre decision, especially to distinguish between similar subgenres."
        )
    elif audio_desc and not audio_desc.startswith("Audio analysis (Essentia"):
        # v1 format — aggressive framing
        audio_signal_line = (
            "\n* AUDIO ANALYSIS (Essentia) — objective sonic characteristics extracted from the actual "
            "audio waveform. This tells you HOW the track SOUNDS. Use it to distinguish subgenres."
        )
    elif audio_desc:
        # v2 format — neutral framing
        audio_signal_line = (
            "\n* Audio signal features (Essentia) — supplementary sonic data, use as tiebreaker "
            "when metadata signals are ambiguous. Artist identity and BPM remain stronger signals."
        )

    web_search_signal = ""
    if web_search_context and not web_search_context.startswith("(No web search"):
        # SK-1 fix: symmetric framing with EI — both use "inform the genre decision"
        # DJ-2 fix: Beatport "strong evidence" → "reliable indicator" + taxonomy caveat
        # v3 fix: remix leak warning — WS may find remixes of the original track
        remix_leak_warning = ""
        if not is_remix:
            remix_leak_warning = (
                " IMPORTANT: This track is NOT a remix. If web results mention remixes or "
                "alternative versions, classify based on the ORIGINAL track's genre, not the remix's."
            )
        web_search_signal = (
            "\n3. WEB SEARCH RESULTS — real-time search snippets from music databases and DJ sites. "
            "Genre labels from Beatport, Discogs, and other sources are a reliable indicator but may "
            "reflect the source's own taxonomy (not ours). Use all web results to inform the genre decision."
            f"{remix_leak_warning}\n"
        )

    # BPM signal number depends on whether WS is present
    bpm_signal_num = "4" if web_search_signal else "3"

    system_prompt = (
        f"You are an expert DJ music classifier. Classify tracks into exactly ONE of these genres:\n"
        f"{genre_list}\n\n"
        # v3: explicit signal priority hierarchy
        # PE-2 fix: "ALL" → "the following" (truthful for every variant)
        # TX-2 fix: "prefer the most specific subgenre" rule
        f"Use the following signals IN PRIORITY ORDER to determine the MOST SPECIFIC matching genre "
        f"(always prefer a specific subgenre over a broad parent, e.g. Tech House over House):\n"
        f"\n"
        f"1. ARTIST/TITLE KNOWLEDGE — your world knowledge of the artist's known genre is the "
        f"STRONGEST signal. If you recognize the artist, their genre almost always determines "
        f"the classification. A track by a known rock band is Rock regardless of BPM.\n"
        f"2. REMIXER/EDITOR identity — for remixes, the remixer's scene/style overrides the original artist's genre.\n"
        f"{web_search_signal}"
        f"{bpm_signal_num}. BPM — supplementary range indicator, mainly useful for ELECTRONIC genres only."
        f"{audio_signal_line}"
        f"{remix_instruction}"
        f"{bpm_guide}\n\n"
        # v3: non-electronic awareness
        f"CRITICAL: Many tracks are non-electronic (Rock, Pop, Hip-Hop, R&B, Soul, Funk, "
        f"Reggae, Punk, Indie Pop, Synthpop, etc.). For these genres, artist identity is the "
        f"primary signal. Do NOT let BPM override artist knowledge — a hip-hop track at 170 BPM "
        f"is still Hip-Hop (BPM detector may have measured double-time).\n\n"
        # PE-5 fix: fallback rule for weak/missing signals
        f"If signals are weak or conflicting, choose the broadest matching genre family "
        f"and set confidence below 0.5.\n\n"
        # PE-4 fix: confidence calibration guidance
        # PE-3 fix: structured reasoning requirement
        f"Respond with JSON: {{\"genre\": \"...\", \"confidence\": 0.0-1.0, \"reasoning\": \"...\"}}\n"
        f"Confidence guide: 0.9+ only when multiple signals agree, 0.7-0.9 single strong signal, "
        f"<0.7 uncertain or conflicting. Confidence MUST be below 0.5 if classification relies solely on BPM.\n"
        f"In reasoning, briefly list each signal used and what it indicated."
    )

    user_prompt = f"Classify this track:\n\n{track_info}"

    return json.dumps([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ])


# ── OpenAI caller ────────────────────────────────────────────────────────────

def call_openai(api_key: str, prompt_json: str, model: str) -> Dict[str, Any]:
    """Call OpenAI Responses API and parse JSON result."""
    messages = json.loads(prompt_json)

    input_msgs = []
    for m in messages:
        input_msgs.append({"role": m["role"], "content": m["content"]})

    t0 = time.time()
    resp = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "input": input_msgs,
            "text": {"format": {"type": "json_object"}},
            "max_output_tokens": 4096,
        },
        timeout=60,
    )
    elapsed = time.time() - t0
    resp.raise_for_status()
    data = resp.json()

    # Extract text from response
    text = ""
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    text = c.get("text", "")

    # Parse usage
    usage = data.get("usage", {})
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)

    # Parse JSON from text
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON from markdown fences
        match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if match:
            result = json.loads(match.group(1))
        else:
            result = {"genre": "PARSE_ERROR", "confidence": 0, "reasoning": text[:200]}

    result["_elapsed"] = round(elapsed, 2)
    result["_input_tokens"] = input_tokens
    result["_output_tokens"] = output_tokens
    result["_model"] = model

    return result


# ── Results persistence ──────────────────────────────────────────────────────

def load_results() -> Dict[str, Any]:
    """Load existing results from JSON file."""
    if RESULTS_FILE.exists():
        with open(RESULTS_FILE) as f:
            return json.load(f)
    return {"tracks": {}, "runs": []}


def save_results(results: Dict[str, Any]) -> None:
    """Save results to JSON file."""
    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


# ── Main test runner ─────────────────────────────────────────────────────────

def run_scan():
    """Just list discovered tracks without running anything."""
    tracks = discover_tracks()
    if not tracks:
        print("❌ No audio files found in data/ab_test/<genre>/")
        print(f"   Place audio files in genre folders under: {AB_DIR}")
        return

    genre_counts: Dict[str, int] = {}
    for t in tracks:
        g = t["expected_genre"]
        genre_counts[g] = genre_counts.get(g, 0) + 1

    print(f"\n📂 Found {len(tracks)} tracks in {len(genre_counts)} genres:\n")
    for genre, count in sorted(genre_counts.items()):
        print(f"  {genre:25s} {count} track(s)")
        for t in tracks:
            if t["expected_genre"] == genre:
                meta = extract_metadata_from_filename(t["filename"])
                print(f"    → {meta['artist']} - {meta['title']}", end="")
                if meta["version"]:
                    print(f" ({meta['version']})", end="")
                print()
    print()


def run_essentia_only():
    """Run Essentia analysis on all tracks (no API calls)."""
    tracks = discover_tracks()
    if not tracks:
        print("❌ No tracks found")
        return

    print(f"\n🔬 Running Essentia on {len(tracks)} tracks...\n")
    ok, fail = 0, 0
    for t in tracks:
        features = run_essentia_analysis(t["path"])
        if features:
            ok += 1
            desc = describe_audio_features(features)
            print(f"  ✅ {t['filename'][:60]}")
        else:
            fail += 1
            print(f"  ❌ {t['filename'][:60]}")

    print(f"\n{'='*60}")
    print(f"Essentia: {ok} OK, {fail} failed, {ok+fail} total")


def run_ab_test(variants: List[str], resume: bool = False, concurrency: int = 16):
    """Run the full A/B test with parallel OpenAI API calls.

    Args:
        variants: list of variant names to test
        resume: skip already-tested track+variant pairs
        concurrency: max parallel API calls (default 16 ≈ 4 tracks × 4 variants)
    """
    api_key = get_openai_api_key()
    if not api_key:
        print("❌ No OpenAI API key. Add openai_api_key to config.local.yml")
        sys.exit(1)

    tracks = discover_tracks()
    if not tracks:
        print("❌ No gold-labeled tracks found in data/ab_test/")
        print("   Ensure gold_labels.json exists and audio files are present.")
        sys.exit(1)

    genre_labels = load_genre_labels()
    results = load_results() if resume else {"tracks": {}, "runs": []}

    run_meta = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "variants": variants,
        "track_count": len(tracks),
    }

    print(f"\n🧪 A/B Test: {len(tracks)} tracks × {len(variants)} variants = {len(tracks) * len(variants)} API calls\n")
    print(f"Variants: {', '.join(variants)}")
    print(f"{'='*70}\n")

    # Pre-run Essentia if needed
    essentia_variants = [v for v in variants if "+E" in v and "+E2" not in v and "+EI" not in v and "+D400" not in v]
    essentia_cache: Dict[str, str] = {}  # path -> audio_description

    essentia_v2_variants = [v for v in variants if "+E2" in v]
    essentia_cache_v2: Dict[str, str] = {}  # path -> audio_description v2

    essentia_interp_variants = [v for v in variants if "+EI" in v]
    essentia_interp_cache: Dict[str, str] = {}  # path -> interpreted description
    essentia_raw_cache: Dict[str, Dict[str, Any]] = {}  # path -> raw features (for interpreter)

    needs_essentia = essentia_variants or essentia_v2_variants or essentia_interp_variants

    if needs_essentia:
        print("🔬 Loading cached Essentia audio features...\n")
        essentia_ok = essentia_miss = 0
        for t in tracks:
            features = run_essentia_analysis(t["path"], cache_only=True)
            if features:
                if essentia_variants:
                    essentia_cache[t["path"]] = describe_audio_features(features)
                if essentia_v2_variants:
                    essentia_cache_v2[t["path"]] = describe_audio_features_v2(features)
                if essentia_interp_variants:
                    essentia_raw_cache[t["path"]] = features
                essentia_ok += 1
            else:
                essentia_cache[t["path"]] = ""
                essentia_cache_v2[t["path"]] = ""
                essentia_miss += 1
        print(f"   Essentia: {essentia_ok} OK, {essentia_miss} missing (run --essentia first)\n")

    # Pre-run Essentia interpreter (LLM calls) if needed
    if essentia_interp_variants and essentia_raw_cache:
        print("🧠 Running Essentia interpreter (LLM-based)...\n")
        interp_ok = interp_fail = 0
        for i, t in enumerate(tracks):
            features = essentia_raw_cache.get(t["path"])
            if not features:
                essentia_interp_cache[t["path"]] = ""
                interp_fail += 1
                print(f"  [{i+1}/{len(tracks)}] {t['filename'][:55]}... ❌ no features")
                continue

            meta = extract_metadata_from_filename(t["filename"])
            tag_bpm = read_bpm_from_audio_tag(t["path"])
            bpm = tag_bpm or meta.get("bpm", "")
            key = features.get("key_camelot", "")

            print(f"  [{i+1}/{len(tracks)}] {t['filename'][:55]}...", end=" ", flush=True)
            t0 = time.time()
            desc = interpret_audio_features(features, bpm, key, api_key)
            elapsed = time.time() - t0

            if desc:
                essentia_interp_cache[t["path"]] = desc
                interp_ok += 1
                # Show first descriptor as preview
                first_line = desc.split("\n")[1] if "\n" in desc else desc[:60]
                print(f"✅ {elapsed:.1f}s → {first_line[:60]}")
            else:
                essentia_interp_cache[t["path"]] = ""
                interp_fail += 1
                print(f"❌ {elapsed:.1f}s")
            time.sleep(0.3)  # Rate limit
        print(f"\n   Interpreter: {interp_ok} OK, {interp_fail} failed\n")

    # Pre-run Discogs400 deep learning analysis if needed
    discogs_variants = [v for v in variants if "+D400" in v]
    discogs_cache: Dict[str, str] = {}  # path -> formatted description

    if discogs_variants:
        print("🧠 Running Discogs400 deep learning analysis...\n")
        d400_ok = d400_fail = 0
        for i, t in enumerate(tracks):
            print(f"  [{i+1}/{len(tracks)}] {t['filename'][:55]}...", end=" ", flush=True)
            t0 = time.time()
            analysis = run_discogs400_analysis(t["path"])
            elapsed = time.time() - t0
            if analysis:
                discogs_cache[t["path"]] = describe_discogs400_features(analysis)
                top1 = analysis["genre_predictions"][0][0].split("---")[-1] if analysis.get("genre_predictions") else "?"
                print(f"✅ {elapsed:.1f}s → {top1}")
                d400_ok += 1
            else:
                discogs_cache[t["path"]] = ""
                print(f"❌ {elapsed:.1f}s")
                d400_fail += 1
        print(f"\n   Discogs400: {d400_ok} OK, {d400_fail} failed\n")

    # Pre-run web search if needed
    ws_variants = [v for v in variants if "+WS" in v]
    ws_cache: Dict[str, str] = {}  # track_path -> prompt_context
    ws_cache_file = AB_DIR / "ws_cache.json"

    # Load persistent WS cache (survives --resume)
    if ws_variants and ws_cache_file.exists():
        try:
            ws_cache = json.loads(ws_cache_file.read_text(encoding="utf-8"))
            print(f"  💾 WS cache loaded: {sum(1 for v in ws_cache.values() if v)} results from {ws_cache_file.name}")
        except Exception:
            ws_cache = {}

    if ws_variants:
        # Only search tracks not already cached
        tracks_to_search = [t for t in tracks if t["path"] not in ws_cache]
        if not tracks_to_search:
            print(f"🔍 Web search: all {len(tracks)} tracks cached, skipping.\n")
        else:
            print(f"🔍 Running web search (SearXNG) for {len(tracks_to_search)}/{len(tracks)} tracks...\n")
        try:
            searcher = create_searcher("searxng")
            if not searcher.is_available():
                print("  ⚠️  SearXNG not available — WS variants will have empty context")
                searcher = None
        except Exception as e:
            print(f"  ⚠️  SearXNG init failed: {e}")
            searcher = None

        ws_ok = ws_fail = 0
        for i, t in enumerate(tracks_to_search):
            meta = extract_metadata_from_filename(t["filename"])
            tk = f"{meta.get('artist', '')} - {meta.get('title', '')}"

            if not searcher:
                ws_cache[t["path"]] = ""
                ws_fail += 1
                continue

            print(f"  [{i+1}/{len(tracks_to_search)}] {tk[:55]}...", end=" ", flush=True)
            t0 = time.time()
            try:
                sr = search_track_genre(
                    searcher,
                    artist=meta.get("artist", ""),
                    title=meta.get("title", ""),
                    version=meta.get("version", ""),
                    filename=t["filename"],
                )
                elapsed = time.time() - t0
                context = sr.to_prompt_context()
                ws_cache[t["path"]] = context
                n = len(sr.results)
                from collections import Counter
                src_counts = Counter(r.source for r in sr.results)
                src_parts = ", ".join(f"{c} {s}" for s, c in src_counts.most_common())
                print(f"✅ {elapsed:.1f}s → {n} results ({src_parts})")
                ws_ok += 1
            except Exception as e:
                elapsed = time.time() - t0
                ws_cache[t["path"]] = ""
                ws_fail += 1
                print(f"❌ {elapsed:.1f}s — {e}")
            time.sleep(1.0)  # Rate limit for SearXNG

            # Save WS cache after each track so progress survives interrupts
            ws_cache_file.write_text(json.dumps(ws_cache, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n   Web search: {ws_ok} OK, {ws_fail} failed\n")

    # ── Build all jobs ────────────────────────────────────────────────────
    # Pre-compute per-track metadata once (same across all variants)
    track_meta_cache: Dict[str, Dict[str, str]] = {}  # path -> meta dict
    for t in tracks:
        meta = extract_metadata_from_filename(t["filename"])
        tag_bpm = read_bpm_from_audio_tag(t["path"])
        if tag_bpm:
            meta["bpm"] = tag_bpm
        tag_key = read_key_from_audio_tag(t["path"])
        if tag_key:
            meta["key"] = tag_key
        # Safety: AB test must NOT use enrichment metadata (genres_*).
        assert not any(meta.get(k) for k in (
            "genres_musicbrainz", "genres_lastfm",
            "genres_soundcloud", "genres_beatport",
        )), f"Enrichment metadata leaked into AB test meta: {meta}"
        track_meta_cache[t["path"]] = meta

    # Build job list: (track, variant, prompt, model) — skip cached if resume
    all_results: List[Dict[str, Any]] = []
    total_calls = 0
    total_tokens = 0
    jobs: List[Dict[str, Any]] = []

    for t in tracks:
        meta = track_meta_cache[t["path"]]
        for variant in variants:
            track_key = f"{t['filename']}:{variant}"

            # Skip if resume and already tested
            if resume and track_key in results.get("tracks", {}):
                cached = results["tracks"][track_key]
                all_results.append(cached)
                continue

            use_d400 = "+D400" in variant
            use_essentia_interp = "+EI" in variant
            use_essentia_v2 = "+E2" in variant
            use_essentia = "+E" in variant and not use_essentia_v2 and not use_d400 and not use_essentia_interp
            use_ws = "+WS" in variant
            model_key = variant.replace("+D400", "").replace("+WS", "").replace("+EI", "").replace("+E2", "").replace("+E", "")
            model_name = MODEL_TIERS[model_key]

            if use_d400:
                audio_desc = discogs_cache.get(t["path"], "")
            elif use_essentia_interp:
                audio_desc = essentia_interp_cache.get(t["path"], "")
            elif use_essentia_v2:
                audio_desc = essentia_cache_v2.get(t["path"], "")
            elif use_essentia:
                audio_desc = essentia_cache.get(t["path"], "")
            else:
                audio_desc = ""

            ws_context = ws_cache.get(t["path"], "") if use_ws else ""
            prompt = build_prompt(meta, genre_labels, audio_desc, ws_context)

            jobs.append({
                "track": t,
                "variant": variant,
                "model_name": model_name,
                "prompt": prompt,
                "track_key": track_key,
            })

    cached_count = len(all_results)
    print(f"  💾 {cached_count} cached, {len(jobs)} to process (concurrency={concurrency})")

    # ── Execute API calls in parallel ────────────────────────────────────
    def _process_job(job: Dict[str, Any]) -> Dict[str, Any]:
        """Call OpenAI for a single (track, variant) job. Thread-safe."""
        try:
            result = call_openai(api_key, job["prompt"], job["model_name"])
        except Exception as e:
            result = {"genre": "ERROR", "confidence": 0, "reasoning": str(e)}

        predicted = result.get("genre", "?")
        expected = job["track"]["expected_genre"]
        is_correct = predicted == expected

        return {
            "filename": job["track"]["filename"],
            "expected_genre": expected,
            "predicted_genre": predicted,
            "correct": is_correct,
            "confidence": result.get("confidence", 0),
            "reasoning": result.get("reasoning", ""),
            "variant": job["variant"],
            "model": job["model_name"],
            "elapsed": result.get("_elapsed", 0),
            "input_tokens": result.get("_input_tokens", 0),
            "output_tokens": result.get("_output_tokens", 0),
            "_track_key": job["track_key"],
        }

    if jobs:
        t_start = time.time()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_process_job, job): job for job in jobs}
            done_count = 0
            for future in as_completed(futures):
                done_count += 1
                entry = future.result()
                track_key = entry.pop("_track_key")
                all_results.append(entry)
                results["tracks"][track_key] = entry
                total_calls += 1
                total_tokens += entry["input_tokens"] + entry["output_tokens"]

                icon = "✅" if entry["correct"] else "❌"
                print(f"  [{done_count}/{len(jobs)}] {icon} {entry['variant']:12s} | "
                      f"{entry['filename'][:40]:40s} → {entry['predicted_genre']}")
                if not entry["correct"]:
                    reasoning = entry.get("reasoning", "")[:80]
                    print(f"{'':20s}💭 {reasoning}")

                # Save results periodically (every 16 completions)
                if done_count % 16 == 0:
                    save_results(results)

        elapsed_total = time.time() - t_start
        rps = total_calls / elapsed_total if elapsed_total > 0 else 0
        print(f"\n  ⚡ {total_calls} API calls in {elapsed_total:.1f}s ({rps:.1f} req/s)")

    save_results(results)

    # Per-variant accuracy summary (computed from all_results including cached)
    for variant in variants:
        variant_results = [r for r in all_results if r.get("variant") == variant]
        correct = sum(1 for r in variant_results if r.get("correct"))
        total = len(variant_results)
        accuracy = correct / total * 100 if total else 0
        vtokens = sum(r.get("input_tokens", 0) + r.get("output_tokens", 0) for r in variant_results)
        print(f"  📊 {variant:12s}: {correct}/{total} correct ({accuracy:.0f}%)  tokens: {vtokens:,}")

    # Save results
    run_meta["total_api_calls"] = total_calls
    run_meta["total_tokens"] = total_tokens
    results["runs"].append(run_meta)
    save_results(results)

    # Summary
    print(f"\n{'='*70}")
    print(f"  SUMMARY")
    print(f"{'='*70}\n")

    for variant in variants:
        variant_results = [r for r in all_results if r.get("variant") == variant]
        correct = sum(1 for r in variant_results if r.get("correct"))
        total = len(variant_results)
        accuracy = correct / total * 100 if total else 0
        avg_tokens = sum(r.get("input_tokens", 0) + r.get("output_tokens", 0) for r in variant_results) / max(total, 1)
        avg_time = sum(r.get("elapsed", 0) for r in variant_results) / max(total, 1)

        print(f"  {variant:12s} : {correct}/{total} ({accuracy:5.1f}%)  avg {avg_tokens:.0f} tok  avg {avg_time:.1f}s")

    # Cost estimate
    print(f"\n  Cost estimate for 5000 tracks:")
    for variant in variants:
        variant_results = [r for r in all_results if r.get("variant") == variant]
        avg_in = sum(r.get("input_tokens", 0) for r in variant_results) / max(len(variant_results), 1)
        avg_out = sum(r.get("output_tokens", 0) for r in variant_results) / max(len(variant_results), 1)
        model_key = variant.replace("+D400", "").replace("+WS", "").replace("+EI", "").replace("+E2", "").replace("+E", "")
        model_name = MODEL_TIERS[model_key]

        # Pricing per 1M tokens (approximate, 2026 rates)
        pricing = {
            "gpt-5-nano": (0.10, 0.40),
            "gpt-5-mini": (0.40, 1.60),
            "gpt-5": (2.00, 8.00),
        }
        in_price, out_price = pricing.get(model_name, (1.0, 4.0))
        cost_5k = 5000 * (avg_in * in_price / 1_000_000 + avg_out * out_price / 1_000_000)
        # EI variants have 2 API calls per track (interpreter + classifier)
        if "+EI" in variant:
            cost_5k *= 2  # rough estimate — interpreter uses similar token count
        print(f"    {variant:12s} : ~${cost_5k:.2f}")

    # Per-genre breakdown
    genres_tested = sorted(set(r["expected_genre"] for r in all_results))
    if len(genres_tested) > 1:
        print(f"\n  Per-genre accuracy (across all variants):")
        for genre in genres_tested:
            genre_results = [r for r in all_results if r["expected_genre"] == genre]
            correct = sum(1 for r in genre_results if r.get("correct"))
            total = len(genre_results)
            print(f"    {genre:25s} : {correct}/{total} ({correct/total*100:.0f}%)")

    # Misclassifications
    misses = [r for r in all_results if not r.get("correct")]
    if misses:
        print(f"\n  Misclassifications ({len(misses)} total):")
        for r in misses:
            print(f"    {r['variant']:12s} | {r['filename'][:40]:40s} → {r['predicted_genre']:20s} (exp: {r['expected_genre']})")

    print(f"\n  Results saved to: {RESULTS_FILE}")
    print(f"  Total API calls: {total_calls}, Total tokens: {total_tokens:,}\n")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="A/B test genre classification with Essentia")
    parser.add_argument("--scan", action="store_true", help="Just list discovered tracks")
    parser.add_argument("--essentia", action="store_true", help="Run Essentia analysis only (no API calls)")
    parser.add_argument("--run", choices=["v2"],
                        help="Run preset: v2 = nano, nano+EI, nano+WS, nano+EI+WS (symmetric prompts)")
    parser.add_argument("--variants", nargs="+", default=None,
                        choices=ALL_VARIANTS, help="Which variants to test (overrides --run)")
    parser.add_argument("--resume", action="store_true", help="Skip already-tested tracks")
    parser.add_argument("--concurrency", type=int, default=16,
                        help="Max parallel OpenAI API calls (default: 16)")
    args = parser.parse_args()

    if args.scan:
        run_scan()
    elif args.essentia:
        run_essentia_only()
    else:
        if args.variants:
            variants = args.variants
        elif args.run == "v2":
            variants = V2_VARIANTS
        else:
            variants = V2_VARIANTS  # default to the 4 key variants
        run_ab_test(variants, resume=args.resume, concurrency=args.concurrency)


if __name__ == "__main__":
    main()
