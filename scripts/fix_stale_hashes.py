"""Recover from pre-fix stale file_hash in library.csv.

Before the apply→DJLIB-tag→hash-recompute fix, apply stored the PRE-tag hash in
library.csv. Files on disk now have post-tag content (DJLIB tags written), so
unapply's hash check always fails with hash_mismatch.

This script:
  1. Takes a list of track_ids (from unapply WAL hash_mismatch events, or all rows).
  2. Recomputes sha256 for each file at its current path.
  3. Updates library.csv:file_hash IF the row's current hash matches the
     `actual` value from the WAL (defensive: don't blindly trust).
  4. Backs up library.csv first.

Usage:
    # Fix only the 7 stuck tracks from the latest WAL
    python scripts/fix_stale_hashes.py --from-wal LOGS/unapply-20260526-162349.wal.jsonl

    # Dry-run (default)
    python scripts/fix_stale_hashes.py --from-wal <path>

    # Apply
    python scripts/fix_stale_hashes.py --from-wal <path> --write
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
from datetime import datetime
from pathlib import Path

from djlib.fingerprint import file_sha256

LIBRARY_CSV = Path("data/library.csv")


def parse_wal(wal_path: Path) -> dict[str, dict[str, str]]:
    """Return {track_id: {expected, actual}} from UNAPPLY_FAILED hash_mismatch events."""
    out: dict[str, dict[str, str]] = {}
    with wal_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            if ev.get("event") != "UNAPPLY_FAILED":
                continue
            if ev.get("reason") != "hash_mismatch":
                continue
            tid = ev.get("track_id", "")
            if not tid:
                continue
            out[tid] = {
                "expected": ev.get("expected", ""),
                "actual": ev.get("actual", ""),
            }
    return out


def run(wal_path: Path, write: bool) -> None:
    wal_mismatches = parse_wal(wal_path)
    print(f"WAL hash_mismatch events: {len(wal_mismatches)}")
    print()

    with LIBRARY_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    plan: list[dict] = []
    for row in rows:
        tid = row.get("track_id", "")
        if tid not in wal_mismatches:
            continue

        wal_entry = wal_mismatches[tid]
        csv_hash = row.get("file_hash", "")
        file_path = row.get("file_path", "")

        if not file_path:
            print(f"  [SKIP] {tid[:8]}… — no file_path in library.csv")
            continue

        path = Path(file_path)
        if not path.exists():
            print(f"  [SKIP] {tid[:8]}… — file not found: {file_path}")
            continue

        # Sanity check: stored CSV hash must match WAL's `expected`
        if csv_hash != wal_entry["expected"]:
            print(f"  [SKIP] {tid[:8]}… — library.csv hash differs from WAL expected "
                  f"({csv_hash[:8]} vs {wal_entry['expected'][:8]})")
            continue

        # Recompute current
        current_hash = file_sha256(path)
        if current_hash != wal_entry["actual"]:
            print(f"  [SKIP] {tid[:8]}… — current hash differs from WAL actual "
                  f"({current_hash[:8]} vs {wal_entry['actual'][:8]}). File changed since WAL.")
            continue

        plan.append({
            "row": row,
            "track_id": tid,
            "artist": row.get("artist", ""),
            "title": row.get("title", ""),
            "old_hash": csv_hash,
            "new_hash": current_hash,
        })

    print(f"Will update {len(plan)} rows in library.csv:")
    for p in plan:
        print(f"  {p['track_id'][:8]}  {p['artist']} - {p['title']}")
        print(f"      {p['old_hash'][:16]}… → {p['new_hash'][:16]}…")
    print()

    if not plan:
        print("Nothing to do.")
        return

    if not write:
        print("--- DRY-RUN: no changes written. Re-run with --write to apply. ---")
        return

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = LIBRARY_CSV.with_name(f"library.bak-fix-stale-hashes-{ts}.csv")
    shutil.copy2(LIBRARY_CSV, backup)
    print(f"Backup: {backup.name}")

    for p in plan:
        p["row"]["file_hash"] = p["new_hash"]

    tmp = LIBRARY_CSV.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(LIBRARY_CSV)

    print(f"✅ Updated file_hash for {len(plan)} rows.")
    print("Now retry: in Review UI, click Unapply Last Run.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-wal", type=Path, required=True,
                        help="Path to unapply-*.wal.jsonl with hash_mismatch events")
    parser.add_argument("--write", action="store_true", help="Write changes (default: dry-run)")
    args = parser.parse_args()
    run(wal_path=args.from_wal, write=args.write)


if __name__ == "__main__":
    main()
