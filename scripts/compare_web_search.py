#!/usr/bin/env python3
"""Compare web search backends for genre classification quality.

Runs the same set of test tracks through all available backends,
collects search snippets, and optionally feeds them to GPT-5-nano
for genre classification.

Usage:
    # Just compare search quality (no LLM, free):
    .venv/bin/python scripts/compare_web_search.py --search-only

    # Full comparison with LLM classification:
    .venv/bin/python scripts/compare_web_search.py

    # Compare specific backends:
    .venv/bin/python scripts/compare_web_search.py --backends ddg brave

    # With custom test tracks from CSV:
    .venv/bin/python scripts/compare_web_search.py --csv data/library.csv --limit 20
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from djlib.metadata.web_search import (
    SearchBackend,
    create_searcher,
    list_backends,
    search_track_genre,
)

# ── Test dataset ─────────────────────────────────────────────────────────────

# Curated test tracks with known genres (ground truth).
# Mix of originals, remixes, non-EDM, obscure tracks.
TEST_TRACKS = [
    # === EDM originals (should be easy) ===
    {
        "artist": "deadmau5",
        "title": "Strobe",
        "version": "",
        "expected_genre": "Progressive House",
    },
    {
        "artist": "Fisher",
        "title": "Losing It",
        "version": "",
        "expected_genre": "Tech House",
    },
    {
        "artist": "Peggy Gou",
        "title": "Starry Night",
        "version": "",
        "expected_genre": "House",
    },
    {
        "artist": "Charlotte de Witte",
        "title": "Doppler",
        "version": "",
        "expected_genre": "Techno",
    },
    # === Remixes (tricky — remix genre != original genre) ===
    {
        "artist": "Kanye West",
        "title": "Runaway",
        "version": "Vintage Culture Remix",
        "expected_genre": "Tech House",
    },
    {
        "artist": "Modjo",
        "title": "Lady (Hear Me Tonight)",
        "version": "Meduza Remix",
        "expected_genre": "House",
    },
    {
        "artist": "Arctic Monkeys",
        "title": "Do I Wanna Know?",
        "version": "Adriatique & Dardust Remix",
        "expected_genre": "Melodic House & Techno",
    },
    # === Non-EDM (needs Discogs/generic search) ===
    {
        "artist": "Nirvana",
        "title": "Smells Like Teen Spirit",
        "version": "",
        "expected_genre": "Rock",
    },
    {
        "artist": "Kendrick Lamar",
        "title": "HUMBLE.",
        "version": "",
        "expected_genre": "Hip-Hop",
    },
    {
        "artist": "Norah Jones",
        "title": "Don't Know Why",
        "version": "",
        "expected_genre": "Jazz",
    },
    # === Obscure / edge cases ===
    {
        "artist": "ARTBAT",
        "title": "Flame",
        "version": "",
        "expected_genre": "Melodic House & Techno",
    },
    {
        "artist": "Bonobo",
        "title": "Kerala",
        "version": "",
        "expected_genre": "Downtempo",
    },
]


# ── Search comparison ────────────────────────────────────────────────────────


def compare_search_backends(
    tracks: List[Dict[str, str]],
    backend_names: List[str],
    max_queries: int = 3,
) -> Dict[str, List[Dict[str, Any]]]:
    """Run tracks through multiple backends and collect results.

    Returns:
        Dict mapping backend_name → list of result dicts per track.
    """
    all_results: Dict[str, List[Dict[str, Any]]] = {}

    for name in backend_names:
        try:
            backend = create_searcher(name)
        except (ValueError, ImportError) as e:
            print(f"  ⚠ Skipping {name}: {e}", file=sys.stderr)
            continue

        if not backend.is_available():
            print(f"  ⚠ Skipping {name}: not available (missing API key or Docker?)", file=sys.stderr)
            continue

        print(f"\n{'='*60}", file=sys.stderr)
        print(f"Backend: {name.upper()}", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)

        results_for_backend = []
        for i, track in enumerate(tracks):
            artist = track.get("artist", "")
            title = track.get("title", "")
            version = track.get("version", "")
            expected = track.get("expected_genre", "?")

            label = f"{artist} - {title}"
            if version:
                label += f" ({version})"

            print(f"  [{i+1}/{len(tracks)}] {label}...", end=" ", file=sys.stderr, flush=True)

            t0 = time.time()
            sr = search_track_genre(
                backend,
                artist=artist,
                title=title,
                version=version,
                max_queries=max_queries,
            )
            elapsed = time.time() - t0

            n_results = len(sr.results)
            print(f"{n_results} results ({elapsed:.1f}s)", file=sys.stderr)

            results_for_backend.append({
                "track": track,
                "expected_genre": expected,
                "num_results": n_results,
                "queries_made": sr.queries_made,
                "time_ms": sr.search_time_ms,
                "prompt_context": sr.to_prompt_context(),
                "results": [r.to_dict() for r in sr.results],
            })

        all_results[name] = results_for_backend

    return all_results


def classify_with_llm(
    track: Dict[str, str],
    search_context: str,
    api_key: str,
    model: str = "",
) -> Dict[str, Any]:
    """Classify a track using GPT-5-nano with search context injected."""
    from djlib.ai_classify import (
        load_genre_labels,
        _call_openai_chat as _call_chat,
        _validate_result,
        _parse_json_content,
    )
    from djlib.config import get_ai_quick_model

    genre_labels = load_genre_labels()
    genre_list = ", ".join(genre_labels)
    model = model or get_ai_quick_model()

    artist = track.get("artist", "")
    title = track.get("title", "")
    version = track.get("version", "")

    # Build parts from track info
    parts = []
    if artist:
        parts.append(f"Artist: {artist}")
    if title:
        parts.append(f"Title: {title}")
    if version:
        parts.append(f"Version: {version}")

    track_info = "\n".join(parts)

    system_prompt = (
        "You are a DJ music genre classifier. Given track metadata AND web search "
        "results about the track, classify it into exactly ONE genre.\n\n"
        f"Allowed genres: {genre_list}\n\n"
        "RULES:\n"
        "- For remixes, classify by the REMIX STYLE, not the original genre\n"
        "- Beatport genre is the strongest signal for EDM tracks\n"
        "- SoundCloud tags are strong for remixes\n"
        "- Discogs style is good for non-EDM\n"
        "- If web search results conflict, prefer Beatport > SoundCloud > Discogs\n\n"
        "Respond ONLY with valid JSON:\n"
        '{"genre": "Exact Genre From List", "confidence": 0.0-1.0, '
        '"reasoning": "1-2 sentences"}'
    )

    user_prompt = (
        f"Track information:\n{track_info}\n\n"
        f"Web search results:\n{search_context}\n\n"
        "Classify this track's genre."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    is_reasoning = model.startswith("gpt-5") or model.startswith("o")
    import requests as http_requests

    token_param = "max_completion_tokens" if is_reasoning else "max_tokens"
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

    result = _parse_json_content(content)
    result = _validate_result(result, genre_labels)

    usage = data.get("usage", {})
    result["_usage"] = {
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "model": model,
    }
    return result


# ── Reporting ────────────────────────────────────────────────────────────────


def print_search_report(all_results: Dict[str, List[Dict[str, Any]]]) -> None:
    """Print a comparison table of search results across backends."""
    backends = list(all_results.keys())
    if not backends:
        print("No backends available!", file=sys.stderr)
        return

    # Use first backend's track list as reference
    tracks = [r["track"] for r in all_results[backends[0]]]

    print("\n" + "="*80)
    print("SEARCH RESULTS COMPARISON")
    print("="*80)

    # Summary table header
    header = f"{'Track':<45}"
    for b in backends:
        header += f" | {b:>8} results | {b:>6} ms"
    print(header)
    print("-" * len(header))

    for i, track in enumerate(tracks):
        label = f"{track['artist']} - {track['title']}"
        if track.get("version"):
            label += f" ({track['version'][:20]})"
        label = label[:44]

        row = f"{label:<45}"
        for b in backends:
            r = all_results[b][i]
            row += f" | {r['num_results']:>14} | {r['time_ms']:>8}"
        print(row)

    # Totals
    print("-" * len(header))
    totals_row = f"{'TOTAL':<45}"
    for b in backends:
        total_results = sum(r["num_results"] for r in all_results[b])
        total_time = sum(r["time_ms"] for r in all_results[b])
        totals_row += f" | {total_results:>14} | {total_time:>8}"
    print(totals_row)

    # Per-backend snippet quality samples
    for b in backends:
        print(f"\n{'─'*60}")
        print(f"Sample snippets from {b.upper()}:")
        print(f"{'─'*60}")
        for r in all_results[b][:3]:
            track = r["track"]
            label = f"{track['artist']} - {track['title']}"
            print(f"\n  📀 {label}")
            for sr in r["results"][:2]:
                print(f"    [{sr['source'].upper()}] {sr['title'][:60]}")
                if sr["snippet"]:
                    print(f"    └─ {sr['snippet'][:120]}")


def print_classification_report(
    classifications: Dict[str, List[Dict[str, Any]]],
) -> None:
    """Print LLM classification accuracy comparison."""
    backends = list(classifications.keys())
    if not backends:
        return

    print("\n" + "="*80)
    print("GENRE CLASSIFICATION COMPARISON (with GPT-5-nano)")
    print("="*80)

    header = f"{'Track':<40} {'Expected':<22}"
    for b in backends:
        header += f" | {b:>8} genre"
    print(header)
    print("-" * len(header))

    accuracy = {b: 0 for b in backends}
    total_tokens = {b: {"in": 0, "out": 0} for b in backends}

    tracks = [c["track"] for c in classifications[backends[0]]]
    for i, track in enumerate(tracks):
        label = f"{track['artist']} - {track['title']}"[:39]
        expected = track.get("expected_genre", "?")[:21]
        row = f"{label:<40} {expected:<22}"

        for b in backends:
            c = classifications[b][i]
            genre = c.get("genre", "ERROR")[:15]
            conf = c.get("confidence", 0)
            is_correct = genre.lower() == track.get("expected_genre", "").lower()
            marker = "✅" if is_correct else "❌"
            if is_correct:
                accuracy[b] += 1
            row += f" | {marker} {genre:<13}"

            usage = c.get("_usage", {})
            total_tokens[b]["in"] += usage.get("input_tokens", 0)
            total_tokens[b]["out"] += usage.get("output_tokens", 0)

        print(row)

    # Summary
    total = len(tracks)
    print("-" * len(header))
    acc_row = f"{'ACCURACY':<40} {'':<22}"
    for b in backends:
        pct = accuracy[b] / total * 100 if total else 0
        acc_row += f" | {accuracy[b]}/{total} ({pct:.0f}%)    "
    print(acc_row)

    # Token usage / cost
    print(f"\n{'Token usage & estimated cost':}")
    for b in backends:
        t = total_tokens[b]
        # gpt-5-nano pricing: $0.10/1M in, $0.40/1M out
        cost = t["in"] * 0.10 / 1_000_000 + t["out"] * 0.40 / 1_000_000
        print(
            f"  {b:>8}: {t['in']:>6} in + {t['out']:>6} out = ${cost:.4f}"
        )


# ── Main ─────────────────────────────────────────────────────────────────────


def load_tracks_from_csv(
    csv_path: str, limit: int = 20
) -> List[Dict[str, str]]:
    """Load tracks from a CSV file for testing."""
    tracks = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            artist = (row.get("artist") or row.get("artist_suggest") or "").strip()
            title = (row.get("title") or row.get("title_suggest") or "").strip()
            version = (row.get("version_info") or row.get("version_suggest") or "").strip()
            genre = (row.get("genre") or row.get("genre_suggest") or "").strip()

            if not artist or not title:
                continue

            tracks.append({
                "artist": artist,
                "title": title,
                "version": version,
                "expected_genre": genre,
            })

            if len(tracks) >= limit:
                break

    return tracks


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare web search backends for genre classification"
    )
    parser.add_argument(
        "--backends",
        nargs="+",
        default=None,
        help=f"Backends to compare (default: all available). Options: {', '.join(list_backends())}",
    )
    parser.add_argument(
        "--search-only",
        action="store_true",
        help="Only compare search results, skip LLM classification",
    )
    parser.add_argument(
        "--csv",
        help="Load test tracks from CSV instead of built-in dataset",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=12,
        help="Max tracks to test (default: 12)",
    )
    parser.add_argument(
        "--max-queries",
        type=int,
        default=3,
        help="Max search queries per track (default: 3)",
    )
    parser.add_argument(
        "--model",
        default="",
        help="LLM model override (default: config ai_quick_model)",
    )
    parser.add_argument(
        "--json",
        metavar="PATH",
        help="Save raw results to JSON file",
    )
    args = parser.parse_args()

    # Determine backends
    backend_names = args.backends or list_backends()

    # Determine tracks
    if args.csv:
        tracks = load_tracks_from_csv(args.csv, limit=args.limit)
        print(f"Loaded {len(tracks)} tracks from {args.csv}", file=sys.stderr)
    else:
        tracks = TEST_TRACKS[:args.limit]
        print(f"Using {len(tracks)} built-in test tracks", file=sys.stderr)

    if not tracks:
        print("No tracks to test!", file=sys.stderr)
        sys.exit(1)

    # Run search comparison
    print(f"\nComparing backends: {', '.join(backend_names)}", file=sys.stderr)
    print(f"Tracks: {len(tracks)}, max queries/track: {args.max_queries}", file=sys.stderr)

    all_search_results = compare_search_backends(
        tracks, backend_names, max_queries=args.max_queries
    )

    print_search_report(all_search_results)

    # LLM classification comparison
    if not args.search_only:
        from djlib.config import get_openai_api_key
        api_key = get_openai_api_key()
        if not api_key:
            print("\n⚠ No OpenAI API key — skipping LLM classification.", file=sys.stderr)
            print("   Set OPENAI_API_KEY or add openai_api_key to config.local.yml", file=sys.stderr)
        else:
            print(f"\n{'='*60}", file=sys.stderr)
            print("Running LLM classification...", file=sys.stderr)

            all_classifications: Dict[str, List[Dict[str, Any]]] = {}

            for backend_name, search_results in all_search_results.items():
                print(f"\n  Classifying with {backend_name} snippets...", file=sys.stderr)
                classifications = []

                for i, sr in enumerate(search_results):
                    track = sr["track"]
                    label = f"{track['artist']} - {track['title']}"
                    print(f"    [{i+1}/{len(search_results)}] {label}...", end=" ", file=sys.stderr, flush=True)

                    try:
                        result = classify_with_llm(
                            track,
                            sr["prompt_context"],
                            api_key,
                            model=args.model,
                        )
                        result["track"] = track
                        print(f"→ {result.get('genre', '?')}", file=sys.stderr)
                    except Exception as e:
                        result = {"genre": "ERROR", "error": str(e), "track": track}
                        print(f"→ ERROR: {e}", file=sys.stderr)

                    classifications.append(result)
                    time.sleep(0.2)  # rate limit

                all_classifications[backend_name] = classifications

            # Also classify WITHOUT any web search (baseline)
            print(f"\n  Classifying WITHOUT web search (baseline)...", file=sys.stderr)
            baseline = []
            for i, track in enumerate(tracks):
                label = f"{track['artist']} - {track['title']}"
                print(f"    [{i+1}/{len(tracks)}] {label}...", end=" ", file=sys.stderr, flush=True)
                try:
                    result = classify_with_llm(
                        track,
                        "(No web search results — classify based on your knowledge only)",
                        api_key,
                        model=args.model,
                    )
                    result["track"] = track
                    print(f"→ {result.get('genre', '?')}", file=sys.stderr)
                except Exception as e:
                    result = {"genre": "ERROR", "error": str(e), "track": track}
                    print(f"→ ERROR: {e}", file=sys.stderr)
                baseline.append(result)
                time.sleep(0.2)
            all_classifications["no_search"] = baseline

            print_classification_report(all_classifications)

    # Save raw JSON
    if args.json:
        output = {
            "search_results": {
                name: [
                    {k: v for k, v in r.items() if k != "prompt_context"}
                    for r in results
                ]
                for name, results in all_search_results.items()
            },
            "tracks": tracks,
            "backends_tested": list(all_search_results.keys()),
        }
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"\nRaw results saved to {args.json}", file=sys.stderr)


if __name__ == "__main__":
    main()
