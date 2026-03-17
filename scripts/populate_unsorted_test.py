#!/usr/bin/env python3
"""
Copy tracks from data/ab_test/<Genre>/ → data/ab_test/unsorted-test/ (flat).

This populates the dev/test unsorted inbox with the same 121 tracks
from the A/B test, but in a flat structure (no genre folders)
— exactly how a real unsorted folder looks.

Usage:
    .venv/bin/python scripts/populate_unsorted_test.py [--dry-run]
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AB_DIR = REPO / "data" / "ab_test"
DEST = AB_DIR / "unsorted-test"
AUDIO_EXTS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".ogg"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Populate unsorted-test from ab_test tracks")
    parser.add_argument("--dry-run", action="store_true", help="Just show what would be copied")
    args = parser.parse_args()

    DEST.mkdir(parents=True, exist_ok=True)

    # Collect all audio files from genre folders
    tracks: list[tuple[Path, str]] = []
    for genre_dir in sorted(AB_DIR.iterdir()):
        if not genre_dir.is_dir() or genre_dir.name == "unsorted-test":
            continue
        for f in sorted(genre_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in AUDIO_EXTS:
                tracks.append((f, genre_dir.name))

    print(f"Found {len(tracks)} tracks in {len(set(g for _, g in tracks))} genre folders\n")

    copied = 0
    skipped = 0
    for src, genre in tracks:
        dest_file = DEST / src.name
        if dest_file.exists():
            print(f"  SKIP (exists) {src.name}")
            skipped += 1
            continue

        if args.dry_run:
            print(f"  COPY {genre:20s} → {src.name}")
        else:
            shutil.copy2(src, dest_file)
            print(f"  ✅ {src.name}")
        copied += 1

    print(f"\n{'Would copy' if args.dry_run else 'Copied'}: {copied}, Skipped: {skipped}")
    if not args.dry_run and copied:
        print(f"\nTracks are in: {DEST}")
        print("Now run:  scripts/dev_env.sh on")
        print("Then:     .venv/bin/python -m djlib.cli scan")


if __name__ == "__main__":
    main()
