#!/usr/bin/env python3
"""Batch AI classification for unsorted tracks.

Reads unsorted.csv, sends each track to OpenAI for unified classification
(artist, title, version[], genre), and writes AI results back to CSV as
ai_* columns for review in the UI.

Usage:
    .venv/bin/python scripts/batch_classify.py [--dry-run] [--limit N] [--model MODEL]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
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
from djlib.config import UNSORTED_CSV, get_openai_api_key
from djlib.unsorted import load_unsorted_rows, write_unsorted_rows


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
    parser = argparse.ArgumentParser(description="Batch AI classify unsorted tracks")
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
        help="Override AI model (e.g. gpt-4o-mini, gpt-4.1-mini)",
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
    csv_path = Path(UNSORTED_CSV)
    rows = load_unsorted_rows(csv_path)
    if not rows:
        print("No unsorted tracks found.", file=sys.stderr)
        return

    print(f"Loaded {len(rows)} unsorted tracks.", file=sys.stderr)

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

    # Collect results into a lookup
    result_map = {}  # track_id -> result
    errors = 0
    for row, result in results:
        tid = row.get("track_id", "")
        if result.get("error"):
            errors += 1
            continue
        result_map[tid] = result
        usage = result.get("_usage", {})
        total_tokens_in += usage.get("input_tokens", 0)
        total_tokens_out += usage.get("output_tokens", 0)

    # Print summary
    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"Results: {len(result_map)} classified, {errors} errors", file=sys.stderr)
    print(f"Tokens: {total_tokens_in:,} in + {total_tokens_out:,} out", file=sys.stderr)

    # Estimate cost (gpt-4o-mini: $0.15/$0.60 per 1M tokens)
    cost_in = total_tokens_in * 0.15 / 1_000_000
    cost_out = total_tokens_out * 0.60 / 1_000_000
    print(f"Est. cost: ${cost_in + cost_out:.4f}", file=sys.stderr)

    if args.dry_run:
        print("\n[DRY RUN] Results not written to CSV.", file=sys.stderr)
        # Print JSON summary
        for tid, result in result_map.items():
            safe = {k: v for k, v in result.items() if not k.startswith("_")}
            print(json.dumps(safe, ensure_ascii=False))
        return

    # Write results back to CSV
    updated = 0
    for row in rows:
        tid = row.get("track_id", "")
        if tid not in result_map:
            continue
        r = result_map[tid]
        row["ai_artist"] = r.get("artist", "")
        row["ai_title"] = r.get("title", "")
        version_tokens = r.get("version", [])
        if isinstance(version_tokens, list):
            row["ai_version"] = format_version_for_csv(version_tokens)
        else:
            row["ai_version"] = str(version_tokens)
        row["ai_genre"] = r.get("genre", "")
        row["ai_confidence"] = str(r.get("confidence", ""))
        row["ai_reasoning"] = r.get("reasoning", "")
        row["ai_classify_date"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        updated += 1

    write_unsorted_rows(csv_path, rows)
    print(f"\nWrote {updated} AI results to {csv_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
