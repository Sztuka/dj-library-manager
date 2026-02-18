#!/usr/bin/env python3
"""
Cleanup script: find and handle (2), (3), ... duplicate files in the library.

These are files that were silently renamed by the old cmd_apply logic
when a file with the same name already existed at the destination.

Usage:
    python scripts/cleanup_library_dupes.py              # dry-run (report only)
    python scripts/cleanup_library_dupes.py --fix        # move dupes to reject folder
    python scripts/cleanup_library_dupes.py --fix --keep-better  # keep higher quality version
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from djlib.config import load_config
from djlib.dedup import get_audio_info, format_quality, format_duration, normalize_for_match
from djlib.fingerprint import file_sha256

AUDIO_EXTS = {'.mp3', '.flac', '.wav', '.aiff', '.aif', '.m4a', '.ogg', '.opus'}
# Matches filenames ending with " (2)", " (3)", etc. before extension
DUPE_PATTERN = re.compile(r'^(.+?)\s+\((\d+)\)(\.\w+)$')


def find_numbered_dupes(library_path: Path) -> list[tuple[Path, Path, int]]:
    """Find files with (2), (3), ... and their potential originals.
    
    Returns:
        List of (dupe_path, original_path, number) tuples.
        original_path may not exist.
    """
    results = []
    for path in library_path.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTS:
            continue
        m = DUPE_PATTERN.match(path.name)
        if m:
            base_name = m.group(1)
            number = int(m.group(2))
            ext = m.group(3)
            original = path.parent / f"{base_name}{ext}"
            results.append((path, original, number))
    return sorted(results, key=lambda x: str(x[0]))


def analyze_pair(dupe: Path, original: Path) -> dict:
    """Analyze a dupe+original pair."""
    result = {
        "dupe": dupe,
        "original": original,
        "original_exists": original.exists(),
        "identical": False,
        "dupe_info": None,
        "original_info": None,
        "recommendation": "unknown",
    }
    
    if not original.exists():
        result["recommendation"] = "orphan_dupe"  # original gone, dupe is the only copy
        result["dupe_info"] = get_audio_info(dupe)
        return result
    
    # Compare hashes
    dupe_hash = file_sha256(dupe)
    orig_hash = file_sha256(original)
    result["identical"] = (dupe_hash == orig_hash)
    
    if result["identical"]:
        result["recommendation"] = "remove_dupe"  # exact same file — safe to remove dupe
        return result
    
    # Different content — compare quality
    dupe_info = get_audio_info(dupe)
    orig_info = get_audio_info(original)
    result["dupe_info"] = dupe_info
    result["original_info"] = orig_info
    
    if dupe_info and orig_info:
        if dupe_info.quality_score > orig_info.quality_score:
            result["recommendation"] = "dupe_is_better"
        elif dupe_info.quality_score < orig_info.quality_score:
            result["recommendation"] = "original_is_better"
        else:
            result["recommendation"] = "same_quality_different_content"
    else:
        result["recommendation"] = "cannot_compare"
    
    return result


def main():
    parser = argparse.ArgumentParser(description="Find and clean up (2) duplicate files in library")
    parser.add_argument("--fix", action="store_true", help="Actually move duplicates to reject folder")
    parser.add_argument("--keep-better", action="store_true",
                        help="When fixing, keep higher quality version (rename to original name)")
    parser.add_argument("--path", type=str, default=None,
                        help="Override library path (default: from config)")
    args = parser.parse_args()
    
    cfg = load_config()
    library_path = Path(args.path) if args.path else Path(cfg.get("LIB_ROOT", "")).expanduser()
    reject_path = Path(cfg.get("REJECT_ROOT", str(library_path.parent / "Music Rejected"))).expanduser()
    
    if not library_path.exists():
        print(f"❌ Library path does not exist: {library_path}")
        sys.exit(1)
    
    print(f"🔍 Scanning: {library_path}")
    print(f"   Reject:  {reject_path}")
    print()
    
    dupes = find_numbered_dupes(library_path)
    if not dupes:
        print("✅ No (2)/(3)/... files found. Library is clean!")
        return
    
    print(f"Found {len(dupes)} numbered duplicate files:\n")
    
    # Categorize
    categories: dict[str, list] = {
        "remove_dupe": [],
        "original_is_better": [],
        "dupe_is_better": [],
        "same_quality_different_content": [],
        "orphan_dupe": [],
        "cannot_compare": [],
        "unknown": [],
    }
    
    for dupe, original, number in dupes:
        analysis = analyze_pair(dupe, original)
        rec = analysis["recommendation"]
        categories[rec].append(analysis)
        
        # Print report
        rel_dupe = dupe.relative_to(library_path)
        icon = {
            "remove_dupe": "🗑️ ",
            "original_is_better": "📉",
            "dupe_is_better": "📈",
            "same_quality_different_content": "⚖️ ",
            "orphan_dupe": "👻",
            "cannot_compare": "❓",
        }.get(rec, "  ")
        
        print(f"  {icon} {rel_dupe}")
        
        if analysis["identical"]:
            print(f"      → IDENTICAL to original (safe to remove)")
        elif analysis["original_exists"]:
            oi = analysis["original_info"]
            di = analysis["dupe_info"]
            if oi and di:
                print(f"      → Original: {format_quality(oi)}, {format_duration(oi.duration)}")
                print(f"      → Dupe:     {format_quality(di)}, {format_duration(di.duration)}")
                print(f"      → Recommendation: {rec}")
        else:
            print(f"      → Original MISSING — this is the only copy")
        print()
    
    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for cat, items in categories.items():
        if items:
            print(f"  {cat}: {len(items)}")
    print()
    
    if not args.fix:
        print("💡 Run with --fix to move duplicates to reject folder")
        print("💡 Run with --fix --keep-better to auto-swap when dupe is better quality")
        return
    
    # --- FIX MODE ---
    reject_path.mkdir(parents=True, exist_ok=True)
    moved = 0
    swapped = 0
    renamed = 0
    skipped = 0
    
    # Safe removals: identical files
    for analysis in categories["remove_dupe"]:
        dupe = analysis["dupe"]
        dest = reject_path / dupe.name
        if dest.exists():
            stem, ext = dest.stem, dest.suffix
            i = 2
            while (reject_path / f"{stem} ({i}){ext}").exists():
                i += 1
            dest = reject_path / f"{stem} ({i}){ext}"
        shutil.move(str(dupe), str(dest))
        print(f"  🗑️  Moved identical dupe: {dupe.name} → reject/")
        moved += 1
    
    # Original is better: remove dupe
    for analysis in categories["original_is_better"]:
        dupe = analysis["dupe"]
        dest = reject_path / dupe.name
        if dest.exists():
            stem, ext = dest.stem, dest.suffix
            i = 2
            while (reject_path / f"{stem} ({i}){ext}").exists():
                i += 1
            dest = reject_path / f"{stem} ({i}){ext}"
        shutil.move(str(dupe), str(dest))
        print(f"  🗑️  Moved lower-quality dupe: {dupe.name} → reject/")
        moved += 1
    
    # Dupe is better: swap if --keep-better
    for analysis in categories["dupe_is_better"]:
        dupe = analysis["dupe"]
        original = analysis["original"]
        if args.keep_better and original.exists():
            # Move original to reject, rename dupe to original name
            dest = reject_path / original.name
            if dest.exists():
                stem, ext = dest.stem, dest.suffix
                i = 2
                while (reject_path / f"{stem} ({i}){ext}").exists():
                    i += 1
                dest = reject_path / f"{stem} ({i}){ext}"
            shutil.move(str(original), str(dest))
            shutil.move(str(dupe), str(original))
            print(f"  📈 Swapped: kept better dupe as {original.name}, moved original to reject/")
            swapped += 1
        else:
            print(f"  ⚠️  Skipped (dupe is better but --keep-better not set): {dupe.name}")
            skipped += 1
    
    # Orphan dupes: rename to clean name (remove the (2))
    for analysis in categories["orphan_dupe"]:
        dupe = analysis["dupe"]
        original = analysis["original"]
        if not original.exists():
            shutil.move(str(dupe), str(original))
            print(f"  👻 Renamed orphan: {dupe.name} → {original.name}")
            renamed += 1
        else:
            print(f"  ⚠️  Skipped orphan (original appeared?): {dupe.name}")
            skipped += 1
    
    # Same quality / cannot compare: skip
    for cat in ("same_quality_different_content", "cannot_compare", "unknown"):
        for analysis in categories[cat]:
            print(f"  ⏭️  Skipped (needs manual review): {analysis['dupe'].name}")
            skipped += 1
    
    print()
    print("=" * 60)
    print(f"✅ Done: moved={moved}, swapped={swapped}, renamed={renamed}, skipped={skipped}")
    print("=" * 60)


if __name__ == "__main__":
    main()
