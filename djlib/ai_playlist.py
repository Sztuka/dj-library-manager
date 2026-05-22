"""AI playlist builder — generates a playlist from a natural-language brief.

Uses Gemini (gemini-2.5-flash) with the full library as context.
Writes the chosen playlist name directly to the `playlists` field in
library.csv so it's immediately visible in the Review UI.

Usage:
    djlib ai-playlist "dark progressive, 130-134 BPM, 3h set" --name "FridaySet"
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

_MODEL = "gemini-2.5-flash"
_MAX_TRACKS_IN_PROMPT = 2000  # safety cap — 1M token window is plenty


def _fmt_duration(secs_raw: str) -> str:
    try:
        s = int(float(secs_raw))
        return f"{s // 60}:{s % 60:02d}"
    except (ValueError, TypeError):
        return ""


def _build_library_tsv(rows: List[Dict]) -> str:
    """Compact TSV representation of the library — one track per line."""
    lines = ["idx\tartist\ttitle\tgenre\tbpm\tkey\tduration\trating\tplaylists"]
    for i, row in enumerate(rows[:_MAX_TRACKS_IN_PROMPT], 1):
        lines.append("\t".join([
            str(i),
            row.get("artist") or "",
            row.get("title") or "",
            row.get("genre") or "",
            row.get("bpm") or "",
            row.get("key_camelot") or "",
            _fmt_duration(row.get("duration_seconds") or ""),
            row.get("rating") or "",
            row.get("playlists") or "",
        ]))
    return "\n".join(lines)


def _build_prompt(brief: str, library_tsv: str, count: int) -> str:
    return f"""You are a professional DJ assistant. Your job is to select tracks from a DJ's library for a specific set.

## Client brief
{brief}

## Instructions
- Select exactly {count} tracks that best fit the brief.
- Use BPM, key (Camelot notation), genre, and rating to judge fit.
- Prefer tracks with higher ratings when quality is equal.
- Tracks already in a playlist called the same name should NOT be re-added (check the playlists column).
- Return ONLY a JSON array of integers — the `idx` values from the library below.
- No explanation, no markdown, no extra text. Just the JSON array.

Example output: [12, 47, 3, 201, 88]

## Library (TSV, {_MAX_TRACKS_IN_PROMPT} track limit)
{library_tsv}"""


def _call_gemini(api_key: str, prompt: str) -> List[int]:
    from google import genai  # type: ignore[import]

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=_MODEL,
        contents=prompt,
    )
    raw = (response.text or "").strip()
    # Strip accidental markdown fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)


def build_ai_playlist(
    library_path: Path,
    brief: str,
    playlist_name: str,
    count: int = 40,
    dry_run: bool = False,
    api_key: Optional[str] = None,
) -> List[str]:
    """Select tracks and tag them with playlist_name in library.csv.

    Returns list of track_ids that were tagged.
    """
    from djlib.library_schema import load_library_csv, save_library_csv
    from djlib.locks import csv_lock
    from djlib.config import get_gemini_api_key

    if api_key is None:
        api_key = get_gemini_api_key()
    if not api_key:
        raise RuntimeError("Gemini API key not configured — add gemini_api_key to config.local.yml")

    rows = load_library_csv(library_path)
    if not rows:
        raise RuntimeError(f"Library is empty: {library_path}")

    # Only offer library-disposition tracks (skip rejects, mixes if desired)
    candidates = [r for r in rows if (r.get("disposition") or "").strip() == "library"]
    if not candidates:
        log.warning("No 'library' tracks found — using all rows")
        candidates = rows

    log.info("Sending %d tracks to Gemini (%s)…", len(candidates), _MODEL)

    tsv = _build_library_tsv(candidates)
    prompt = _build_prompt(brief, tsv, count)

    selected_indices = _call_gemini(api_key, prompt)

    if not isinstance(selected_indices, list):
        raise RuntimeError(f"Gemini returned unexpected format: {selected_indices!r}")

    # Map 1-based idx → track_id
    tagged: List[str] = []
    idx_to_row = {i + 1: r for i, r in enumerate(candidates[:_MAX_TRACKS_IN_PROMPT])}

    for idx in selected_indices:
        row = idx_to_row.get(int(idx))
        if row is None:
            log.warning("Gemini returned out-of-range idx=%s — skipping", idx)
            continue
        tagged.append(row["track_id"])

    log.info("Gemini selected %d tracks", len(tagged))

    if dry_run:
        return tagged

    # Write playlist name into library.csv for each selected track
    tagged_set = set(tagged)
    with csv_lock(library_path):
        all_rows = load_library_csv(library_path)
        for row in all_rows:
            if row.get("track_id") not in tagged_set:
                continue
            existing = {p.strip() for p in (row.get("playlists") or "").split("|") if p.strip()}
            if playlist_name not in existing:
                existing.add(playlist_name)
                row["playlists"] = "|".join(sorted(existing))
        save_library_csv(library_path, all_rows)

    return tagged
