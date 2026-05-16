"""Export DJPlayCount from Rekordbox for all missing local files.

Run this BEFORE deleting missing links from Rekordbox. Saves a
playcounts.json ledger to LOGS/ that dj apply will pick up automatically.

Usage:
    python scripts/export_rb_playcounts.py [--all]

By default only exports tracks whose local file is missing (gone from disk).
--all exports play counts for every track in Rekordbox.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import djlib.config as cfg_mod
from djlib.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Export all tracks, not just missing")
    args = parser.parse_args()

    cfg = load_config()
    logs_dir = Path(cfg.get("LOGS_DIR", "LOGS"))
    logs_dir.mkdir(parents=True, exist_ok=True)

    try:
        from pyrekordbox.db6 import Rekordbox6Database
    except ImportError:
        print("ERROR: pyrekordbox not installed.")
        sys.exit(1)

    print("Opening Rekordbox database…")
    db = Rekordbox6Database()

    ledger: dict[str, int] = {}
    total = 0
    exported = 0
    skipped_zero = 0

    for track in db.get_content():
        total += 1
        fp = track.FolderPath or ""
        if fp.startswith("apple-music:"):
            continue
        p = Path(fp)
        if not args.all and p.exists():
            continue  # file still on disk — skip unless --all
        count = int(track.DJPlayCount or 0)
        stem = p.stem
        if not stem:
            continue
        if count == 0:
            skipped_zero += 1
        # Always write even if 0, so we have a complete record
        ledger[stem] = ledger.get(stem, 0) + count
        exported += 1

    out_path = logs_dir / "historic_playcounts_from_rb.playcounts.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2, ensure_ascii=False, sort_keys=True)

    nonzero = sum(1 for v in ledger.values() if v > 0)
    print(f"Rekordbox tracks scanned : {total}")
    print(f"Entries written          : {exported}  ({nonzero} with play_count > 0)")
    print(f"Ledger saved to          : {out_path}")
    print()
    print("dj apply will pick this up automatically for future scan→review→apply cycles.")


if __name__ == "__main__":
    main()
