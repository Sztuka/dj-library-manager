#!/usr/bin/env python3
"""
Compare Rekordbox tracks: Music Library vs DJ MUSIC BACKUP 2025.
Shows what's unique to backup, what's duplicated, and what's safe to remove.
"""

from pathlib import Path
import unicodedata
from collections import defaultdict
import csv
import sys
import os

try:
    from pyrekordbox import Rekordbox6Database
except ImportError:
    print("pyrekordbox not available")
    exit(1)

db_path = Path.home() / "Library" / "Pioneer" / "rekordbox" / "master.db"
db = Rekordbox6Database(db_path)

BACKUP_PREFIX = str(Path.home() / "Desktop" / "MUSIC" / "DJ MUSIC BACKUP 2025")
LIBRARY_PREFIX = str(Path.home() / "Music Library")
UNSORTED_PREFIX = str(Path.home() / "Music Unsorted")
REJECTED_PREFIX = str(Path.home() / "Music Rejected")

# Collect all entries
entries = []
for content in db.get_content():
    full_path = getattr(content, "FolderPath", "").strip()
    if not full_path:
        continue
    fp = Path(unicodedata.normalize("NFC", full_path))
    
    attrs = {}
    for attr_name in ["ID", "Artist", "Title", "Name", "FolderPath"]:
        try:
            val = getattr(content, attr_name, None)
            if val is not None:
                attrs[attr_name] = val
        except Exception:
            pass
    
    exists = fp.exists()
    path_str = str(fp)
    
    if path_str.startswith(LIBRARY_PREFIX):
        source = "Music Library"
    elif path_str.startswith(BACKUP_PREFIX):
        source = "DJ MUSIC BACKUP"
    elif path_str.startswith(UNSORTED_PREFIX):
        source = "Music Unsorted"
    elif path_str.startswith(REJECTED_PREFIX):
        source = "Music Rejected"
    else:
        source = "Other"
    
    # Sub-folder within backup
    subfolder = ""
    if source == "DJ MUSIC BACKUP":
        relative = path_str[len(BACKUP_PREFIX)+1:]
        parts = relative.split("/")
        if len(parts) > 1:
            subfolder = parts[0]
    
    entries.append({
        "rb_id": str(attrs.get("ID", "")),
        "path": path_str,
        "name": fp.name,
        "stem": fp.stem.lower().strip(),
        "artist": str(attrs.get("Artist", "") or "").strip(),
        "title": str(attrs.get("Title", "") or "").strip(),
        "exists": exists,
        "source": source,
        "subfolder": subfolder,
    })

# ── Summary by source ──
print("=" * 70)
print("REKORDBOX COLLECTION — SOURCE BREAKDOWN")
print("=" * 70)
print()

by_source = defaultdict(list)
for e in entries:
    by_source[e["source"]].append(e)

for src, ents in sorted(by_source.items()):
    existing = sum(1 for e in ents if e["exists"])
    missing = len(ents) - existing
    print(f"  {src}: {len(ents)} tracks ({existing} exist, {missing} missing)")

print(f"\n  TOTAL: {len(entries)}")
print()

# ── Backup folder analysis ──
backup_entries = by_source.get("DJ MUSIC BACKUP", [])
if backup_entries:
    print("=" * 70)
    print("DJ MUSIC BACKUP 2025 — SUBFOLDER BREAKDOWN")
    print("=" * 70)
    print()
    
    by_subfolder = defaultdict(list)
    for e in backup_entries:
        by_subfolder[e["subfolder"] or "(root)"].append(e)
    
    for sf, ents in sorted(by_subfolder.items()):
        existing = sum(1 for e in ents if e["exists"])
        missing = len(ents) - existing
        print(f"  {sf}: {len(ents)} tracks ({existing} exist, {missing} missing)")
    print()

# ── Cross-reference: what's in both Library AND Backup? ──
library_entries = by_source.get("Music Library", [])

# Build matching key: normalized artist+title
def match_key(e):
    a = e["artist"].lower().strip()
    t = e["title"].lower().strip()
    if a and t:
        return f"{a}|||{t}"
    # Fallback: try to parse from filename (Artist - Title pattern)
    stem = e["stem"]
    if " - " in stem:
        parts = stem.split(" - ", 1)
        return f"{parts[0].strip()}|||{parts[1].strip()}"
    return f"|||{stem}"

library_keys = {}
for e in library_entries:
    k = match_key(e)
    library_keys[k] = e

backup_in_library = []     # Backup tracks that also exist in Music Library
backup_unique = []          # Backup tracks NOT in Music Library
backup_missing_files = []   # Backup tracks where file doesn't exist

for e in backup_entries:
    if not e["exists"]:
        backup_missing_files.append(e)
        continue
    
    k = match_key(e)
    if k in library_keys:
        backup_in_library.append((e, library_keys[k]))
    else:
        backup_unique.append(e)

print("=" * 70)
print("BACKUP vs MUSIC LIBRARY — COMPARISON")
print("=" * 70)
print()
print(f"  Backup tracks with files on disk: {len(backup_in_library) + len(backup_unique)}")
print(f"  Backup tracks with missing files: {len(backup_missing_files)}")
print()
print(f"  ✅ DUPLICATED in Music Library: {len(backup_in_library)}")
print(f"     → Safe to remove from Rekordbox (already in your library)")
print()
print(f"  🔶 UNIQUE to backup: {len(backup_unique)}")
print(f"     → These exist ONLY in backup — removing them loses them!")
print()

# Show the unique backup tracks
if backup_unique:
    print("=" * 70)
    print(f"🔶 UNIQUE BACKUP TRACKS ({len(backup_unique)})")
    print("   (NOT FOUND in Music Library — these are NOT duplicates)")
    print("=" * 70)
    print()
    
    by_sf = defaultdict(list)
    for e in backup_unique:
        by_sf[e["subfolder"] or "(root)"].append(e)
    
    for sf, ents in sorted(by_sf.items()):
        print(f"  [{sf}] — {len(ents)} unique tracks:")
        for e in sorted(ents, key=lambda x: x["name"])[:15]:
            print(f"    {e['artist']} - {e['title']}  ({e['name']})")
        if len(ents) > 15:
            print(f"    ... and {len(ents) - 15} more")
        print()

# Show duplicated tracks
if backup_in_library:
    print("=" * 70)
    print(f"✅ DUPLICATED IN LIBRARY ({len(backup_in_library)})")
    print("   (These backup entries can be safely removed from Rekordbox)")
    print("=" * 70)
    print()
    for be, le in sorted(backup_in_library, key=lambda x: x[0]["name"])[:30]:
        print(f"  {be['artist']} - {be['title']}")
        print(f"    BACKUP:  [{be['rb_id']}] {be['name']}")
        print(f"    LIBRARY: [{le['rb_id']}] {le['name']}")
        print()
    if len(backup_in_library) > 30:
        print(f"  ... and {len(backup_in_library) - 30} more")

# ── Export CSV for review ──
report_path = Path("LOGS/rekordbox_analysis.csv")
report_path.parent.mkdir(parents=True, exist_ok=True)

with open(report_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=[
        "category", "rb_id", "source", "subfolder", "artist", "title",
        "filename", "path", "exists", "library_equivalent_rb_id"
    ])
    writer.writeheader()
    
    for be, le in backup_in_library:
        writer.writerow({
            "category": "DUPLICATE_IN_LIBRARY",
            "rb_id": be["rb_id"],
            "source": be["source"],
            "subfolder": be["subfolder"],
            "artist": be["artist"],
            "title": be["title"],
            "filename": be["name"],
            "path": be["path"],
            "exists": be["exists"],
            "library_equivalent_rb_id": le["rb_id"],
        })
    
    for e in backup_unique:
        writer.writerow({
            "category": "UNIQUE_IN_BACKUP",
            "rb_id": e["rb_id"],
            "source": e["source"],
            "subfolder": e["subfolder"],
            "artist": e["artist"],
            "title": e["title"],
            "filename": e["name"],
            "path": e["path"],
            "exists": e["exists"],
            "library_equivalent_rb_id": "",
        })
    
    for e in backup_missing_files:
        writer.writerow({
            "category": "BACKUP_FILE_MISSING",
            "rb_id": e["rb_id"],
            "source": e["source"],
            "subfolder": e["subfolder"],
            "artist": e["artist"],
            "title": e["title"],
            "filename": e["name"],
            "path": e["path"],
            "exists": e["exists"],
            "library_equivalent_rb_id": "",
        })
    
    # Also include all missing non-backup files
    for src, ents in by_source.items():
        if src == "DJ MUSIC BACKUP":
            continue
        for e in ents:
            if not e["exists"]:
                writer.writerow({
                    "category": f"MISSING_{src.upper().replace(' ', '_')}",
                    "rb_id": e["rb_id"],
                    "source": e["source"],
                    "subfolder": "",
                    "artist": e["artist"],
                    "title": e["title"],
                    "filename": e["name"],
                    "path": e["path"],
                    "exists": False,
                    "library_equivalent_rb_id": "",
                })

print()
print("=" * 70)
print(f"📋 Full report saved to: {report_path}")
print("=" * 70)
print()
print("NEXT STEPS:")
print("  1. Review the UNIQUE_IN_BACKUP tracks above")
print("     If you want them in your library → run 'scan' on them")
print("  2. In Rekordbox:")
print("     a. File → Display All Missing Files → Select All → Delete")
print(f"        (removes {len(backup_missing_files) + sum(1 for e in entries if not e['exists'] and e['source'] != 'DJ MUSIC BACKUP')} ghost entries)")
print("     b. For duplicates: search each backup track, delete the backup copy")
print(f"        ({len(backup_in_library)} safe to remove)")
