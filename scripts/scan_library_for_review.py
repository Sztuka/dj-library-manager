#!/usr/bin/env python3
"""Scan Music Library folder and generate library_review.csv for AI re-processing.

This is temporary tooling for re-reviewing existing library tracks.
It scans ~/Music Library/ (artist subfolders), reads DJLIB custom tags
(track_id, rekordbox_id, traktor_id) and standard audio tags, then
writes data/library_review.csv in the same format as unsorted.csv.

Usage:
    .venv/bin/python scripts/scan_library_for_review.py [--limit N]
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from djlib.config import load_config
from djlib.djlib_tags import read_djlib_tags, generate_track_id
from djlib.tags import read_tags
from djlib.enrich import derive_local_metadata
from djlib.unsorted import UNSORTED_FIELDNAMES, normalize_unsorted_row

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aiff", ".aif", ".m4a"}

# Subfolders to skip (not artist folders)
SKIP_DIRS = {"LIBRARY", "MIXES", ".Spotlight-V100", ".Trashes"}


def scan_music_library(lib_root: Path, limit: int = 0) -> list[dict[str, str]]:
    """Walk Music Library, read tags from each audio file, return rows."""
    files: list[Path] = []
    for root, dirs, fnames in os.walk(lib_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in sorted(fnames):
            p = Path(root) / fname
            if p.suffix.lower() in AUDIO_EXTS and not fname.startswith("."):
                files.append(p)

    total = len(files)
    if limit:
        files = files[:limit]
    print(f"📂 Found {total} audio files in {lib_root}")
    if limit:
        print(f"   (processing first {limit})")

    rows: list[dict[str, str]] = []
    errors = 0

    for i, p in enumerate(files, 1):
        try:
            # Read DJLIB custom tags (track_id, rekordbox_id, traktor_id)
            djlib = read_djlib_tags(p)
            track_id = djlib.get("track_id", "")
            rekordbox_id = djlib.get("rekordbox_id", "")
            traktor_id = djlib.get("traktor_id", "")

            # Read standard audio tags
            tags = read_tags(p)
            tags_original = dict(tags)

            # Derive clean artist/title/version from tags + filename
            artist, title, version = derive_local_metadata(p, tags)

            # Generate track_id if not in DJLIB tags
            if not track_id:
                track_id = generate_track_id(p, artist, title)

            # Build row matching unsorted.csv format
            rec: dict[str, str] = {
                "track_id": track_id,
                "file_path": str(p),
                "file_hash": "",
                "fingerprint": "",
                "added_date": "",
                "is_duplicate": "false",
                "tag_artist_original": (tags_original.get("artist") or "").strip(),
                "tag_title_original": (tags_original.get("title") or "").strip(),
                "tag_genre_original": (tags_original.get("genre") or "").strip(),
                "tag_bpm_original": (tags_original.get("bpm") or "").strip(),
                "tag_key_original": (tags_original.get("key_camelot") or "").strip(),
                "artist": artist.strip(),
                "title": title.strip(),
                "version_info": (version or "").strip(),
                "genre": (tags.get("genre") or "").strip(),
                "bpm": (tags.get("bpm") or "").strip(),
                "key_camelot": (tags.get("key_camelot") or "").strip(),
                "energy_hint": (tags.get("energy_hint") or "").strip(),
                "year": (tags.get("year") or "").strip(),
                # Preserve DJ software IDs
                "rekordbox_id": rekordbox_id,
                "traktor_id": traktor_id,
                # Algorithmic suggestions (will be filled by enrich/AI later)
                "artist_suggest": "",
                "title_suggest": "",
                "title_normalized": "",
                "version_suggest": "",
                "genre_suggest": "",
                "album_suggest": (tags.get("album") or "").strip(),
                "release_group_id": "",
                "year_suggest": (tags.get("year") or "").strip(),
                "duration_suggest": "",
                "genres_musicbrainz": "",
                "genres_lastfm": "",
                "genres_soundcloud": "",
                "genres_beatport": "",
                "pop_playcount": "",
                "pop_listeners": "",
                "meta_source": "",
                # Editable fields
                "status": "",
                "destination": "library",  # Already in library
                "target_subfolder": "",
                "must_play": "",
                "occasion_tags": "",
                "notes": "",
                "rating": "",
                "final_filename": "",
                # AI columns (will be filled by batch_classify)
                "ai_artist": "",
                "ai_title": "",
                "ai_version": "",
                "ai_genre": "",
                "ai_confidence": "",
                "ai_reasoning": "",
                "ai_classify_date": "",
                "done": "FALSE",
            }

            # Normalize to ensure all columns are present
            rec = normalize_unsorted_row(rec)
            rows.append(rec)

            if i % 25 == 0 or i == len(files):
                print(f"   [{i}/{len(files)}] {p.name}")

        except Exception as e:
            print(f"   ⚠️  Error reading {p.name}: {e}")
            errors += 1

    print(f"\n✅ Scanned {len(rows)} tracks ({errors} errors)")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan Music Library for AI re-review"
    )
    parser.add_argument("--limit", type=int, default=0, help="Process only first N files")
    args = parser.parse_args()

    cfg = load_config()
    lib_root = Path(cfg["LIB_ROOT"])
    print(f"🎵 Scanning: {lib_root}")

    if not lib_root.exists():
        print(f"❌ Music Library not found: {lib_root}")
        sys.exit(1)

    rows = scan_music_library(lib_root, limit=args.limit)

    if not rows:
        print("No tracks found.")
        sys.exit(0)

    # Write to data/library_review.csv
    out_path = Path(__file__).resolve().parent.parent / "data" / "library_review.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=UNSORTED_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n📄 Written to {out_path}")
    print(f"   {len(rows)} tracks ready for AI classify")

    # Quick stats
    no_artist = sum(1 for r in rows if not r.get("artist"))
    no_genre = sum(1 for r in rows if not r.get("genre"))
    has_rb = sum(1 for r in rows if r.get("rekordbox_id"))
    has_tr = sum(1 for r in rows if r.get("traktor_id"))
    has_tid = sum(1 for r in rows if r.get("track_id"))
    print(f"\n📊 Stats:")
    print(f"   track_id:     {has_tid}/{len(rows)}")
    print(f"   rekordbox_id: {has_rb}/{len(rows)}")
    print(f"   traktor_id:   {has_tr}/{len(rows)}")
    print(f"   no artist:    {no_artist}/{len(rows)}")
    print(f"   no genre:     {no_genre}/{len(rows)}")


if __name__ == "__main__":
    main()
