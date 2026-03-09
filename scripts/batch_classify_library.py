#!/usr/bin/env python3
"""Batch AI classification for library review tracks.

Reads data/library_review.csv, sends each track to OpenAI for unified
classification (artist, title, version[], genre), and writes AI results
back to CSV as ai_* columns for review in the UI.

Usage:
    .venv/bin/python scripts/batch_classify_library.py [--dry-run] [--limit N] [--model MODEL]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from djlib.ai_classify import (
    batch_classify,
    classify_track,
    format_version_for_csv,
    load_genre_labels,
)
from djlib.config import get_openai_api_key
from djlib.unsorted import load_unsorted_rows, write_unsorted_rows

LIBRARY_REVIEW_CSV = _REPO / "data" / "library_review.csv"


def _progress(i: int, total: int, row: dict, result: dict) -> None:
    """Print progress line to stderr."""
    artist = result.get("artist", "?")
    title = result.get("title", "?")
    genre = result.get("genre", "?")
    conf = result.get("confidence", 0)
    err = result.get("error", "")

    if err:
        status = f"  ERROR: {err}"
    else:
        status = f"  {artist} — {title} [{genre}] ({conf:.0%})"

    print(f"[{i + 1}/{total}]{status}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch AI classify library review tracks")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print results but don't write to CSV",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process only first N unclassified tracks (0 = all)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override AI model (default: gpt-5-nano from config)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-classify even if ai_genre already set",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.15,
        help="Seconds between API calls (rate limiting)",
    )
    args = parser.parse_args()

    api_key = get_openai_api_key()
    if not api_key:
        print("ERROR: No OpenAI API key. Set openai_api_key in config.local.yml", file=sys.stderr)
        sys.exit(1)

    # Load data
    csv_path = Path(LIBRARY_REVIEW_CSV)
    rows = load_unsorted_rows(csv_path)
    if not rows:
        print("No library review tracks found. Run scan_library_for_review.py first.", file=sys.stderr)
        return

    print(f"Loaded {len(rows)} library review tracks.", file=sys.stderr)

    # Filter to unclassified (unless --force)
    if args.force:
        to_classify = rows
    else:
        to_classify = [r for r in rows if not (r.get("ai_genre") or "").strip()]

    if not to_classify:
        print("All tracks already classified. Use --force to re-classify.", file=sys.stderr)
        return

    if args.limit > 0:
        to_classify = to_classify[: args.limit]

    print(f"Classifying {len(to_classify)} tracks...", file=sys.stderr)
    genre_labels = load_genre_labels()
    print(f"Genre labels: {len(genre_labels)} genres", file=sys.stderr)

    # Run batch
    total_tokens_in = 0
    total_tokens_out = 0
    results = batch_classify(
        to_classify,
        api_key=api_key,
        model=args.model,
        on_progress=_progress,
        delay=args.delay,
    )

    # Write results back to rows
    # batch_classify returns List[Tuple[row, result]]
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    classified = 0
    errors = 0

    for row, result in results:
        if result.get("error"):
            errors += 1
            continue

        row["ai_artist"] = result.get("artist", "")
        row["ai_title"] = result.get("title", "")
        row["ai_version"] = format_version_for_csv(result.get("version", []))
        row["ai_genre"] = result.get("genre", "")
        row["ai_confidence"] = f"{result.get('confidence', 0):.2f}"
        row["ai_reasoning"] = result.get("reasoning", "")
        row["ai_classify_date"] = now_str
        classified += 1

        # Track token usage
        usage = result.get("_usage", {})
        total_tokens_in += usage.get("input_tokens", 0)
        total_tokens_out += usage.get("output_tokens", 0)

    # Summary
    print(f"\n{'='*50}", file=sys.stderr)
    print(f"Classified: {classified}/{len(to_classify)}", file=sys.stderr)
    print(f"Errors:     {errors}", file=sys.stderr)
    print(f"Tokens:     {total_tokens_in:,} in + {total_tokens_out:,} out", file=sys.stderr)

    # Estimate cost (gpt-5-nano: $0.10/1M in + $0.40/1M out)
    cost_in = total_tokens_in * 0.10 / 1_000_000
    cost_out = total_tokens_out * 0.40 / 1_000_000
    print(f"Est. cost:  ${cost_in + cost_out:.4f}", file=sys.stderr)
    print(f"{'='*50}", file=sys.stderr)

    if not args.dry_run:
        write_unsorted_rows(csv_path, rows, [])
        print(f"\n✅ Written to {csv_path}", file=sys.stderr)
    else:
        print("\n🔍 DRY RUN — no changes written.", file=sys.stderr)
        # Print sample results
        for row, result in results[:5]:
            if not result.get("error"):
                ver = result.get("version", [])
                ver_str = " ".join(f"({v})" for v in ver) if ver else ""
                print(
                    f"  {result['artist']} — {result['title']} {ver_str} [{result['genre']}] ({result['confidence']:.0%})",
                    file=sys.stderr,
                )


if __name__ == "__main__":
    main()
