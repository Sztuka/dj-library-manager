"""Unified AI classification: artist, title, version, genre in one call.

This module sends track metadata to OpenAI and gets back a structured
result with properly parsed naming AND genre classification.  One API
call replaces the old two-step identify + suggest-genre pipeline.

Usage:
    from djlib.ai_classify import classify_track, batch_classify
    result = classify_track(row, genre_labels, api_key)
    # result = {"artist": "...", "title": "...", "version": ["Remix", "Clean"],
    #           "genre": "Tech House", "confidence": 0.92, "reasoning": "..."}
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests as http_requests
import yaml

from djlib.config import get_openai_api_key, get_ai_quick_model

_log = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parent.parent


# ── Genre loading ────────────────────────────────────────────────────────────

_genres_cache: Optional[List[str]] = None


def load_genre_labels() -> List[str]:
    """Return sorted list of genre labels from genres.yml."""
    global _genres_cache
    if _genres_cache is not None:
        return _genres_cache
    genres_path = _REPO / "genres.yml"
    if not genres_path.exists():
        return []
    with open(genres_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return []
    labels = sorted(
        {entry.get("label", key) for key, entry in data.items()
         if isinstance(entry, dict)}
    )
    _genres_cache = labels
    return labels


# ── Prompt construction ──────────────────────────────────────────────────────

def _build_track_info(row: Dict[str, str]) -> str:
    """Extract all available metadata from a CSV row into formatted text."""
    file_path = (row.get("file_path") or "").strip()
    filename = ""
    folder = ""
    if file_path:
        p = Path(file_path).expanduser()
        filename = p.name
        folder = p.parent.name

    parts = []
    if filename:
        parts.append(f"Filename: {filename}")
    if folder:
        parts.append(f"Folder: {folder}")

    # Original audio tags
    for tag_field, label in [
        ("tag_artist_original", "Audio tag artist"),
        ("tag_title_original", "Audio tag title"),
        ("tag_genre_original", "Audio tag genre"),
    ]:
        val = (row.get(tag_field) or "").strip()
        if val:
            parts.append(f"{label}: {val}")

    # Algorithmic parse results
    for field, label in [
        ("artist_suggest", "Parsed artist"),
        ("title_suggest", "Parsed title"),
        ("version_suggest", "Parsed version"),
    ]:
        val = (row.get(field) or "").strip()
        if val:
            parts.append(f"{label}: {val}")

    # Audio characteristics
    bpm = (row.get("bpm") or "").strip()
    key = (row.get("key_camelot") or "").strip()
    duration = (row.get("duration_suggest") or "").strip()
    if bpm:
        parts.append(f"BPM: {bpm}")
    if key:
        parts.append(f"Key: {key}")
    if duration:
        parts.append(f"Duration: {duration}")

    # External genre sources
    for field, label in [
        ("genres_beatport", "Beatport genre"),
        ("genres_lastfm", "Last.fm genres"),
        ("genres_musicbrainz", "MusicBrainz genres"),
        ("genres_soundcloud", "SoundCloud tags"),
    ]:
        val = (row.get(field) or "").strip()
        if val:
            parts.append(f"{label}: {val}")

    return "\n".join(parts)


def build_classify_prompt(row: Dict[str, str], genre_labels: List[str]) -> str:
    """Build the unified identify + genre classification prompt.

    Returns a JSON-encoded list of messages [{role, content}, ...].
    """
    genre_list = ", ".join(genre_labels)
    track_info = _build_track_info(row)

    system_prompt = (
        "You are a DJ music expert. Given track metadata, you perform TWO tasks:\n"
        "1. IDENTIFY the correct artist, title, and version/remix information\n"
        "2. CLASSIFY the genre from the allowed list\n\n"

        "═══ NAMING RULES ═══\n"
        "• Artist field: main artist(s) who MADE the original track\n"
        "  - Multiple artists: join with ' x ' (e.g. 'Diplo x Damian Marley')\n"
        "  - Featuring artists go IN THE TITLE: 'Title feat. Other Artist'\n"
        "  - The remixer is NOT the artist — they go in version\n"
        "• Title field: the track name, without remix/edit info\n"
        "  - Include 'feat. X' in the title if there is a featured artist\n"
        "  - Do NOT include remix/edit/version info in the title\n"
        "• Version field: a JSON ARRAY of separate version tokens\n"
        "  - Each distinct modifier gets its own entry\n"
        "  - Examples:\n"
        '    ["Extended Mix"] — one token\n'
        '    ["Vintage Culture Remix", "Clean"] — two separate tokens\n'
        '    ["Hard Edit", "Clean Intro"] — two separate tokens\n'
        '    ["Original Mix"] — one token\n'
        '    [] — empty array if no version/remix info\n'
        "  - Common version tokens: Remix, Edit, Mix, Extended Mix, Original Mix,\n"
        "    Radio Edit, Club Mix, Dub Mix, VIP, Bootleg, Rework, Instrumental,\n"
        "    Acapella, Clean, Dirty, Intro, Outro, Short Edit, Quick Hit\n"
        "  - For mashups/edits: put the edit creator as 'Creator Edit'\n"
        "    e.g. artist='Wiz Khalifa x Feid', title='We Dem Boyz x Ethnica',\n"
        "    version=['Loup Musa Edit']\n"
        "• Use proper Title Case for artist and title names\n"
        "• If a field cannot be determined, return empty string (or empty array for version)\n\n"

        "═══ GENRE CLASSIFICATION ═══\n"
        f"Classify into exactly ONE genre from: {genre_list}\n\n"
        "Genre signals (strongest to weakest):\n"
        "1. REMIXER identity — for remixes, the remixer's known scene/style is the #1 signal\n"
        "2. BPM — strong structural constraint (see ranges below)\n"
        "3. Artist identity — what genre does this artist typically produce?\n"
        "4. External genre tags — Beatport, MusicBrainz, Last.fm (helpful but may be generic)\n"
        "5. Folder name — weak hint, may indicate DJ's intended use, not precise genre\n\n"

        "BPM ranges (approximate, overlapping — combine with other signals):\n"
        "70-100: Hip-Hop, R&B, Reggaeton, Dancehall\n"
        "100-115: Broken Beat, UK Garage, Afrobeats\n"
        "115-126: Deep House, Soulful House\n"
        "116-128: Afro House, Organic House\n"
        "120-128: House, Tech House, Jackin House\n"
        "124-132: Melodic Techno, Progressive House\n"
        "128-140: Techno, Hard Techno, Trance\n"
        "140-150: Psytrance\n"
        "150-180: Drum & Bass\n\n"

        "CRITICAL — REMIX RULE:\n"
        "If this is a remix/edit, classify by the REMIX STYLE, NOT the original track's genre.\n"
        "A Hip-Hop track remixed at 124 BPM = House-family, NOT Hip-Hop.\n\n"

        "═══ RESPONSE FORMAT ═══\n"
        "Respond ONLY with valid JSON (no markdown, no code fences):\n"
        '{"artist": "Artist Name", "title": "Track Title", '
        '"version": ["Token1", "Token2"], '
        '"genre": "Exact Genre From List", "confidence": 0.0-1.0, '
        '"reasoning": "1-2 sentences"}'
    )

    user_prompt = f"Identify and classify this track:\n\n{track_info}"

    return json.dumps([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ])


# ── OpenAI caller ────────────────────────────────────────────────────────────

def _call_openai(
    api_key: str,
    prompt_json: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Call OpenAI Chat Completions API and parse JSON result."""
    messages = json.loads(prompt_json)
    model = model or get_ai_quick_model()

    # GPT-5+ / reasoning models: need max_completion_tokens (not max_tokens),
    # no temperature support, and higher token budget for reasoning + output
    is_reasoning = model and (model.startswith("gpt-5") or model.startswith("o"))
    token_param = "max_completion_tokens" if is_reasoning else "max_tokens"
    # Reasoning models use ~1500-2000 tokens for thinking before producing output
    token_limit = 4000 if is_reasoning else 400

    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        token_param: token_limit,
    }
    if not is_reasoning:
        body["temperature"] = 0.2

    resp = http_requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=90 if is_reasoning else 30,
    )
    resp.raise_for_status()
    data = resp.json()
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")

    # Strip markdown fences
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```\w*\n?", "", content)
        content = re.sub(r"\n?```$", "", content)
        content = content.strip()

    result = json.loads(content)

    # Extract usage for cost tracking
    usage = data.get("usage", {})
    result["_usage"] = {
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "model": model,
    }

    return result


def _validate_result(
    result: Dict[str, Any], genre_labels: List[str]
) -> Dict[str, Any]:
    """Validate and normalize the AI response."""
    # Ensure version is a list
    version = result.get("version", [])
    if isinstance(version, str):
        # AI returned a string instead of list — split on comma
        if version:
            result["version"] = [v.strip() for v in version.split(",") if v.strip()]
        else:
            result["version"] = []

    # Validate genre is from our list
    genre = result.get("genre", "")
    if genre not in genre_labels:
        lower_map = {g.lower(): g for g in genre_labels}
        matched = lower_map.get(genre.lower())
        if matched:
            result["genre"] = matched
        else:
            result["genre_warning"] = (
                f"AI suggested '{genre}' which is not in genres.yml"
            )

    # Ensure confidence is a float
    try:
        result["confidence"] = float(result.get("confidence", 0))
    except (TypeError, ValueError):
        result["confidence"] = 0.0

    return result


# ── Public API ───────────────────────────────────────────────────────────────

def classify_track(
    row: Dict[str, str],
    genre_labels: Optional[List[str]] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Classify a single track: returns artist, title, version[], genre.

    Args:
        row: CSV row dict with track metadata.
        genre_labels: Allowed genre labels (loaded from genres.yml if None).
        api_key: OpenAI API key (loaded from config if None).
        model: Model override (uses config default if None).

    Returns:
        Dict with keys: artist, title, version (list), genre, confidence,
        reasoning, _usage.
    """
    if genre_labels is None:
        genre_labels = load_genre_labels()
    if api_key is None:
        api_key = get_openai_api_key()
    if not api_key:
        raise ValueError("No OpenAI API key configured")

    prompt = build_classify_prompt(row, genre_labels)
    result = _call_openai(api_key, prompt, model=model)
    result = _validate_result(result, genre_labels)
    return result


def batch_classify(
    rows: List[Dict[str, str]],
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    on_progress: Optional[Any] = None,
    delay: float = 0.1,
) -> List[Tuple[Dict[str, str], Dict[str, Any]]]:
    """Classify a batch of tracks.

    Args:
        rows: List of CSV row dicts.
        api_key: OpenAI API key.
        model: Model override.
        on_progress: Callback(index, total, row, result) called after each track.
        delay: Seconds between API calls (rate limiting).

    Returns:
        List of (row, result) tuples. On error, result has an "error" key.
    """
    if api_key is None:
        api_key = get_openai_api_key()
    if not api_key:
        raise ValueError("No OpenAI API key configured")

    genre_labels = load_genre_labels()
    results: List[Tuple[Dict[str, str], Dict[str, Any]]] = []
    total = len(rows)

    for i, row in enumerate(rows):
        try:
            result = classify_track(row, genre_labels, api_key, model)
        except Exception as e:
            _log.warning(
                "Error classifying track %s: %s",
                row.get("file_path", row.get("title", f"#{i}")),
                e,
            )
            result = {"error": str(e)}

        results.append((row, result))

        if on_progress:
            on_progress(i, total, row, result)

        if delay and i < total - 1:
            time.sleep(delay)

    return results


def format_version_for_filename(version_tokens: List[str]) -> str:
    """Convert version token list to filename format.

    ["Vintage Culture Remix", "Clean"] → "(Vintage Culture Remix) (Clean)"
    """
    if not version_tokens:
        return ""
    return " ".join(f"({tok})" for tok in version_tokens if tok)


def format_version_for_csv(version_tokens: List[str]) -> str:
    """Convert version token list to CSV storage format.

    ["Vintage Culture Remix", "Clean"] → "Vintage Culture Remix, Clean"
    """
    return ", ".join(tok for tok in version_tokens if tok)
