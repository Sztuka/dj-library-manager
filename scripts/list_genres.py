#!/usr/bin/env python3
"""Print genre frequency counts from a CSV exported by extract_tags_to_csv.py."""

import argparse
import csv
from collections import Counter
from pathlib import Path
from typing import Optional


DEFAULT_CSV = Path("data/OLD_LIBRARY_SOURCE.csv")
DEFAULT_OUTPUT = Path("data/OLD_LIBRARY_GENRES_LIST.csv")
GENRE_COLUMN = "genre"


def load_counts(csv_path: Path, split_delimiters: Optional[str], include_empty: bool) -> Counter[str]:
    counts: Counter[str] = Counter()
    with csv_path.open(newline="", encoding="utf-8", errors="ignore") as f:
        # Strip any stray NUL bytes that break csv module parsing
        reader = csv.DictReader((line.replace("\x00", "") for line in f))
        for row in reader:
            raw = (row.get(GENRE_COLUMN) or "").strip()
            if not raw and not include_empty:
                continue
            if split_delimiters:
                parts = [raw]
                for delim in split_delimiters:
                    parts = sum([p.split(delim) for p in parts], [])
                for part in parts:
                    cleaned = part.strip()
                    if cleaned or include_empty:
                        counts[cleaned or "<empty>"] += 1
            else:
                counts[raw or "<empty>"] += 1
    return counts


def parse_args():
    parser = argparse.ArgumentParser(description="List unique genres with counts.")
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
        help="Path to CSV file (default: data/OLD_LIBRARY_SOURCE.csv)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Where to write the genres CSV (default: data/OLD_LIBRARY_GENRES_LIST.csv)",
    )
    parser.add_argument(
        "--split",
        dest="split_delimiters",
        default=None,
        help="Optional delimiters (e.g. ',;') to split multi-genre fields.",
    )
    parser.add_argument(
        "--include-empty",
        action="store_true",
        help="Include empty genre entries in the output.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.csv.exists():
        raise SystemExit(f"CSV not found: {args.csv}")

    counts = load_counts(args.csv, args.split_delimiters, args.include_empty)
    for genre, count in counts.most_common():
        print(f"{genre}\t{count}")

    # Export to CSV
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["genre", "count"])
        for genre, count in counts.most_common():
            writer.writerow([genre, count])


if __name__ == "__main__":
    main()
