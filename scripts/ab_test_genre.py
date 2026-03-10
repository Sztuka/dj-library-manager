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
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import yaml
import requests

from djlib.config import get_openai_api_key

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
                "nano+D400", "mini+D400", "full+D400"]


# ── Track discovery ──────────────────────────────────────────────────────────

def discover_tracks() -> List[Dict[str, str]]:
    """Scan data/ab_test/<genre>/ for audio files.

    Returns list of dicts with keys: path, expected_genre, filename.
    """
    tracks = []
    if not AB_DIR.exists():
        return tracks

    for genre_dir in sorted(AB_DIR.iterdir()):
        if not genre_dir.is_dir() or genre_dir.name.startswith("."):
            continue
        expected_genre = genre_dir.name
        for audio_file in sorted(genre_dir.iterdir()):
            if audio_file.suffix.lower() in AUDIO_EXTENSIONS and not audio_file.name.startswith("."):
                tracks.append({
                    "path": str(audio_file),
                    "expected_genre": expected_genre,
                    "filename": audio_file.name,
                })
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


# ── Essentia analysis ───────────────────────────────────────────────────────

def read_bpm_from_audio_tag(file_path: str) -> str:
    """Read BPM from audio file tags (ID3/Vorbis/MP4). Rekordbox writes these.

    Returns BPM as string (e.g. '125') or '' if not found.
    Priority: tag BPM > filename BPM, because Rekordbox analysis is precise.
    """
    try:
        import mutagen
        audio = mutagen.File(file_path, easy=True)
        if audio and "bpm" in audio:
            raw = str(audio["bpm"][0]).strip()
            # Validate: should be a number between 50 and 250
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


def build_prompt(ctx: Dict[str, str], genre_labels: List[str], audio_desc: str = "") -> str:
    """Build genre classification prompt (mirrors server.py _build_genre_prompt)."""
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
    if ctx.get("folder"):
        parts.append(f"DJ's working folder (set context, NOT genre): {ctx['folder']}")
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
        "\n\nBPM genre ranges (approximate, ranges overlap — use together with other signals):\n"
        "70-100: Hip-Hop, R&B, Reggaeton, Dancehall\n"
        "100-115: UK Garage, Afrobeats\n"
        "115-126: Deep House\n"
        "116-128: Afro House\n"
        "120-128: House, Tech House\n"
        "124-132: Melodic Techno, Progressive House\n"
        "128-140: Techno, Hard Techno, Trance\n"
        "140-150: Psytrance\n"
        "150-180: Drum & Bass"
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

    system_prompt = (
        f"You are an expert DJ music classifier. Classify tracks into exactly ONE of these genres:\n"
        f"{genre_list}\n\n"
        f"Use ALL available signals to determine the MOST SPECIFIC matching genre:\n"
        f"* REMIXER/EDITOR identity — strongest signal for remixes (their known scene/style)\n"
        f"* BPM — strong structural signal\n"
        f"* Metadata tags — from music databases (may be wrong for remixes)"
        f"{audio_signal_line}"
        f"{remix_instruction}"
        f"{bpm_guide}\n\n"
        f"Respond with JSON: {{\"genre\": \"...\", \"confidence\": 0.0-1.0, \"reasoning\": \"...\"}}"
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


def run_ab_test(variants: List[str], resume: bool = False):
    """Run the full A/B test."""
    api_key = get_openai_api_key()
    if not api_key:
        print("❌ No OpenAI API key. Add openai_api_key to config.local.yml")
        sys.exit(1)

    tracks = discover_tracks()
    if not tracks:
        print("❌ No tracks found in data/ab_test/<genre>/")
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
    essentia_variants = [v for v in variants if "+E" in v and "+E2" not in v and "+D400" not in v]
    essentia_cache: Dict[str, str] = {}  # path -> audio_description

    essentia_v2_variants = [v for v in variants if "+E2" in v]
    essentia_cache_v2: Dict[str, str] = {}  # path -> audio_description v2

    if essentia_variants or essentia_v2_variants:
        print("🔬 Loading cached Essentia audio features...\n")
        essentia_ok = essentia_miss = 0
        for t in tracks:
            features = run_essentia_analysis(t["path"], cache_only=True)
            if features:
                if essentia_variants:
                    essentia_cache[t["path"]] = describe_audio_features(features)
                if essentia_v2_variants:
                    essentia_cache_v2[t["path"]] = describe_audio_features_v2(features)
                essentia_ok += 1
            else:
                essentia_cache[t["path"]] = ""
                essentia_cache_v2[t["path"]] = ""
                essentia_miss += 1
        print(f"   Essentia: {essentia_ok} OK, {essentia_miss} missing (run --essentia first)\n")

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

    # Run tests
    all_results: List[Dict[str, Any]] = []
    total_calls = 0
    total_tokens = 0

    for variant in variants:
        use_d400 = "+D400" in variant
        use_essentia_v2 = "+E2" in variant
        use_essentia = "+E" in variant and not use_essentia_v2 and not use_d400
        model_key = variant.replace("+D400", "").replace("+E2", "").replace("+E", "")
        model_name = MODEL_TIERS[model_key]

        print(f"\n{'─'*70}")
        print(f"  Variant: {variant} ({model_name})")
        print(f"{'─'*70}\n")

        correct = 0
        variant_tokens = 0

        for t in tracks:
            track_key = f"{t['filename']}:{variant}"

            # Skip if resume and already tested
            if resume and track_key in results.get("tracks", {}):
                cached = results["tracks"][track_key]
                if cached.get("genre") == t["expected_genre"]:
                    correct += 1
                all_results.append(cached)
                print(f"  ⏭ {t['filename'][:50]} (cached)")
                continue

            meta = extract_metadata_from_filename(t["filename"])
            meta["folder"] = t["expected_genre"]  # folder = context hint

            # Enrich BPM from audio tag (Rekordbox) — overrides filename BPM
            tag_bpm = read_bpm_from_audio_tag(t["path"])
            if tag_bpm:
                meta["bpm"] = tag_bpm

            if use_d400:
                audio_desc = discogs_cache.get(t["path"], "")
            elif use_essentia_v2:
                audio_desc = essentia_cache_v2.get(t["path"], "")
            elif use_essentia:
                audio_desc = essentia_cache.get(t["path"], "")
            else:
                audio_desc = ""
            prompt = build_prompt(meta, genre_labels, audio_desc)

            try:
                result = call_openai(api_key, prompt, model_name)
                total_calls += 1
            except Exception as e:
                print(f"  ❌ API error: {e}")
                result = {"genre": "ERROR", "confidence": 0, "reasoning": str(e)}

            predicted = result.get("genre", "?")
            expected = t["expected_genre"]
            is_correct = predicted == expected

            if is_correct:
                correct += 1

            icon = "✅" if is_correct else "❌"
            print(f"  {icon} {t['filename'][:50]}")
            print(f"     → {predicted:25s} (exp: {expected})")
            if not is_correct:
                reasoning = result.get("reasoning", "")[:100]
                print(f"     💭 {reasoning}")

            # Store result
            entry = {
                "filename": t["filename"],
                "expected_genre": expected,
                "predicted_genre": predicted,
                "correct": is_correct,
                "confidence": result.get("confidence", 0),
                "reasoning": result.get("reasoning", ""),
                "variant": variant,
                "model": model_name,
                "elapsed": result.get("_elapsed", 0),
                "input_tokens": result.get("_input_tokens", 0),
                "output_tokens": result.get("_output_tokens", 0),
            }
            all_results.append(entry)
            results["tracks"][track_key] = entry

            variant_tokens += result.get("_input_tokens", 0) + result.get("_output_tokens", 0)
            total_tokens += result.get("_input_tokens", 0) + result.get("_output_tokens", 0)

            # Rate limit: small delay between calls
            time.sleep(0.3)

        accuracy = correct / len(tracks) * 100 if tracks else 0
        print(f"\n  📊 {variant}: {correct}/{len(tracks)} correct ({accuracy:.0f}%)")
        print(f"     Tokens: {variant_tokens:,}")

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
        model_key = variant.replace("+D400", "").replace("+E2", "").replace("+E", "")
        model_name = MODEL_TIERS[model_key]

        # Pricing per 1M tokens (approximate, 2026 rates)
        pricing = {
            "gpt-5-nano": (0.10, 0.40),
            "gpt-5-mini": (0.40, 1.60),
            "gpt-5": (2.00, 8.00),
        }
        in_price, out_price = pricing.get(model_name, (1.0, 4.0))
        cost_5k = 5000 * (avg_in * in_price / 1_000_000 + avg_out * out_price / 1_000_000)
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
    parser.add_argument("--variants", nargs="+", default=ALL_VARIANTS,
                        choices=ALL_VARIANTS, help="Which variants to test")
    parser.add_argument("--resume", action="store_true", help="Skip already-tested tracks")
    args = parser.parse_args()

    if args.scan:
        run_scan()
    elif args.essentia:
        run_essentia_only()
    else:
        run_ab_test(args.variants, resume=args.resume)


if __name__ == "__main__":
    main()
