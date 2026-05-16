"""One-time repair: set old_full_path for library.csv rows whose WAV/FLAC
is already in Music Library but old_full_path is empty.

These rows were created by cmd_apply before the old_full_path bug was fixed.
Their original_path points to a now-gone unsorted location; the actual file
lives somewhere in MUSIC_DIR with the same stem.

Usage:
    python scripts/repair_old_full_path.py [--dry-run] [--music-dir PATH]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# allow running from project root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

import djlib.config as _cfg_mod
from djlib.config import load_config
from djlib.library_schema import load_library_csv, save_library_csv
from djlib.locks import csv_lock

_CONVERT_EXTS = {".wav", ".flac"}


import re as _re

_KEY_BPM_SUFFIX = _re.compile(r"\s*\[\s*\d+[AB]\s+\d+\s*\]\s*$", _re.IGNORECASE)


def _normalize(s: str) -> str:
    """Lowercase, strip [key BPM] suffix, collapse punctuation to spaces."""
    s = _KEY_BPM_SUFFIX.sub("", s).lower().strip()
    s = _re.sub(r"[^\w\s]", " ", s)
    return _re.sub(r"\s+", " ", s).strip()


def find_in_tree(stem: str, music_dir: Path) -> list[Path]:
    """Return WAV/FLAC files in music_dir whose stem matches.

    Tries exact match first, then case-insensitive + strip [key BPM] suffix,
    then checks if the original stem is a substring of the file stem (handles
    cases where cmd_apply prepended the artist name to the file).
    """
    norm_stem = _normalize(stem)
    exact: list[Path] = []
    loose: list[Path] = []
    for p in music_dir.rglob("*"):
        if p.suffix.lower() not in _CONVERT_EXTS:
            continue
        if p.stem == stem:
            exact.append(p)
        elif _normalize(p.stem) == norm_stem:
            loose.append(p)
        elif norm_stem and norm_stem in _normalize(p.stem):
            loose.append(p)
    return exact if exact else loose


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--music-dir", help="Override path to Music Library dir")
    args = parser.parse_args()

    cfg = load_config()
    csv_path = _cfg_mod.CSV_PATH
    music_dir = Path(args.music_dir) if args.music_dir else Path(cfg.get("LIB_ROOT", "~/Music Library")).expanduser()

    if not music_dir.exists():
        print(f"ERROR: music_dir not found: {music_dir}")
        sys.exit(1)

    print(f"Library CSV : {csv_path}")
    print(f"Music dir   : {music_dir}")
    print(f"Dry run     : {args.dry_run}\n")

    with csv_lock(csv_path):
        rows = load_library_csv(csv_path)

    candidates = [
        r for r in rows
        if not str(r.get("old_full_path", "") or "").strip()
        and Path(str(r.get("original_path", "") or "")).suffix.lower() in _CONVERT_EXTS
    ]

    print(f"Rows with empty old_full_path and WAV/FLAC original_path: {len(candidates)}")

    patched = 0
    ambiguous = 0
    not_found = 0

    for r in candidates:
        orig = Path(str(r.get("original_path", "")).strip())
        stem = orig.stem
        matches = find_in_tree(stem, music_dir)

        if len(matches) == 1:
            new_path = str(matches[0])
            print(f"  PATCH  {stem!r:50s} → {matches[0].name}")
            if not args.dry_run:
                r["old_full_path"] = new_path
            patched += 1
        elif len(matches) > 1:
            print(f"  AMBIG  {stem!r} — {len(matches)} matches: {[str(m) for m in matches]}")
            ambiguous += 1
        else:
            print(f"  MISS   {stem!r} — not found in music_dir")
            not_found += 1

    print(f"\nSummary: {patched} patched, {ambiguous} ambiguous, {not_found} not found")

    if not args.dry_run and patched > 0:
        with csv_lock(csv_path):
            save_library_csv(csv_path, rows)
        print("library.csv updated.")
    elif args.dry_run:
        print("(dry run — no changes written)")


if __name__ == "__main__":
    main()
