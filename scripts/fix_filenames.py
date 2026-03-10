#!/usr/bin/env python3
"""
Fix filenames for audio files using local tags + filename parsing.

Workflow:
    1. Export CSV:  scripts/fix_filenames.py <folder> --export-csv
       → generates <folder>/fix_filenames.csv with proposed metadata
    2. Edit CSV:    open in Excel/Numbers/editor, fix artist/title/version columns
    3. Apply:       scripts/fix_filenames.py <folder> --from-csv [--apply] [--write-tags]

Direct mode (no CSV):
    scripts/fix_filenames.py <folder>              # dry-run from tags
    scripts/fix_filenames.py <folder> --apply      # actually rename

Example:
    .venv/bin/python scripts/fix_filenames.py data/ab_test --export-csv
    # ... edit data/ab_test/fix_filenames.csv ...
    .venv/bin/python scripts/fix_filenames.py data/ab_test --from-csv
    .venv/bin/python scripts/fix_filenames.py data/ab_test --from-csv --apply --write-tags
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from djlib.tags import read_tags
from djlib.enrich import derive_local_metadata
from djlib.filename import build_final_filename, extension_for

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aiff", ".aif", ".m4a", ".ogg"}


def discover_audio_files(folder: Path) -> list[Path]:
    """Recursively find audio files in folder."""
    files = []
    for p in sorted(folder.rglob("*")):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS and not p.name.startswith("."):
            files.append(p)
    return files


def compute_new_name(path: Path) -> dict:
    """Read tags, normalize, build new filename. Returns info dict."""
    tags = read_tags(path) or {}
    artist, title, version = derive_local_metadata(path, tags)

    bpm = (tags.get("bpm") or "").strip()
    key = (tags.get("key_camelot") or "").strip()

    ext = extension_for(path)
    new_name = build_final_filename(artist, title, version, key, bpm, ext)

    return {
        "path": path,
        "old_name": path.name,
        "new_name": new_name,
        "changed": path.name != new_name,
        "artist": artist,
        "title": title,
        "version": version,
        "bpm": bpm,
        "key": key,
        "tags_raw": tags,
    }


def main():
    parser = argparse.ArgumentParser(description="Fix audio filenames from tags + filename parsing")
    parser.add_argument("folder", type=str, help="Folder with audio files (recursive)")
    parser.add_argument("--apply", action="store_true", help="Actually rename files (default: dry-run)")
    parser.add_argument("--write-tags", action="store_true",
                        help="Also write normalized artist/title/version back to audio tags")
    parser.add_argument("--export-csv", action="store_true",
                        help="Export editable CSV with proposed metadata (edit, then use --from-csv)")
    parser.add_argument("--from-csv", action="store_true",
                        help="Read artist/title/version from fix_filenames.csv instead of tags")
    args = parser.parse_args()

    folder = Path(args.folder).expanduser().resolve()
    if not folder.exists():
        print(f"❌ Folder not found: {folder}")
        sys.exit(1)

    # ── Export CSV mode ──────────────────────────────────────────────────
    if args.export_csv:
        export_csv(folder)
        return

    # ── From CSV mode ────────────────────────────────────────────────────
    if args.from_csv:
        results = load_from_csv(folder)
    else:
        # ── Direct mode (from tags) ─────────────────────────────────────
        files = discover_audio_files(folder)
        print(f"Found {len(files)} audio files in {folder}\n")
        if not files:
            return
        results = []
        for f in files:
            try:
                info = compute_new_name(f)
                results.append(info)
            except Exception as e:
                print(f"⚠ Error processing {f.name}: {e}")

    display_results(results, folder)
    if not check_collisions(results):
        return

    if not args.apply:
        print(f"\nDry-run mode. Use --apply to rename files.")
        return

    apply_renames(results, args.write_tags)


def export_csv(folder: Path):
    """Export editable CSV with current + proposed metadata."""
    files = discover_audio_files(folder)
    print(f"Found {len(files)} audio files in {folder}")

    CSV_FIELDS = [
        "genre_folder", "old_filename",
        "artist", "title", "version", "key", "bpm",
        "new_filename",
        "tag_artist_raw", "tag_title_raw",
    ]

    rows = []
    for p in files:
        try:
            tags = read_tags(p) or {}
            artist, title, version = derive_local_metadata(p, tags)
            bpm = (tags.get("bpm") or "").strip()
            key = (tags.get("key_camelot") or "").strip()
            ext = extension_for(p)
            new_name = build_final_filename(artist, title, version, key, bpm, ext)
            rel = p.relative_to(folder)
            genre_folder = rel.parts[0] if len(rel.parts) > 1 else ""
            rows.append({
                "genre_folder": genre_folder,
                "old_filename": p.name,
                "artist": artist,
                "title": title,
                "version": version,
                "key": key,
                "bpm": bpm,
                "new_filename": new_name,
                "tag_artist_raw": (tags.get("artist") or "").strip(),
                "tag_title_raw": (tags.get("title") or "").strip(),
            })
        except Exception as e:
            print(f"⚠ Error processing {p.name}: {e}")

    out = folder / "fix_filenames.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)

    print(f"✅ Wrote {len(rows)} rows to {out}")
    print(f"\nEdit artist/title/version columns, then run:")
    print(f"  .venv/bin/python scripts/fix_filenames.py {folder} --from-csv")
    print(f"  .venv/bin/python scripts/fix_filenames.py {folder} --from-csv --apply --write-tags")


def load_from_csv(folder: Path) -> list[dict]:
    """Load metadata from user-edited CSV, rebuild filenames."""
    csv_path = folder / "fix_filenames.csv"
    if not csv_path.exists():
        print(f"❌ CSV not found: {csv_path}")
        print(f"   Run --export-csv first.")
        sys.exit(1)

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        csv_rows = list(reader)

    print(f"Loaded {len(csv_rows)} rows from {csv_path}\n")

    results = []
    for row in csv_rows:
        genre_folder = row.get("genre_folder", "")
        old_filename = row["old_filename"]
        artist = (row.get("artist") or "").strip()
        title = (row.get("title") or "").strip()
        version = (row.get("version") or "").strip()
        key = (row.get("key") or "").strip()
        bpm = (row.get("bpm") or "").strip()

        # Find actual file on disk
        if genre_folder:
            old_path = folder / genre_folder / old_filename
        else:
            old_path = folder / old_filename

        if not old_path.exists():
            print(f"⚠ File not found (skipping): {old_path}")
            continue

        ext = extension_for(old_path)
        new_name = build_final_filename(artist, title, version, key, bpm, ext)

        results.append({
            "path": old_path,
            "old_name": old_filename,
            "new_name": new_name,
            "changed": old_filename != new_name,
            "artist": artist,
            "title": title,
            "version": version,
            "bpm": bpm,
            "key": key,
        })

    return results


def display_results(results: list[dict], folder: Path):
    """Show proposed changes."""
    changed = [r for r in results if r["changed"]]
    unchanged = [r for r in results if not r["changed"]]

    if changed:
        print(f"{'='*80}")
        print(f"CHANGES ({len(changed)} files):")
        print(f"{'='*80}")
        for r in changed:
            rel = r["path"].relative_to(folder)
            parent = rel.parent
            prefix = f"  {parent}/" if str(parent) != "." else "  "
            print(f"\n{prefix}{r['old_name']}")
            print(f"  → {r['new_name']}")
            meta_parts = []
            if r["artist"]:
                meta_parts.append(f"artist={r['artist']}")
            if r["title"]:
                meta_parts.append(f"title={r['title']}")
            if r["version"]:
                meta_parts.append(f"ver={r['version']}")
            if r["key"]:
                meta_parts.append(f"key={r['key']}")
            if r["bpm"]:
                meta_parts.append(f"bpm={r['bpm']}")
            print(f"    [{', '.join(meta_parts)}]")

    if unchanged:
        print(f"\n{'='*80}")
        print(f"UNCHANGED ({len(unchanged)} files):")
        print(f"{'='*80}")
        for r in unchanged:
            rel = r["path"].relative_to(folder)
            print(f"  ✓ {rel}")

    print(f"\n{'='*80}")
    print(f"SUMMARY: {len(changed)} to rename, {len(unchanged)} already OK, {len(results)} total")
    print(f"{'='*80}")


def check_collisions(results: list[dict]) -> bool:
    """Check for filename collisions. Returns True if safe."""
    changed = [r for r in results if r["changed"]]
    by_dir: dict[Path, list[dict]] = {}
    for r in changed:
        d = r["path"].parent
        by_dir.setdefault(d, []).append(r)

    collisions = []
    for d, items in by_dir.items():
        names: dict[str, dict] = {}
        for r in items:
            nn = r["new_name"]
            if nn in names:
                collisions.append((d, nn, names[nn], r))
            else:
                names[nn] = r

    if collisions:
        print(f"\n⚠️  COLLISIONS DETECTED ({len(collisions)}):")
        for d, name, r1, r2 in collisions:
            print(f"  {d}/{name}")
            print(f"    ← {r1['old_name']}")
            print(f"    ← {r2['old_name']}")
        print("Cannot apply until collisions are resolved.")
        return False
    return True


def apply_renames(results: list[dict], write_tags: bool):
    """Apply renames and optionally write tags."""
    changed = [r for r in results if r["changed"]]
    print(f"\nApplying {len(changed)} renames...")
    success = 0
    errors = 0

    for r in changed:
        old_path = r["path"]
        new_path = old_path.parent / r["new_name"]

        if new_path.exists() and new_path != old_path:
            print(f"  ⚠ SKIP (target exists): {r['new_name']}")
            errors += 1
            continue

        try:
            old_path.rename(new_path)
            r["_renamed_path"] = new_path
            success += 1
            print(f"  ✓ {r['old_name']} → {r['new_name']}")
        except Exception as e:
            print(f"  ✗ {r['old_name']}: {e}")
            errors += 1

    if write_tags:
        from djlib.tags import write_tags as wt
        from djlib.filename import merge_title_and_version

        print(f"\nWriting normalized tags...")
        for r in changed:
            new_path = r.get("_renamed_path")
            if not new_path or not new_path.exists():
                continue
            updates: dict[str, str] = {}
            if r["artist"]:
                updates["artist"] = r["artist"]
            if r["title"]:
                full_title = r["title"]
                if r["version"]:
                    full_title = merge_title_and_version(r["title"], r["version"])
                updates["title"] = full_title
            if updates:
                try:
                    wt(new_path, updates)
                except Exception as e:
                    print(f"  ⚠ Tag write failed for {new_path.name}: {e}")

    print(f"\nDone: {success} renamed, {errors} errors")


if __name__ == "__main__":
    main()
