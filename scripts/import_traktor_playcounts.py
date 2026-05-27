"""
Import play counts from Traktor collection.nml into library.csv.

Matches by traktor_id (Traktor AUDIO_ID). Only updates play_count.
Uses MAX(library, traktor) — never decreases a count.

Usage:
    python scripts/import_traktor_playcounts.py             # dry-run
    python scripts/import_traktor_playcounts.py --write     # write
"""
from __future__ import annotations

import argparse
import csv
import shutil
from datetime import datetime
from pathlib import Path

NML_PATH = Path.home() / "Documents/Native Instruments/Traktor 3.11.1/collection.nml"
LIBRARY_CSV = Path("data/library.csv")


def build_traktor_playcount_index(nml_path: Path) -> dict[str, int]:
    """Return {audio_id: playcount} for all Traktor entries with playcount > 0."""
    from traktor_nml_utils import TraktorCollection

    col = TraktorCollection(path=nml_path)
    index: dict[str, int] = {}
    for entry in col.nml.collection.entry:
        audio_id = entry.audio_id
        if not audio_id:
            continue
        playcount = 0
        if entry.info and entry.info.playcount:
            playcount = int(entry.info.playcount)
        if playcount > 0:
            index[audio_id] = playcount
    return index


def run(write: bool) -> None:
    print(f"\n{'=' * 60}")
    print("IMPORT TRAKTOR PLAY COUNTS")
    print("DRY-RUN" if not write else "⚠️  WRITE MODE")
    print("=" * 60)

    traktor_index = build_traktor_playcount_index(NML_PATH)
    print(f"\nTraktor entries with play_count > 0: {len(traktor_index)}")

    with open(LIBRARY_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    updates: list[dict] = []

    for row in rows:
        traktor_id = row.get("traktor_id", "").strip()
        if not traktor_id or traktor_id not in traktor_index:
            continue

        traktor_count = traktor_index[traktor_id]
        current_count = int(row.get("play_count", "") or 0)
        new_count = max(current_count, traktor_count)

        if new_count > current_count:
            updates.append({
                "track_id": row.get("track_id", ""),
                "artist": row.get("artist", ""),
                "title": row.get("title", ""),
                "current": current_count,
                "traktor": traktor_count,
                "new": new_count,
            })

    print(f"Library rows: {len(rows)}")
    print(f"Matched by traktor_id: {sum(1 for r in rows if r.get('traktor_id','').strip() in traktor_index)}")
    print(f"Will update play_count: {len(updates)}")
    print()

    if not updates:
        print("Nothing to update.")
        return

    print(f"{'Artist':<35} {'Title':<35} {'Before':>6} {'Traktor':>7} {'After':>6}")
    print("-" * 93)
    for u in sorted(updates, key=lambda x: -x["traktor"]):
        print(f"{u['artist'][:34]:<35} {u['title'][:34]:<35} {u['current']:>6} {u['traktor']:>7} {u['new']:>6}")

    if not write:
        print(f"\n--- DRY-RUN: no changes written. Re-run with --write to apply. ---")
        return

    # Backup before write
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = LIBRARY_CSV.with_name(f"library.bak-traktor-playcounts-{ts}.csv")
    shutil.copy2(LIBRARY_CSV, backup)
    print(f"\nBackup: {backup.name}")

    # Apply updates (only play_count, nothing else)
    update_map = {u["track_id"]: u["new"] for u in updates}
    for row in rows:
        tid = row.get("track_id", "")
        if tid in update_map:
            row["play_count"] = str(update_map[tid])

    # Atomic write via temp file
    tmp = LIBRARY_CSV.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(LIBRARY_CSV)

    print(f"✅ Updated play_count for {len(updates)} tracks in library.csv")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Write changes (default: dry-run)")
    args = parser.parse_args()
    run(write=args.write)


if __name__ == "__main__":
    main()
