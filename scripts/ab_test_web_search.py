#!/usr/bin/env python3
"""A/B Test: Web search backend comparison for genre classification.

Runs the same tracks through all 4 web search backends (DDG, SearXNG,
Brave, Serper) + a "no search" baseline, feeds results to GPT-5-nano,
and produces detailed comparison tables.

Data sources:
    - data/ab_test/<Genre>/*.mp3  (ground truth from folder name)
    - Built-in TEST_TRACKS        (curated with expected genres)
    - Any CSV via --csv

Results:
    - Persistent JSON at data/ab_test/web_search_results.json
    - Summary CSV at data/ab_test/web_search_comparison.csv
    - Terminal tables with color-coded accuracy

Usage:
    # Phase 1: search only (free, no LLM) — see what each backend finds
    .venv/bin/python scripts/ab_test_web_search.py --search-only --limit 10

    # Phase 2: full comparison with LLM (costs ~$0.01 per track × 5 variants)
    .venv/bin/python scripts/ab_test_web_search.py --limit 10

    # Specific backends only:
    .venv/bin/python scripts/ab_test_web_search.py --backends ddg brave

    # Resume (skip tracks already tested):
    .venv/bin/python scripts/ab_test_web_search.py --resume

    # From CSV:
    .venv/bin/python scripts/ab_test_web_search.py --csv data/library_review.csv --limit 10

    # Just print previous results (no API calls):
    .venv/bin/python scripts/ab_test_web_search.py --report
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from djlib.metadata.web_search import (
    SearchBackend,
    TrackSearchResults,
    create_searcher,
    list_backends,
    search_track_genre,
)
from djlib.config import get_openai_api_key, get_ai_quick_model

# ── Paths ────────────────────────────────────────────────────────────────────

AB_DIR = PROJECT_ROOT / "data" / "ab_test"
RESULTS_FILE = AB_DIR / "web_search_results.json"
CSV_REPORT_FILE = AB_DIR / "web_search_comparison.csv"

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aiff", ".aif", ".m4a", ".ogg"}

# ── Genre labels ─────────────────────────────────────────────────────────────

_genre_labels_cache: Optional[List[str]] = None


def load_genre_labels() -> List[str]:
    global _genre_labels_cache
    if _genre_labels_cache is None:
        from djlib.ai_classify import load_genre_labels as _load
        _genre_labels_cache = _load()
    return _genre_labels_cache


# ── Track discovery ──────────────────────────────────────────────────────────

# Built-in test tracks covering diverse genres.
BUILTIN_TRACKS = [
    # EDM originals
    {"artist": "deadmau5", "title": "Strobe", "version": "", "expected_genre": "Progressive House"},
    {"artist": "Fisher", "title": "Losing It", "version": "", "expected_genre": "Tech House"},
    {"artist": "Peggy Gou", "title": "Starry Night", "version": "", "expected_genre": "House"},
    {"artist": "Charlotte de Witte", "title": "Doppler", "version": "", "expected_genre": "Techno"},
    {"artist": "ARTBAT", "title": "Flame", "version": "", "expected_genre": "Melodic Techno"},
    # Remixes (genre = remix style, not original)
    {"artist": "Kanye West", "title": "Runaway", "version": "Vintage Culture Remix", "expected_genre": "Tech House"},
    {"artist": "Modjo", "title": "Lady", "version": "Meduza Remix", "expected_genre": "House"},
    {"artist": "Arctic Monkeys", "title": "Do I Wanna Know?", "version": "Adriatique & Dardust Remix", "expected_genre": "Melodic Techno"},
    # Non-EDM
    {"artist": "Nirvana", "title": "Smells Like Teen Spirit", "version": "", "expected_genre": "Rock"},
    {"artist": "Kendrick Lamar", "title": "HUMBLE.", "version": "", "expected_genre": "Hip-Hop"},
    {"artist": "Norah Jones", "title": "Don't Know Why", "version": "", "expected_genre": "Jazz"},
    {"artist": "Bonobo", "title": "Kerala", "version": "", "expected_genre": "Downtempo"},
]


def discover_ab_tracks() -> List[Dict[str, str]]:
    """Scan data/ab_test/<genre>/ for audio files with ground truth."""
    tracks = []
    if not AB_DIR.exists():
        return tracks

    for genre_dir in sorted(AB_DIR.iterdir()):
        if not genre_dir.is_dir() or genre_dir.name.startswith("."):
            continue
        expected_genre = genre_dir.name
        for audio_file in sorted(genre_dir.iterdir()):
            if audio_file.suffix.lower() in AUDIO_EXTENSIONS and not audio_file.name.startswith("."):
                meta = _parse_filename(audio_file.name)
                tracks.append({
                    "artist": meta["artist"],
                    "title": meta["title"],
                    "version": meta["version"],
                    "expected_genre": expected_genre,
                    "filename": audio_file.name,
                    "bpm": meta.get("bpm", ""),
                    "key": meta.get("key", ""),
                })
    return tracks


def _parse_filename(filename: str) -> Dict[str, str]:
    """Parse 'Artist - Title [key bpm].ext' or 'Artist - Title (Version).ext'."""
    stem = Path(filename).stem

    # Extract key/bpm tag like [9A 120], [6A --]
    key_bpm = re.search(r'\[(\d{1,2}[AB]|--)\s+(--|\d{2,3})\]', stem)
    key = key_bpm.group(1) if key_bpm and key_bpm.group(1) != "--" else ""
    bpm = key_bpm.group(2) if key_bpm and key_bpm.group(2) != "--" else ""
    stem_clean = re.sub(r'\s*\[(?:\d{1,2}[AB]|--)\s+(?:--|\d{2,3})\]\s*', '', stem).strip()

    parts = [p.strip() for p in stem_clean.split(" - ")]
    artist = parts[0] if len(parts) >= 1 else ""
    title = parts[1] if len(parts) >= 2 else stem_clean
    version = ""

    if len(parts) >= 3:
        version = " - ".join(parts[2:])

    # Extract version from parentheses in title
    paren = re.search(
        r'\(([^)]+(?:Remix|Edit|Mix|Version|Bootleg|Rework|Refix|Flip)[^)]*)\)',
        title, re.IGNORECASE,
    )
    if paren and not version:
        version = paren.group(1)
        title = title[:paren.start()].strip()

    return {"artist": artist, "title": title, "version": version, "bpm": bpm, "key": key}


def load_csv_tracks(csv_path: str, limit: int = 20) -> List[Dict[str, str]]:
    """Load tracks from any CSV file."""
    tracks = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            artist = (row.get("artist") or row.get("artist_suggest") or
                      row.get("tag_artist_original") or "").strip()
            title = (row.get("title") or row.get("title_suggest") or
                     row.get("tag_title_original") or "").strip()
            version = (row.get("version_info") or row.get("version_suggest") or "").strip()
            genre = (row.get("genre") or row.get("genre_suggest") or row.get("ai_genre") or "").strip()

            if not artist or not title:
                continue

            tracks.append({
                "artist": artist,
                "title": title,
                "version": version,
                "expected_genre": genre or "Unknown",
                "bpm": (row.get("bpm") or row.get("tag_bpm") or "").strip(),
            })

            if len(tracks) >= limit:
                break
    return tracks


# ── Track ID (stable key for results JSON) ──────────────────────────────────

def track_key(t: Dict[str, str]) -> str:
    """Stable key for a track: 'artist|title|version'."""
    a = (t.get("artist") or "").strip().lower()
    tl = (t.get("title") or "").strip().lower()
    v = (t.get("version") or "").strip().lower()
    return f"{a}|{tl}|{v}"


# ── Web search phase ─────────────────────────────────────────────────────────

def run_search_phase(
    tracks: List[Dict[str, str]],
    backend_names: List[str],
    max_queries: int = 3,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Run web search for all tracks × backends.

    Returns:
        {backend_name: {track_key: {
            "num_results": int,
            "time_ms": int,
            "queries_made": int,
            "prompt_context": str,
            "results": [...],
            "beatport_hits": int,
            "sources": {source_name: count},  # e.g. {"beatport": 2, "wikipedia": 1, "djcity": 1}
        }}}
    """
    all_results: Dict[str, Dict[str, Dict[str, Any]]] = {}

    for bname in backend_names:
        try:
            backend = create_searcher(bname)
        except (ValueError, ImportError) as e:
            print(f"  ⚠ Skipping {bname}: {e}", file=sys.stderr)
            continue

        if not backend.is_available():
            print(f"  ⚠ Skipping {bname}: not available (API key or Docker?)", file=sys.stderr)
            continue

        print(f"\n{'━'*60}", file=sys.stderr)
        print(f"  🔍 SEARCH: {bname.upper()}", file=sys.stderr)
        print(f"{'━'*60}", file=sys.stderr)

        backend_results: Dict[str, Dict[str, Any]] = {}

        for i, track in enumerate(tracks):
            artist = track.get("artist", "")
            title = track.get("title", "")
            version = track.get("version", "")
            label = f"{artist} - {title}"
            if version:
                label += f" ({version[:25]})"

            print(f"  [{i+1}/{len(tracks)}] {label[:55]}...", end=" ", file=sys.stderr, flush=True)

            t0 = time.time()
            try:
                sr = search_track_genre(
                    backend,
                    artist=artist,
                    title=title,
                    version=version,
                    max_queries=max_queries,
                )
            except Exception as e:
                print(f"ERROR: {e}", file=sys.stderr)
                sr = TrackSearchResults(artist=artist, title=title, version=version)

            elapsed_ms = int((time.time() - t0) * 1000)

            # Count source types dynamically
            from collections import Counter
            source_counts = Counter(r.source for r in sr.results)
            bp = source_counts.get("beatport", 0)

            sources_str = " ".join(f"{s}:{c}" for s, c in source_counts.most_common(5))
            print(f"{len(sr.results)} results ({sources_str}) {elapsed_ms}ms", file=sys.stderr)

            backend_results[track_key(track)] = {
                "num_results": len(sr.results),
                "time_ms": elapsed_ms,
                "queries_made": sr.queries_made,
                "prompt_context": sr.to_prompt_context(),
                "results": [r.to_dict() for r in sr.results],
                "beatport_hits": bp,
                "sources": dict(source_counts),
            }

        all_results[bname] = backend_results

    return all_results


# ── LLM classification phase ────────────────────────────────────────────────

def classify_with_context(
    track: Dict[str, str],
    search_context: str,
    api_key: str,
    model: str,
    genre_labels: List[str],
) -> Dict[str, Any]:
    """Classify a track with injected search context via Chat Completions."""
    from djlib.ai_classify import _parse_json_content, _validate_result
    import requests as http_requests

    genre_list = ", ".join(genre_labels)

    # Build track info
    parts = []
    if track.get("artist"):
        parts.append(f"Artist: {track['artist']}")
    if track.get("title"):
        parts.append(f"Title: {track['title']}")
    if track.get("version"):
        parts.append(f"Version/Remix: {track['version']}")
    if track.get("bpm"):
        parts.append(f"BPM: {track['bpm']}")
    if track.get("key"):
        parts.append(f"Key: {track['key']}")
    track_info = "\n".join(parts)

    version_str = track.get("version", "")
    is_remix = bool(re.search(
        r'\b(?:remix|edit|bootleg|rework|refix|mashup|flip)\b',
        version_str, re.IGNORECASE,
    ))

    remix_note = ""
    if is_remix:
        remix_note = (
            "\n\nCRITICAL — this is a REMIX/EDIT. Classify by the REMIX STYLE, "
            "NOT the original track's genre. The remixer's known scene is the strongest signal."
        )

    bpm_guide = (
        "\nBPM ranges: 70-100 Hip-Hop/R&B/Reggaeton, 100-115 UK Garage/Afrobeats, "
        "115-126 Deep House, 120-128 House/Tech House, 124-132 Melodic Techno/Prog House, "
        "128-140 Techno/Trance, 150-180 D&B"
    )

    system_prompt = (
        f"You are an expert DJ music classifier. Classify tracks into exactly ONE of these genres:\n"
        f"{genre_list}\n\n"
        f"Genre signals (strongest to weakest):\n"
        f"1. Remixer identity (for remixes)\n"
        f"2. Beatport genre (strongest for EDM)\n"
        f"3. SoundCloud tags (strong for remixes)\n"
        f"4. BPM + artist identity\n"
        f"5. Discogs style (good for non-EDM)\n"
        f"6. General knowledge"
        f"{remix_note}{bpm_guide}\n\n"
        f"Respond ONLY with valid JSON:\n"
        f'{{\"genre\": \"Exact Genre\", \"confidence\": 0.0-1.0, \"reasoning\": \"1-2 sentences\"}}'
    )

    user_prompt = f"Track:\n{track_info}\n\n"
    if search_context and search_context != "(no search)":
        user_prompt += f"Web search results:\n{search_context}\n\n"
    user_prompt += "Classify this track's genre."

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    is_reasoning = model.startswith("gpt-5") or model.startswith("o")
    token_param = "max_completion_tokens" if is_reasoning else "max_tokens"
    token_limit = 4000 if is_reasoning else 400

    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        token_param: token_limit,
    }
    if not is_reasoning:
        body["temperature"] = 0.2

    t0 = time.time()
    resp = http_requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=90 if is_reasoning else 30,
    )
    elapsed = time.time() - t0
    resp.raise_for_status()
    data = resp.json()

    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    result = _parse_json_content(content)
    result = _validate_result(result, genre_labels)

    usage = data.get("usage", {})
    result["_usage"] = {
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "reasoning_tokens": usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0),
        "model": model,
        "time_ms": int(elapsed * 1000),
    }
    return result


def run_llm_phase(
    tracks: List[Dict[str, str]],
    search_results: Dict[str, Dict[str, Dict[str, Any]]],
    backend_names: List[str],
    api_key: str,
    model: str,
    delay: float = 0.3,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Run LLM classification for each track × backend combo.

    Also runs a "none" (no search) baseline.

    Returns:
        {variant_name: {track_key: {
            "genre": str,
            "confidence": float,
            "reasoning": str,
            "_usage": {...},
        }}}
    """
    genre_labels = load_genre_labels()
    variants = backend_names + ["none"]
    all_classifications: Dict[str, Dict[str, Dict[str, Any]]] = {}

    for variant in variants:
        print(f"\n{'━'*60}", file=sys.stderr)
        print(f"  🤖 CLASSIFY: {variant.upper()}" + (" (baseline, no search)" if variant == "none" else ""), file=sys.stderr)
        print(f"{'━'*60}", file=sys.stderr)

        variant_results: Dict[str, Dict[str, Any]] = {}

        for i, track in enumerate(tracks):
            tk = track_key(track)
            label = f"{track['artist']} - {track['title']}"
            if track.get("version"):
                label += f" ({track['version'][:20]})"

            print(f"  [{i+1}/{len(tracks)}] {label[:55]}...", end=" ", file=sys.stderr, flush=True)

            # Get search context for this variant
            if variant == "none":
                context = "(no search)"
            else:
                sr = search_results.get(variant, {}).get(tk, {})
                context = sr.get("prompt_context", "(no results)")

            try:
                result = classify_with_context(track, context, api_key, model, genre_labels)
                genre = result.get("genre", "?")
                conf = result.get("confidence", 0)
                expected = track.get("expected_genre", "?")
                match = "✅" if genre.lower() == expected.lower() else "❌"
                print(f"{match} {genre} ({conf:.0%})", file=sys.stderr)
            except Exception as e:
                result = {"genre": "ERROR", "confidence": 0, "reasoning": str(e), "error": str(e)}
                print(f"ERROR: {e}", file=sys.stderr)

            variant_results[tk] = result
            time.sleep(delay)

        all_classifications[variant] = variant_results

    return all_classifications


# ── Results persistence ──────────────────────────────────────────────────────

def load_results() -> Dict[str, Any]:
    """Load existing results from JSON."""
    if RESULTS_FILE.exists():
        with open(RESULTS_FILE) as f:
            return json.load(f)
    return {"tracks": {}, "search": {}, "classify": {}, "runs": []}


def save_results(results: Dict[str, Any]) -> None:
    """Save results to JSON (append-safe by track_key × variant)."""
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


def merge_results(
    existing: Dict[str, Any],
    tracks: List[Dict[str, str]],
    search_results: Dict[str, Dict[str, Dict[str, Any]]],
    classifications: Dict[str, Dict[str, Dict[str, Any]]],
) -> Dict[str, Any]:
    """Merge new results into existing JSON structure."""
    # Store track metadata
    for t in tracks:
        tk = track_key(t)
        existing.setdefault("tracks", {})[tk] = {
            "artist": t.get("artist", ""),
            "title": t.get("title", ""),
            "version": t.get("version", ""),
            "expected_genre": t.get("expected_genre", ""),
            "bpm": t.get("bpm", ""),
        }

    # Store search results (without prompt_context to save space)
    for backend, results in search_results.items():
        existing.setdefault("search", {})[backend] = existing.get("search", {}).get(backend, {})
        for tk, data in results.items():
            existing["search"][backend][tk] = {
                "num_results": data["num_results"],
                "time_ms": data["time_ms"],
                "queries_made": data["queries_made"],
                "beatport_hits": data.get("beatport_hits", 0),
                "sources": data.get("sources", {}),
                # Keep first 3 result titles for reference
                "top_results": [
                    {"title": r["title"][:80], "source": r["source"], "url": r["url"]}
                    for r in data.get("results", [])[:3]
                ],
            }

    # Store classifications
    for variant, results in classifications.items():
        existing.setdefault("classify", {})[variant] = existing.get("classify", {}).get(variant, {})
        for tk, data in results.items():
            existing["classify"][variant][tk] = {
                "genre": data.get("genre", ""),
                "confidence": data.get("confidence", 0),
                "reasoning": data.get("reasoning", "")[:200],
                "usage": data.get("_usage", {}),
            }

    # Record run
    existing.setdefault("runs", []).append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "track_count": len(tracks),
        "backends": list(search_results.keys()),
        "variants": list(classifications.keys()),
    })

    return existing


# ── Reporting ────────────────────────────────────────────────────────────────

def print_search_summary(
    tracks: List[Dict[str, str]],
    search_results: Dict[str, Dict[str, Dict[str, Any]]],
) -> None:
    """Print search quality comparison table."""
    backends = list(search_results.keys())
    if not backends:
        return

    print(f"\n{'='*80}")
    print("  SEARCH RESULTS COMPARISON")
    print(f"{'='*80}\n")

    # Per-track table
    # Header
    col_w = 14
    header = f"  {'Track':<42}"
    for b in backends:
        header += f" │ {b.upper():^{col_w}}"
    print(header)
    print(f"  {'─'*42}" + "─┼─".join(["─" * col_w for _ in backends]) + "─")

    totals = {b: {"results": 0, "bp": 0, "sources": Counter(), "time": 0} for b in backends}

    for track in tracks:
        tk = track_key(track)
        label = f"{track['artist']} - {track['title']}"
        if track.get("version"):
            label += f" ({track['version'][:15]})"
        label = label[:41]

        row = f"  {label:<42}"
        for b in backends:
            sr = search_results.get(b, {}).get(tk, {})
            n = sr.get("num_results", 0)
            bp = sr.get("beatport_hits", 0)
            ms = sr.get("time_ms", 0)
            sources = sr.get("sources", {})

            totals[b]["results"] += n
            totals[b]["bp"] += bp
            totals[b]["sources"].update(sources)
            totals[b]["time"] += ms

            # Format: "3 (2 BP) 1.2s" or "3 (wiki+djcity) 1.2s"
            if bp:
                detail = f"{bp}BP"
            else:
                top_sources = sorted(sources.items(), key=lambda x: -x[1])[:2]
                detail = "+".join(s[:4] for s, _ in top_sources) if top_sources else "-"
            cell = f"{n} ({detail}) {ms/1000:.1f}s"
            row += f" │ {cell:^{col_w}}"
        print(row)

    # Totals
    print(f"  {'─'*42}" + "─┼─".join(["─" * col_w for _ in backends]) + "─")
    row = f"  {'TOTAL':<42}"
    for b in backends:
        t = totals[b]
        top3 = t["sources"].most_common(3)
        src_str = " ".join(f"{s}:{c}" for s, c in top3)
        cell = f"{t['results']}r {src_str}"
        row += f" │ {cell:^{col_w}}"
    print(row)

    row = f"  {'AVG TIME':<42}"
    n_tracks = len(tracks) or 1
    for b in backends:
        avg_ms = totals[b]["time"] / n_tracks
        cell = f"{avg_ms:.0f}ms"
        row += f" │ {cell:^{col_w}}"
    print(row)

    # Source distribution
    print(f"\n  Source breakdown:")
    for b in backends:
        t = totals[b]
        total = t["results"] or 1
        src = t["sources"]
        top_sources = src.most_common(8)
        parts = [f"{name}: {cnt} [{cnt/total*100:.0f}%]" for name, cnt in top_sources]
        print(f"    {b.upper():>8}: {t['results']} results ({', '.join(parts)})")


def print_classification_matrix(
    tracks: List[Dict[str, str]],
    classifications: Dict[str, Dict[str, Dict[str, Any]]],
) -> None:
    """Print the main comparison matrix: Track × Variant → Genre."""
    variants = list(classifications.keys())
    if not variants:
        return

    print(f"\n{'='*80}")
    print("  GENRE CLASSIFICATION COMPARISON")
    print(f"{'='*80}\n")

    # Column width
    cw = 18
    header = f"  {'Track':<32} {'Expected':<18}"
    for v in variants:
        header += f" │ {v.upper():^{cw}}"
    print(header)
    print(f"  {'─'*32} {'─'*18}" + "─┼─".join(["─" * cw for _ in variants]) + "─")

    # Accuracy counters
    accuracy = {v: 0 for v in variants}
    total_tokens_in = {v: 0 for v in variants}
    total_tokens_out = {v: 0 for v in variants}
    total_time = {v: 0 for v in variants}
    agreement_count = 0  # How often all variants agree

    for track in tracks:
        tk = track_key(track)
        label = f"{track['artist']} - {track['title']}"[:31]
        expected = track.get("expected_genre", "?")[:17]

        row = f"  {label:<32} {expected:<18}"
        genres_this_track = []

        for v in variants:
            c = classifications.get(v, {}).get(tk, {})
            genre = c.get("genre", "—")
            conf = c.get("confidence", 0)
            genres_this_track.append(genre.lower())

            is_correct = genre.lower() == track.get("expected_genre", "").lower()
            if is_correct:
                accuracy[v] += 1
                marker = "✅"
            else:
                marker = "❌"

            cell = f"{marker} {genre[:14]}"
            row += f" │ {cell:<{cw}}"

            usage = c.get("usage", c.get("_usage", {}))
            total_tokens_in[v] += usage.get("input_tokens", 0)
            total_tokens_out[v] += usage.get("output_tokens", 0) + usage.get("reasoning_tokens", 0)
            total_time[v] += usage.get("time_ms", 0)

        # Check agreement
        unique_genres = set(genres_this_track)
        if len(unique_genres) == 1:
            agreement_count += 1

        print(row)

    # Summary
    total = len(tracks) or 1
    print(f"  {'─'*32} {'─'*18}" + "─┼─".join(["─" * cw for _ in variants]) + "─")

    # Accuracy row
    row = f"  {'ACCURACY':<32} {'':18}"
    for v in variants:
        pct = accuracy[v] / total * 100
        cell = f"{accuracy[v]}/{total} ({pct:.0f}%)"
        row += f" │ {cell:^{cw}}"
    print(row)

    # Cost row
    print(f"\n  {'─'*60}")
    print(f"  Cost & performance:")
    for v in variants:
        ti = total_tokens_in[v]
        to = total_tokens_out[v]
        # gpt-5-nano: $0.10/1M in, $0.40/1M out
        cost = ti * 0.10 / 1_000_000 + to * 0.40 / 1_000_000
        avg_ms = total_time[v] / total
        print(f"    {v.upper():>8}: {ti:>6} in + {to:>6} out tokens = ${cost:.4f} | avg {avg_ms:.0f}ms/track")

    # Agreement
    print(f"\n  Cross-variant agreement: {agreement_count}/{total} tracks ({agreement_count/total*100:.0f}%) — "
          f"all variants returned same genre")


def print_disagreement_details(
    tracks: List[Dict[str, str]],
    classifications: Dict[str, Dict[str, Dict[str, Any]]],
) -> None:
    """Print details for tracks where variants disagree."""
    variants = list(classifications.keys())
    if not variants:
        return

    disagreements = []
    for track in tracks:
        tk = track_key(track)
        genres = {}
        for v in variants:
            c = classifications.get(v, {}).get(tk, {})
            genres[v] = c.get("genre", "—")

        unique = set(g.lower() for g in genres.values())
        if len(unique) > 1:
            disagreements.append((track, genres))

    if not disagreements:
        print(f"\n  ✅ No disagreements — all variants agree on all tracks!")
        return

    print(f"\n{'='*80}")
    print(f"  DISAGREEMENTS ({len(disagreements)} tracks)")
    print(f"{'='*80}\n")

    for track, genres in disagreements:
        label = f"{track['artist']} - {track['title']}"
        if track.get("version"):
            label += f" ({track['version'][:20]})"
        expected = track.get("expected_genre", "?")
        print(f"  📀 {label}")
        print(f"     Expected: {expected}")
        for v in variants:
            g = genres[v]
            match = "✅" if g.lower() == expected.lower() else "❌"
            # Get reasoning
            tk = track_key(track)
            c = classifications.get(v, {}).get(tk, {})
            reasoning = c.get("reasoning", "")[:100]
            print(f"     {v:>8}: {match} {g:<20} — {reasoning}")
        print()


def export_csv_report(
    tracks: List[Dict[str, str]],
    search_results: Dict[str, Dict[str, Dict[str, Any]]],
    classifications: Dict[str, Dict[str, Dict[str, Any]]],
) -> None:
    """Export full comparison to CSV for spreadsheet analysis."""
    backends = list(search_results.keys())
    variants = list(classifications.keys())

    rows = []
    for track in tracks:
        tk = track_key(track)
        row: Dict[str, Any] = {
            "artist": track.get("artist", ""),
            "title": track.get("title", ""),
            "version": track.get("version", ""),
            "expected_genre": track.get("expected_genre", ""),
            "bpm": track.get("bpm", ""),
        }

        # Search data per backend
        for b in backends:
            sr = search_results.get(b, {}).get(tk, {})
            row[f"search_{b}_results"] = sr.get("num_results", 0)
            row[f"search_{b}_beatport"] = sr.get("beatport_hits", 0)
            sources = sr.get("sources", {})
            row[f"search_{b}_sources"] = "|".join(f"{s}:{c}" for s, c in sorted(sources.items()))
            row[f"search_{b}_time_ms"] = sr.get("time_ms", 0)

        # Classification data per variant
        for v in variants:
            c = classifications.get(v, {}).get(tk, {})
            row[f"genre_{v}"] = c.get("genre", "")
            row[f"conf_{v}"] = c.get("confidence", 0)
            row[f"correct_{v}"] = (
                1 if c.get("genre", "").lower() == track.get("expected_genre", "").lower() else 0
            )
            row[f"reasoning_{v}"] = c.get("reasoning", "")[:150]

        rows.append(row)

    if not rows:
        return

    CSV_REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CSV_REPORT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n  📊 CSV report saved to {CSV_REPORT_FILE}", file=sys.stderr)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="A/B test web search backends for genre classification",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Search only (free):\n"
            "  .venv/bin/python scripts/ab_test_web_search.py --search-only --limit 10\n\n"
            "  # Full comparison:\n"
            "  .venv/bin/python scripts/ab_test_web_search.py --limit 10\n\n"
            "  # Just print previous results:\n"
            "  .venv/bin/python scripts/ab_test_web_search.py --report\n"
        ),
    )
    parser.add_argument(
        "--backends", nargs="+", default=None,
        help=f"Backends to compare (default: all available). Options: {', '.join(list_backends())}",
    )
    parser.add_argument(
        "--search-only", action="store_true",
        help="Only run web search, skip LLM classification (free)",
    )
    parser.add_argument(
        "--classify-only", action="store_true",
        help="Skip search phase, reuse search results from previous run (JSON)",
    )
    parser.add_argument(
        "--source", choices=["ab_test", "builtin", "csv"], default=None,
        help="Track source: ab_test (data/ab_test/ folders), builtin (hardcoded), csv (--csv path)",
    )
    parser.add_argument(
        "--csv", help="Load test tracks from CSV file",
    )
    parser.add_argument(
        "--limit", type=int, default=10,
        help="Max tracks to test (default: 10)",
    )
    parser.add_argument(
        "--model", default="",
        help="LLM model override (default: config ai_quick_model)",
    )
    parser.add_argument(
        "--delay", type=float, default=0.3,
        help="Seconds between LLM calls (default: 0.3)",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Skip tracks already in results.json",
    )
    parser.add_argument(
        "--report", action="store_true",
        help="Just print previous results, no API calls",
    )
    parser.add_argument(
        "--json", metavar="PATH",
        help="Save raw results to specific JSON path",
    )
    args = parser.parse_args()

    # ── Report mode ──
    if args.report:
        data = load_results()
        if not data.get("tracks"):
            print("No previous results found. Run a test first.", file=sys.stderr)
            sys.exit(1)

        # Reconstruct tracks list from stored data
        tracks = []
        for tk, info in data["tracks"].items():
            tracks.append(info)

        print(f"Loaded {len(tracks)} tracks from previous results.", file=sys.stderr)

        if data.get("search"):
            print_search_summary(tracks, data["search"])
        if data.get("classify"):
            print_classification_matrix(tracks, data["classify"])
            print_disagreement_details(tracks, data["classify"])
        return

    # ── Determine backends ──
    backend_names = args.backends or list_backends()

    # ── Determine tracks ──
    source = args.source
    if args.csv:
        source = "csv"
    elif source is None:
        # Auto-detect: prefer ab_test if it has tracks
        ab_tracks = discover_ab_tracks()
        if ab_tracks:
            source = "ab_test"
        else:
            source = "builtin"

    if source == "ab_test":
        tracks = discover_ab_tracks()
        if not tracks:
            print("No tracks in data/ab_test/<genre>/. Place audio files there.", file=sys.stderr)
            sys.exit(1)
        print(f"Using {len(tracks)} tracks from data/ab_test/ (ground truth from folders)", file=sys.stderr)
    elif source == "csv":
        if not args.csv:
            print("--csv required with --source csv", file=sys.stderr)
            sys.exit(1)
        tracks = load_csv_tracks(args.csv, limit=args.limit)
        print(f"Loaded {len(tracks)} tracks from {args.csv}", file=sys.stderr)
    else:
        tracks = BUILTIN_TRACKS[:]
        print(f"Using {len(tracks)} built-in test tracks", file=sys.stderr)

    # Apply limit
    if args.limit and len(tracks) > args.limit:
        tracks = tracks[:args.limit]
        print(f"Limited to {args.limit} tracks", file=sys.stderr)

    if not tracks:
        print("No tracks to test!", file=sys.stderr)
        sys.exit(1)

    # Resume: filter out already-tested tracks
    existing = load_results() if args.resume else {"tracks": {}, "search": {}, "classify": {}, "runs": []}
    if args.resume:
        already_done = set()
        # A track is "done" if it has classification results for all requested variants
        needed_variants = set(backend_names + ["none"])
        for tk in existing.get("classify", {}).get("none", {}).keys():
            variants_done = set()
            for v in needed_variants:
                if tk in existing.get("classify", {}).get(v, {}):
                    variants_done.add(v)
            if variants_done >= needed_variants:
                already_done.add(tk)

        before = len(tracks)
        tracks = [t for t in tracks if track_key(t) not in already_done]
        skipped = before - len(tracks)
        if skipped:
            print(f"Resuming: skipping {skipped} already-tested tracks, {len(tracks)} remaining", file=sys.stderr)

    if not tracks:
        print("All tracks already tested. Use --report to see results.", file=sys.stderr)
        return

    # ── Run search phase ──
    model = args.model or get_ai_quick_model()
    print(f"\nComparing {len(backend_names)} backends × {len(tracks)} tracks", file=sys.stderr)
    print(f"Backends: {', '.join(backend_names)}", file=sys.stderr)
    if not args.search_only:
        print(f"Model: {model}", file=sys.stderr)
        est_calls = len(tracks) * (len(backend_names) + 1)  # +1 for baseline
        # gpt-5-nano ~1500 tokens avg/call → ~$0.0007/call
        est_cost = est_calls * 0.0007
        print(f"Estimated: {est_calls} LLM calls ≈ ${est_cost:.3f}", file=sys.stderr)

    if args.classify_only:
        # Reuse search results from previous run
        prev = load_results()
        search_results = prev.get("search", {})
        # Filter to requested backends only
        search_results = {b: search_results[b] for b in backend_names if b in search_results}
        if not search_results:
            print("No previous search results found! Run search phase first.", file=sys.stderr)
            sys.exit(1)
        found_backends = list(search_results.keys())
        print(f"Loaded search results from JSON: {', '.join(found_backends)} ({len(next(iter(search_results.values())))} tracks)", file=sys.stderr)
    else:
        search_results = run_search_phase(tracks, backend_names)
    print_search_summary(tracks, search_results)

    # ── Run classification phase ──
    classifications: Dict[str, Dict[str, Dict[str, Any]]] = {}
    if not args.search_only:
        api_key = get_openai_api_key()
        if not api_key:
            print("\n⚠ No OpenAI API key — skipping LLM classification.", file=sys.stderr)
        else:
            available_backends = list(search_results.keys())
            classifications = run_llm_phase(
                tracks, search_results, available_backends,
                api_key, model, delay=args.delay,
            )
            print_classification_matrix(tracks, classifications)
            print_disagreement_details(tracks, classifications)
            export_csv_report(tracks, search_results, classifications)

    # ── Save results ──
    merged = merge_results(existing, tracks, search_results, classifications)
    save_path = args.json or str(RESULTS_FILE)
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "w") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
    print(f"\n  💾 Results saved to {save_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
