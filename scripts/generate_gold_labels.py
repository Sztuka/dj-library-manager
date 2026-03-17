#!/usr/bin/env python3
"""
Generate gold_labels.json from the dev environment after manual genre review.

Workflow:
    1. scripts/populate_unsorted_test.py     -- copy tracks to unsorted-test/
    2. scripts/dev_env.sh on                 -- switch to dev environment
    3. .venv/bin/python -m djlib.cli scan    -- scan tracks into unsorted-test.csv
    4. .venv/bin/python -m djlib.cli enrich-online  -- enrich metadata
    5. .venv/bin/python -m djlib.cli review  -- manually assign genres in Review UI
    6. scripts/dev_env.sh off                -- switch back to production
    7. .venv/bin/python scripts/generate_gold_labels.py  -- THIS SCRIPT

Reads: data/unsorted-test.csv (enriched + manually reviewed by user)
Outputs: data/ab_test/gold_labels.json

The gold_labels.json is then used by `ab_test_genre.py --eval` to provide
validated ground truth instead of the unreliable folder names.

Usage:
    .venv/bin/python scripts/generate_gold_labels.py [--dry-run] [--csv PATH]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_CSV = PROJECT_ROOT / "data" / "unsorted-test.csv"
OUTPUT = PROJECT_ROOT / "data" / "ab_test" / "gold_labels.json"

# Try loading genre family map from genres.yml for family annotation
try:
    from scripts.ab_test_genre import load_genre_family_map
except ImportError:
    load_genre_family_map = None  # type: ignore[assignment]


def load_family_map() -> dict[str, str]:
    """Load genre → family mapping from genres.yml."""
    if load_genre_family_map is not None:
        return load_genre_family_map()
    # Fallback: load directly from genres.yml
    try:
        import yaml
        genres_path = PROJECT_ROOT / "genres.yml"
        with open(genres_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        fmap: dict[str, str] = {}
        for item in data.get("genres", []):
            label = item.get("label", "")
            family = item.get("family", "")
            if label and family:
                fmap[label] = family
        return fmap
    except Exception:
        return {}


def generate_gold_labels(csv_path: Path, dry_run: bool = False) -> None:
    """Read enriched CSV and extract gold labels for tracks with assigned genres."""
    if not csv_path.exists():
        print(f"❌ CSV not found: {csv_path}")
        print("   Run the dev workflow first (see --help for the full sequence).")
        sys.exit(1)

    family_map = load_family_map()

    # Read CSV
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print("❌ CSV is empty.")
        sys.exit(1)

    print(f"Read {len(rows)} tracks from {csv_path.name}\n")

    # Extract gold labels — match by filename
    # Only include tracks that were reviewed AND accepted by the user
    gold: dict[str, dict[str, str]] = {}
    skipped_no_genre = 0
    skipped_unknown = 0
    skipped_not_accepted = 0

    for row in rows:
        file_path = row.get("file_path", "")
        genre = (row.get("genre") or "").strip()

        if not file_path:
            continue

        filename = Path(file_path).name

        # Skip tracks that were not reviewed/accepted
        done = (row.get("done") or "").strip().upper()
        dest = (row.get("dest_decision") or "").strip().lower()
        if done != "TRUE" or dest == "reject":
            skipped_not_accepted += 1
            continue

        # Skip tracks without a genre assignment
        if not genre:
            skipped_no_genre += 1
            continue

        # Skip UNKNOWN/unresolved genres
        if genre.upper() in ("UNKNOWN", "?", ""):
            skipped_unknown += 1
            continue

        entry: dict[str, str] = {"genre": genre}

        # Add family if available
        family = family_map.get(genre, "")
        if family:
            entry["family"] = family

        # Add source metadata for traceability
        artist = (row.get("artist") or row.get("artist_suggest") or "").strip()
        title = (row.get("title") or row.get("title_suggest") or "").strip()
        if artist:
            entry["artist"] = artist
        if title:
            entry["title"] = title

        gold[filename] = entry

    # Summary
    print(f"  Gold labels generated:    {len(gold)}")
    print(f"  Skipped (not accepted):  {skipped_not_accepted}")
    print(f"  Skipped (no genre):      {skipped_no_genre}")
    print(f"  Skipped (UNKNOWN):       {skipped_unknown}")

    if not gold:
        print("\n❌ No gold labels found. Did you assign genres in the Review UI?")
        sys.exit(1)

    # Validate against genres.yml taxonomy
    if family_map:
        valid_genres = set(family_map.keys())
        invalid = [fn for fn, entry in gold.items()
                   if entry["genre"] not in valid_genres]
        if invalid:
            print(f"\n  ⚠️  {len(invalid)} tracks have genres NOT in genres.yml:")
            for fn in invalid[:10]:
                print(f"      {fn} → {gold[fn]['genre']}")
            if len(invalid) > 10:
                print(f"      ... and {len(invalid) - 10} more")

    # Family distribution
    families: dict[str, int] = {}
    for entry in gold.values():
        fam = entry.get("family", "Unknown")
        families[fam] = families.get(fam, 0) + 1
    print(f"\n  Genre family distribution:")
    for fam in sorted(families, key=families.get, reverse=True):  # type: ignore[arg-type]
        print(f"    {fam:15s} {families[fam]:3d} tracks")

    if dry_run:
        print(f"\n  DRY RUN — would write to: {OUTPUT}")
        # Show first 5 entries as preview
        print(f"\n  Preview (first 5):")
        for fn, entry in list(gold.items())[:5]:
            print(f"    {fn}")
            print(f"      genre: {entry['genre']}, family: {entry.get('family', '?')}")
        return

    # Write gold_labels.json
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    # If file exists, merge (don't overwrite — user might do multiple rounds)
    existing: dict[str, dict[str, str]] = {}
    if OUTPUT.exists():
        try:
            with open(OUTPUT, encoding="utf-8") as f:
                existing = json.load(f)
            print(f"\n  Merging with existing gold_labels.json ({len(existing)} entries)")
        except (json.JSONDecodeError, ValueError):
            print(f"\n  ⚠️  Existing gold_labels.json is corrupt, overwriting")

    # New labels take priority
    merged = {**existing, **gold}
    updated = len(merged) - len(existing)

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2, sort_keys=True)

    print(f"\n  ✅ Written {len(merged)} gold labels to {OUTPUT}")
    if existing:
        print(f"     ({updated} new/updated, {len(existing)} previously existing)")
    print(f"\n  Next: .venv/bin/python scripts/ab_test_genre.py --eval")
    print(f"  Gold labels will be used automatically as ground truth.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate gold_labels.json from dev environment enriched CSV",
        epilog=(
            "Full workflow:\n"
            "  1. scripts/populate_unsorted_test.py\n"
            "  2. scripts/dev_env.sh on\n"
            "  3. .venv/bin/python -m djlib.cli scan\n"
            "  4. .venv/bin/python -m djlib.cli enrich-online\n"
            "  5. .venv/bin/python -m djlib.cli review  (assign genres manually)\n"
            "  6. scripts/dev_env.sh off\n"
            "  7. .venv/bin/python scripts/generate_gold_labels.py\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be generated without writing")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV,
                        help=f"Path to enriched CSV (default: {DEFAULT_CSV.name})")
    args = parser.parse_args()

    generate_gold_labels(args.csv, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
