from __future__ import annotations
import argparse, csv, re, time, os, json, shutil, unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
import warnings

# Suppress Python 3.13 deprecation warnings from audioread (aifc/sunau modules)
warnings.filterwarnings("ignore", category=DeprecationWarning, module="audioread")

# --- Core importy (nasze moduły) ---
from djlib.config import (
    reconfigure, ensure_base_dirs, CONFIG_FILE,
    INBOX_DIR, LOGS_DIR, CSV_PATH, REJECTED_CSV_PATH, AUDIO_EXTS, UNSORTED_CSV
)
from djlib.csvdb import load_records, save_records, load_rejected, save_rejected
from djlib.tags import read_tags, write_tags
from djlib.rekordbox_status import was_analyzed, extract_metadata_from_db
from djlib.enrich import suggest_metadata, enrich_online_for_row, derive_local_metadata
from djlib.metadata.canonical_mb import import_canonical_dump as do_import_canonical_dump, get_canonical_db_path
from djlib.fingerprint import file_sha256, fingerprint_info
from djlib.filename import build_final_filename, extension_for, split_title_and_version, merge_title_and_version
from djlib.mover import resolve_target_path, move_with_rename, utc_now_str
from djlib.ml.export_dataset import export_training_dataset
from djlib.tag_cleaner import clean_tags
from djlib.unsorted import load_unsorted_rows, write_unsorted_rows, EXPORT_DISPOSITIONS
from djlib.external_sync import (
    import_rekordbox_snapshot, 
    import_traktor_snapshot,
    create_path_map,
    sync_rekordbox_paths,
    sync_traktor_paths
)
from djlib.djlib_tags import read_djlib_tags, generate_track_id  # NEW: Read persistent track IDs
try:
    from djlib.audio import check_env as audio_check_env
    from djlib.audio import analyze as audio_analyze
    from djlib.audio.cache import get_analysis
except Exception:
    # If audio backend is unavailable, fall back to None
    audio_check_env = None  # type: ignore
    audio_analyze = None  # type: ignore
    get_analysis = None  # type: ignore

# --- Pomocnicze ---
REPO_ROOT = Path(__file__).resolve().parents[1]


def _ensure_unique_path(target: Path) -> Path:
    """Return the first free variant of ``target`` by appending ``' (N)'``.

    Two different mojibake filenames in the same folder can sanitize to the
    same clean name (e.g. ``foo"bar.mp3`` and ``foo“bar.mp3`` both collapse
    to ``foo bar.mp3``). The two files are not necessarily duplicates —
    same stem is just a rename coincidence. This helper only ensures the
    destination path is unique; content-level dedup is a separate concern
    and not assumed here.

    NFC-compares against the parent listing so macOS NFD-on-disk doesn't
    make a candidate look free when it actually collides.
    """
    if not target.exists():
        # Also guard against NFD-on-disk: `target.exists()` follows symlinks
        # and normalizes, but on case-insensitive APFS an NFC lookup may
        # miss an NFD twin. Compare against the normalized directory list.
        taken = _parent_nfc_names_uncached(target.parent)
        if unicodedata.normalize("NFC", target.name) not in taken:
            return target
    stem = target.stem
    ext = target.suffix
    parent = target.parent
    taken_names = _parent_nfc_names_uncached(parent)
    n = 2
    while True:
        candidate = parent / f"{stem} ({n}){ext}"
        if (
            not candidate.exists()
            and unicodedata.normalize("NFC", candidate.name) not in taken_names
        ):
            return candidate
        n += 1
        if n > 10_000:
            raise RuntimeError(f"Giving up finding a unique name for {target}")


def _parent_nfc_names_uncached(parent: Path) -> frozenset[str]:
    """Return NFC-normalized names of entries in ``parent``, empty on error."""
    try:
        return frozenset(
            unicodedata.normalize("NFC", n) for n in os.listdir(str(parent))
        )
    except OSError:
        return frozenset()


def _check_path_is_safe(
    p: Path,
    parent_names_cache: Optional[Dict[str, frozenset[str]]] = None,
) -> tuple[bool, str]:
    """Return (is_safe, reason).

    A path is "safe" for downstream tag writes / renames when:
      * its ``name`` contains no C0/C1 control chars (catches typical
        double-encoded UTF-8 mojibake — typographic quotes, em-dashes),
      * the file exists, AND
      * the name appears in the parent directory listing under NFC-normalized
        comparison (catches stale ``rglob`` results, broken symlinks, and
        mid-rename races where ``Path`` disagrees with actual storage).

    When ``parent_names_cache`` is provided, directory listings are cached per
    parent; safe to share across many calls within one command run.
    """
    try:
        name = p.name
        for ch in name:
            cp = ord(ch)
            if cp < 0x20 or 0x80 <= cp <= 0x9F:
                return False, f"control char U+{cp:04X} (likely mojibake — typographic quotes / em-dash)"

        if not p.exists():
            return False, "file does not exist"

        parent_key = str(p.parent)
        if parent_names_cache is not None:
            names = parent_names_cache.get(parent_key)
            if names is None:
                names = _parent_nfc_names_uncached(p.parent)
                parent_names_cache[parent_key] = names
        else:
            names = _parent_nfc_names_uncached(p.parent)

        name_nfc = unicodedata.normalize("NFC", name)
        if name_nfc not in names:
            return False, "filename not found in parent directory listing after NFC normalize"

        return True, ""
    except Exception as e:
        return False, f"exception during safety check: {e}"
LEGACY_ML_MSG = (
    "Legacy ML pipeline (FMA) został usunięty. "
    "Trenowanie i predykcje ML wrócą po wdrożeniu lokalnych modeli na bazie Essentia. "
    "Na razie skorzystaj z `ml-export-training-dataset`, aby przygotować CSV do dalszej pracy."
)


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _load_unsorted() -> List[Dict[str, str]]:
    return load_unsorted_rows(UNSORTED_CSV)


def _save_unsorted(rows: List[Dict[str, str]]) -> None:
    """Save rows to unsorted.csv."""
    # No longer needs bucket choices (legacy system removed)
    write_unsorted_rows(UNSORTED_CSV, rows, [])

# ============ KOMENDY ============

def cmd_configure(_: argparse.Namespace) -> None:
    print("\n" + "=" * 60)
    print("DJ LIBRARY MANAGER — KONFIGURACJA")
    print("=" * 60)
    print()
    
    cfg, path = reconfigure()
    ensure_base_dirs()
    
    print()
    print("=" * 60)
    print("✅ KONFIGURACJA ZAPISANA")
    print("=" * 60)
    print(f"Plik konfiguracyjny: {path}")
    print(f"Library root:        {cfg.library_root}")
    print(f"Inbox (UNSORTED):    {cfg.inbox_dir}")
    print()
    
    # Optional: Configure metadata sources
    print("=" * 60)
    print("OPCJONALNIE: Konfiguracja źródeł metadanych online")
    print("=" * 60)
    print("\nDla najlepszych wyników enrichment możesz skonfigurować:")
    print("  • Beatport (EDM genres, artwork 1400x1400, BPM/Key)")
    print("  • SoundCloud (tagi społecznościowe, artwork)")
    print()
    
    try:
        choice = input("Czy chcesz skonfigurować teraz? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        choice = "n"
    
    if choice in {"y", "yes"}:
        # Beatport setup
        print("\n" + "─" * 60)
        print("1️⃣  BEATPORT SETUP")
        print("─" * 60)
        try:
            beatport_choice = input("Skonfigurować Beatport? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            beatport_choice = "n"
        
        if beatport_choice in {"y", "yes"}:
            from djlib.metadata.beatport import set_beatport_credentials, get_valid_token
            import getpass
            
            username = input("  Beatport username: ").strip()
            if username:
                password = getpass.getpass("  Beatport password: ")
                if password:
                    try:
                        set_beatport_credentials(username, password)
                        print("  ✅ Credentials saved - testing token refresh...")
                        token = get_valid_token()
                        if token:
                            print("  ✅ Beatport ready!")
                    except Exception as e:
                        print(f"  ⚠️  Setup failed: {e}")
                else:
                    print("  ⏭  Pominięto (brak hasła)")
            else:
                print("  ⏭  Pominięto (brak username)")
        else:
            print("  ⏭  Pominięto - możesz uruchomić później: python -m djlib.cli setup-beatport")
        
        # SoundCloud setup (future)
        print("\n" + "─" * 60)
        print("2️⃣  SOUNDCLOUD SETUP")
        print("─" * 60)
        print("  ℹ️  SoundCloud używa auto-refresh client_id (nie wymaga konfiguracji)")
        print("  ✅ Gotowy do użycia!")
        
        print("\n" + "=" * 60)
        print("Konfiguracja zakończona!")
        print("=" * 60)
    else:
        print("⏭  Pominięto - możesz uruchomić później:")
        print("   • python -m djlib.cli setup-beatport")
        print()


def cmd_scan(args: argparse.Namespace) -> None:
    ensure_base_dirs()
    strict = getattr(args, "strict", False)
    library_rows = load_records(CSV_PATH)
    staging_rows = _load_unsorted()
    known_hashes = {r.get("file_hash", "") for r in library_rows if r.get("file_hash")}
    known_fps = {r.get("fingerprint", "") for r in library_rows if r.get("fingerprint")}
    known_hashes.update({r.get("file_hash", "") for r in staging_rows if r.get("file_hash")})
    known_fps.update({r.get("fingerprint", "") for r in staging_rows if r.get("fingerprint")})
    
    # ── Load rejected registry (previously rejected files should not re-enter) ──
    rejected_rows = load_rejected(REJECTED_CSV_PATH)
    rejected_hashes = {r.get("file_hash", "") for r in rejected_rows if r.get("file_hash")}
    rejected_fps = {r.get("fingerprint", "") for r in rejected_rows if r.get("fingerprint")}
    if rejected_rows:
        print(f"🚫 Loaded {len(rejected_rows)} previously rejected files (will skip matches)")
    
    # Also track known file paths to prevent duplicates when tags change
    known_paths = {r.get("file_path", "") for r in library_rows if r.get("file_path")}
    known_paths.update({r.get("file_path", "") for r in staging_rows if r.get("file_path")})

    # Map fingerprint → winner row (mutable dict ref) so we can record duplicate
    # paths on the winner's row when skipping acoustic duplicates.
    # Only staging rows are tracked — library rows are already applied and
    # their duplicate_paths field cannot be updated here.
    fp_to_row: Dict[str, Dict[str, str]] = {
        r["fingerprint"]: r
        for r in staging_rows
        if r.get("fingerprint")
    }
    
    # Get current Rekordbox track IDs for auto-tagging
    from djlib.external_sync import get_rekordbox_track_ids, get_traktor_track_ids
    rekordbox_mapping = get_rekordbox_track_ids()
    traktor_mapping = get_traktor_track_ids()
    
    if rekordbox_mapping:
        print(f"📖 Found {len(rekordbox_mapping)} tracks in Rekordbox database")
    if traktor_mapping:
        print(f"📖 Found {len(traktor_mapping)} tracks in Traktor collection")

    status_path = LOGS_DIR / "scan_status.json"

    def _write_status(data: Dict[str, Any]) -> None:
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            with status_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

    all_files = [p for p in INBOX_DIR.glob("**/*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
    total = len(all_files)
    
    # Check if all files have been analyzed in Rekordbox
    # strict=True: ONLY accept files in Rekordbox DB (not just TBPM/TKEY tags)
    # strict=False: Accept DB OR tags (allows Traktor/Serato-analyzed files)
    not_analyzed_paths: List[Path] = []
    for p in all_files:
        if not was_analyzed(p, strict=strict):
            not_analyzed_paths.append(p)
    
    if not_analyzed_paths:
        if strict:
            print("\n❌ ERROR: Some files are not in Rekordbox database (--strict mode).")
            print("   These files may have BPM/Key tags from Traktor/Serato,")
            print("   but weren't confirmed to be analyzed in Rekordbox specifically.")
            print("\n   Solutions:")
            print("   1. Import to Rekordbox and run Analyze, then re-run scan")
            print("   2. Or run scan WITHOUT --strict to accept tags from any source")
        else:
            print("\n❌ ERROR: Some files have no BPM/Key analysis.")
            print("   Files need either:")
            print("   - Rekordbox DB entry with analysis (preferred)")
            print("   - OR TBPM/TKEY tags from any DJ software (Traktor/Serato/etc)")
        print("\n   Files needing attention:")
        for p in not_analyzed_paths:
            print(f"     - {p.relative_to(INBOX_DIR)}")
        print()
        return  # Abort without generating unsorted.csv
    
    processed = 0
    added = 0
    errors = 0
    missing_fpcalc = False
    _write_status(
        {
            "state": "running",
            "total": total,
            "processed": 0,
            "added": 0,
            "errors": 0,
            "last_file": "",
        }
    )
    
    # Batch status writes to reduce disk I/O (every N files instead of every file)
    _STATUS_BATCH_SIZE = 10
    _last_status_write = 0

    new_rows: List[Dict[str, str]] = []
    for p in all_files:
        # Skip if file path already in staging or library (prevents duplicates when tags change)
        if str(p) in known_paths:
            processed += 1
            continue
        
        fhash = file_sha256(p)
        if fhash in known_hashes:
            processed += 1
            continue
        
        # ── Check rejected registry ──
        if fhash in rejected_hashes:
            print(f"   🚫 [REJECTED] {p.name} (hash matches previously rejected file)")
            processed += 1
            continue

        tags = read_tags(p)
        tags_original = dict(tags)
        
        # Extract metadata from Rekordbox DB if available (authoritative source)
        # Rekordbox may not write TBPM/TKEY to files (especially FLAC), but data is always in DB
        db_meta = extract_metadata_from_db(p)
        if db_meta:
            # Override file tags with DB data (DB is more authoritative)
            tags.update(db_meta)
        
        artist_local, title_local, version_local = derive_local_metadata(p, tags)
        tags["artist"] = artist_local
        tags["title"] = title_local
        tags["version_info"] = version_local
        try:
            dur, fp = fingerprint_info(p)
        except Exception as e:
            fp = ""
            dur = 0
            errors += 1
            if "fpcalc" in str(e).lower():
                missing_fpcalc = True

        is_dup = fp and fp in known_fps

        # Skip acoustic duplicates (same fingerprint = same audio, different path/quality)
        if is_dup:
            # Record this path on the winner's row so cmd_apply can merge cues later
            winner = fp_to_row.get(fp)
            if winner is not None:
                try:
                    existing = winner.get("duplicate_paths") or "[]"
                    dup_list: list = json.loads(existing) if existing.strip() else []
                    dup_str = str(p)
                    if dup_str not in dup_list:
                        dup_list.append(dup_str)
                        winner["duplicate_paths"] = json.dumps(dup_list)
                except Exception:
                    pass
            print(f"   ⊘ [DUPLICATE] {p.name} — fingerprint already known, skipping")
            processed += 1
            continue

        # ── Check rejected registry by fingerprint ──
        if fp and fp in rejected_fps:
            print(f"   🚫 [REJECTED] {p.name} (fingerprint matches previously rejected file)")
            processed += 1
            continue

        # Scan uses only local metadata (fast) - online enrichment is separate workflow
        sugg = suggest_metadata(p, tags, enable_online=False)
        if (sugg.get("duration_suggest") or "").strip() == "" and dur:
            mm = dur // 60
            ss = dur % 60
            sugg["duration_suggest"] = f"{mm}:{ss:02d}"

        # Check if file has DJLIB_TRACK_ID tag (from Phase 1 snapshot import)
        djlib_tags = read_djlib_tags(p)
        needs_id_update = False
        
        if djlib_tags.get('track_id'):
            # Reuse existing track_id (file was previously in DJ software)
            track_id = djlib_tags['track_id']
            rekordbox_id = djlib_tags.get('rekordbox_id', '')
            traktor_id = djlib_tags.get('traktor_id', '')
            
            # ✅ VALIDATION: Check if IDs in tags match current DJ software databases
            p_nfc = Path(unicodedata.normalize('NFC', str(p)))
            current_rekordbox_id = rekordbox_mapping.get(p_nfc, '')
            current_traktor_id = traktor_mapping.get(p_nfc, '')
            
            if current_rekordbox_id and current_rekordbox_id != rekordbox_id:
                print(f"   🔧 Fixing Rekordbox ID for {p.name}: {rekordbox_id} → {current_rekordbox_id}")
                rekordbox_id = current_rekordbox_id
                needs_id_update = True
            
            if current_traktor_id and current_traktor_id != traktor_id:
                print(f"   🔧 Fixing Traktor ID for {p.name}: {traktor_id} → {current_traktor_id}")
                traktor_id = current_traktor_id
                needs_id_update = True
        else:
            # Generate new track_id (new file)
            track_id = generate_track_id(p, tags.get("artist", ""), tags.get("title", ""))
            
            # Get DJ software IDs from current DBs (if file is in Rekordbox/Traktor)
            p_nfc = Path(unicodedata.normalize('NFC', str(p)))
            rekordbox_id = rekordbox_mapping.get(p_nfc, '')
            traktor_id = traktor_mapping.get(p_nfc, '')
            needs_id_update = True
        
        # Tag file with DJLIB custom tags (always on first scan, or if IDs changed)
        if needs_id_update:
            try:
                from djlib.djlib_tags import write_djlib_tags
                write_djlib_tags(
                    p,
                    track_id=track_id,
                    rekordbox_id=rekordbox_id if rekordbox_id else None,
                    traktor_id=traktor_id if traktor_id else None,
                    original_path=str(p)
                )
            except Exception as e:
                print(f"⚠️  Could not tag {p.name}: {e}")
        
        rec: Dict[str, str] = {
            "track_id": track_id,
            "rekordbox_id": rekordbox_id,
            "traktor_id": traktor_id,
            "file_path": str(p),
            "file_hash": fhash,
            "fingerprint": fp,
            "added_date": utc_now_str(),
            "is_duplicate": "false",
            "artist": _safe_str(tags.get("artist")).strip(),
            "title": _safe_str(tags.get("title")).strip(),
            "version_info": _safe_str(tags.get("version_info")).strip(),
            "genre": _safe_str(tags.get("genre")).strip(),
            "bpm": _safe_str(tags.get("bpm")),
            "key_camelot": _safe_str(tags.get("key_camelot")),
            "energy_hint": _safe_str(tags.get("energy_hint")),
            "tag_artist_original": _safe_str(tags_original.get("artist")),
            "tag_title_original": _safe_str(tags_original.get("title")),
            "tag_genre_original": _safe_str(tags_original.get("genre")),
            "tag_bpm_original": _safe_str(tags_original.get("bpm")),
            "tag_key_original": _safe_str(tags_original.get("key_camelot")),
            "target_subfolder": "",
            "must_play": "",
            "occasion_tags": "",
            "notes": "",
            "pop_playcount": _safe_str(sugg.get("pop_playcount")),
            "pop_listeners": _safe_str(sugg.get("pop_listeners")),
            "meta_source": _safe_str(sugg.get("meta_source")),
            "duplicate_paths": "",
            "disposition": "",
        }
        for key in [
            "artist_suggest",
            "title_suggest",
            "version_suggest",
            "genre_suggest",
            "album_suggest",
            "year_suggest",
            "duration_suggest",
            "genres_musicbrainz",
            "genres_lastfm",
            "genres_soundcloud",
        ]:
            rec[key] = _safe_str(sugg.get(key, ""))
        staging_rows.append(rec)
        new_rows.append(rec)
        known_hashes.add(fhash)
        if fp:
            known_fps.add(fp)
            fp_to_row[fp] = rec  # allow duplicates later in this scan to find winner
        added += 1
        processed += 1
        
        # Write status periodically (every N files) to reduce disk I/O
        if processed - _last_status_write >= _STATUS_BATCH_SIZE:
            _last_status_write = processed
            _write_status(
                {
                    "state": "running",
                    "total": total,
                    "processed": processed,
                    "added": added,
                    "errors": errors,
                    "last_file": str(p),
                    "missing_fpcalc": missing_fpcalc,
                }
            )

    # Remove duplicates already present in staging_rows (from previous scans before this fix).
    # Keep first occurrence by fingerprint, then by file_hash.
    seen_fps_clean: set = set()
    seen_hashes_clean: set = set()
    cleaned: List[Dict[str, str]] = []
    removed_dups = 0
    for row in staging_rows:
        rfp = row.get("fingerprint") or ""
        rhash = row.get("file_hash") or ""
        if rfp and rfp in seen_fps_clean:
            removed_dups += 1
            continue
        if rhash and rhash in seen_hashes_clean:
            removed_dups += 1
            continue
        if rfp:
            seen_fps_clean.add(rfp)
        if rhash:
            seen_hashes_clean.add(rhash)
        cleaned.append(row)
    if removed_dups:
        print(f"   ⊘ Removed {removed_dups} duplicate row(s) from unsorted.csv")
        staging_rows = cleaned

    if new_rows or removed_dups:
        _save_unsorted(staging_rows)
        print(f"Scanned {len(new_rows)} files. Saved to {UNSORTED_CSV}.")
    else:
        print("No new files to add.")

    _write_status(
        {
            "state": "done",
            "total": total,
            "processed": processed,
            "added": added,
            "errors": errors,
            "missing_fpcalc": missing_fpcalc,
            "unsorted_rows": len(staging_rows),
        }
    )

# REMOVED: _load_rules, _decide_for_row, cmd_auto_decide, cmd_auto_decide_smart
# Legacy bucketing system (CLUB/OPEN FORMAT) has been replaced with LIBRARY/Artist/Album structure.
# Auto-bucketing logic is no longer relevant. Use manual genre selection in unsorted.csv instead.


def cmd_dedup_staging(args: argparse.Namespace) -> None:
    """Deduplicate unsorted.csv by acoustic fingerprint / file hash.

    Scans existing unsorted.csv rows for duplicates (same fingerprint or same
    file hash), keeps the first occurrence as the winner, records the others
    in the winner's duplicate_paths field, and removes the duplicate rows.
    Run this once after upgrading to fix entries scanned before duplicate
    tracking was added.

    Use --dry-run to preview without writing.
    """
    dry_run = getattr(args, "dry_run", False)
    rows = _load_unsorted()
    if not rows:
        print("unsorted.csv is empty.")
        return

    seen_fps: Dict[str, int] = {}   # fingerprint -> index in `rows`
    seen_hashes: Dict[str, int] = {}  # file_hash -> index in `rows`
    to_remove: List[int] = []

    def _add_dup_path(winner_row: Dict[str, str], dup_path: str) -> None:
        existing = winner_row.get("duplicate_paths") or "[]"
        try:
            dup_list: list = json.loads(existing) if existing.strip() else []
        except Exception:
            dup_list = []
        if dup_path and dup_path not in dup_list:
            dup_list.append(dup_path)
            winner_row["duplicate_paths"] = json.dumps(dup_list)

    for i, row in enumerate(rows):
        fp = row.get("fingerprint") or ""
        fhash = row.get("file_hash") or ""
        fp_dup = fp and fp in seen_fps
        hash_dup = not fp_dup and fhash and fhash in seen_hashes

        if fp_dup:
            winner_idx = seen_fps[fp]
            dup_file = row.get("file_path") or f"row#{i}"
            _add_dup_path(rows[winner_idx], dup_file)
            to_remove.append(i)
            print(f"   ⊘ {Path(dup_file).name} → duplicate of {Path(rows[winner_idx].get('file_path', '')).name}")
        elif hash_dup:
            winner_idx = seen_hashes[fhash]
            dup_file = row.get("file_path") or f"row#{i}"
            _add_dup_path(rows[winner_idx], dup_file)
            to_remove.append(i)
            print(f"   ⊘ {Path(dup_file).name} → duplicate of {Path(rows[winner_idx].get('file_path', '')).name}")
        else:
            if fp:
                seen_fps[fp] = i
            if fhash:
                seen_hashes[fhash] = i

    if not to_remove:
        print(f"No duplicates found in unsorted.csv ({len(rows)} rows checked).")
        return

    print(f"\nFound {len(to_remove)} duplicate(s) in {len(rows)} rows.")
    if dry_run:
        print("Dry-run mode — no changes written.")
        return

    for i in reversed(to_remove):
        rows.pop(i)

    _save_unsorted(rows)
    print(f"Removed {len(to_remove)} duplicate row(s). unsorted.csv now has {len(rows)} rows.")
    print("Run 'apply' to merge cue points and delete duplicate files.")


def cmd_enrich_online(args: argparse.Namespace) -> None:
    """Wzbogaca metadane (suggest_*) dla pozycji pending korzystając z MusicBrainz/AcoustID/Last.fm + WS classifier.
    Prowadzi status w LOGS/enrich_status.json, aby UI mogło pokazywać postęp.
    Nie nadpisuje już zaakceptowanych. Nie zmienia BPM/Key.
    """
    rows = _load_unsorted()
    force_genres = bool(getattr(args, "force_genres", False))
    todo = [r for r in rows if (r.get("disposition") or "").lower().strip() not in EXPORT_DISPOSITIONS]
    total = len(todo)
    processed = 0
    changed = 0
    mb_set = 0
    lfm_set = 0
    # Check API credentials presence for diagnostics
    try:
        from djlib.config import get_lastfm_api_key
        _lfm_key_present = bool(get_lastfm_api_key())
    except Exception:
        _lfm_key_present = False

    # status plik
    status_path = LOGS_DIR / "enrich_status.json"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    def _now_iso() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat() + "Z"

    # Struktura statusu (rozszerzona zgodnie z ARCHITECTURE.md)
    status_doc = {
        "started_at": _now_iso(),
        "completed_at": "",
        "rows_total": total,
        "rows_processed": 0,
        "updated": 0,
        "state": "running",
        "last_file": "",
        "soundcloud": {
            "client_id_status": "unknown",
            "decision": "pending",  # active | skipped | aborted
            "prompt_shown": False,
            "attempted_requests": 0,
            "timestamp": _now_iso(),
        },
        "sources_counts": {
            "musicbrainz": 0,
            "lastfm": 0,
            "soundcloud": 0,
        }
    }

    def _flush_status() -> None:
        try:
            with status_path.open("w", encoding="utf-8") as f:
                json.dump(status_doc, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _classifier_from_online(online: Optional[Dict[str, str]]) -> Optional[Dict[str, Any]]:
        if not online:
            return None
        marker_fields = (
            "__classifier_genre",
            "__classifier_confidence",
            "__classifier_reasoning",
            "__classifier_year",
            "__classifier_year_evidence",
            "__classifier_lastfm_tags",
        )
        if not any((online.get(k) or "").strip() for k in marker_fields):
            return None
        try:
            confidence = float((online.get("__classifier_confidence") or "0").strip() or 0.0)
        except Exception:
            confidence = 0.0
        return {
            "genre": (online.get("__classifier_genre") or "").strip(),
            "confidence": confidence,
            "reasoning": (online.get("__classifier_reasoning") or "").strip(),
            "source": (online.get("__classifier_source") or "nano+WS+LF").strip(),
            "lastfm_tags": (online.get("__classifier_lastfm_tags") or "").strip(),
            "year": (online.get("__classifier_year") or "").strip(),
            "year_evidence": (online.get("__classifier_year_evidence") or "").strip(),
        }

    _flush_status()
    print("ℹ enrich-online uses MusicBrainz, Last.fm and WS-based AI classification; Beatport/SoundCloud APIs are not used in this workflow.")
    status_doc["soundcloud"]["client_id_status"] = "unused"
    status_doc["soundcloud"]["decision"] = "skipped"
    status_doc["soundcloud"]["attempted_requests"] = 0
    _flush_status()

    for r in rows:
        if (r.get("disposition") or "").lower().strip() in EXPORT_DISPOSITIONS:
            continue
        p = Path(r.get("file_path",""))
        online = enrich_online_for_row(p, r)
        precomputed_cls = _classifier_from_online(online)
        if not online:
            processed += 1
            status_doc["rows_processed"] = processed
            status_doc["updated"] = changed
            status_doc["last_file"] = str(p)
            _flush_status()
            continue
        # reguła nadpisywania:
        # - zawsze nadpisuj, jeśli źródłem jest AcoustID (najwyższy priorytet)
        # - w innym przypadku: wypełnij jeśli puste LUB nadpisz fallback (filename|tags_fallback)
        current_source = (r.get("meta_source") or "").strip().lower()
        online_source = (online.get("meta_source") or "").strip().lower()
        acoustid_wins = online_source.startswith("acoustid")
        allow_override = acoustid_wins or (
            current_source in {"filename|tags_fallback", "filename,tags_fallback", "tags_fallback"}
        ) or not r.get("genre_suggest")  # nadpisz jeśli genre pusty
        any_change = False
        for k, v in online.items():
            if k in {"artist_suggest","title_suggest","version_suggest","genre_suggest","album_suggest","release_group_id","year_suggest","duration_suggest",
                     "recording_mbid","original_album_title","original_release_date","original_release_year","original_release_mbid",
                     "original_release_group_mbid","original_release_category","original_release_source",
                     "archive_org_identifier","archive_org_cover_url"}:
                cur = (r.get(k) or "").strip()
                if (not cur and v) or (allow_override and v and cur != v):
                    r[k] = v
                    any_change = True
        # ustaw meta_source jeśli zrobiliśmy jakąkolwiek aktualizację i online podał źródło
        if any_change and (online.get("meta_source") or "").strip():
            r["meta_source"] = online["meta_source"]
        
    # Always try to enrich genres using the production WS-based classifier.
        try:
            a = (r.get("artist_suggest") or r.get("artist") or "").strip()
            t = (r.get("title_suggest") or r.get("title") or "").strip()
            version_from_cols = (r.get("version_info") or "").strip()
            if not version_from_cols:
                _, parsed = split_title_and_version(r.get("title") or "")
                version_from_cols = parsed
            v = (
                r.get("version_suggest")
                or version_from_cols
                or r.get("parsed_version")
                or ""
            ).strip()
            dur_s = None
            if r.get("duration_suggest"):
                try:
                    dur_parts = r["duration_suggest"].split(":")
                    if len(dur_parts) == 2:
                        dur_s = int(dur_parts[0]) * 60 + int(dur_parts[1])
                except Exception:
                    pass
            
            cls = precomputed_cls
            if cls is None:
                from djlib.metadata.genre_classifier import classify_genre, ClassifierError
                print(f"   🎵 Classifying genre (nano+WS+LF) for: {a} - {t}")
                bpm_str = (r.get("bpm") or r.get("tag_bpm_original") or "").strip()
                key_str = (r.get("key_camelot") or r.get("tag_key_original") or "").strip()
                filename_hint = Path(r.get("file_path", "")).name if r.get("file_path") else ""
                try:
                    cls = classify_genre(
                        artist=a, title=t, version=v,
                        bpm=bpm_str, key=key_str, filename=filename_hint,
                    )
                except ClassifierError as e:
                    print(f"      ❌ Classification failed (after retry): {e}")
                    r["ai_genre"] = ""
                    r["ai_confidence"] = ""
                    r["ai_reasoning"] = f"ERROR: {str(e)[:200]}"
                    r["ai_classify_date"] = _now_iso()
                    genre_res = None
                    cls = None
            else:
                print(f"   🎵 Reusing genre classification (nano+WS+LF) for: {a} - {t}")

            if cls is not None:
                r["ai_genre"] = cls["genre"]
                r["ai_confidence"] = f"{cls['confidence']:.2f}"
                r["ai_reasoning"] = cls["reasoning"][:500]
                r["ai_classify_date"] = _now_iso()
                cls_year = (cls.get("year") or "").strip()
                if cls_year and not (r.get("year_suggest") or "").strip():
                    r["year_suggest"] = cls_year
                    any_change = True
                if cls.get("lastfm_tags"):
                    lf_top = [name.strip() for name in re.split(r',\s*', cls["lastfm_tags"]) if name.strip()][:5]
                    r["genres_lastfm"] = ", ".join(s.split(" (")[0] for s in lf_top)
                    lfm_set += 1
                # Shim to keep the downstream code (expects genre_res with .main/.confidence) working
                class _GRes:
                    def __init__(self, g, c):
                        self.main = g
                        self.subs = []
                        self.confidence = c
                        self.breakdown = []
                genre_res = _GRes(cls["genre"], cls["confidence"])
                print(f"      Result: {cls['genre']} (conf={cls['confidence']:.2f})")

            if genre_res and genre_res.confidence >= 0.03:  # lower threshold for missing genres
                # Ustaw gatunek (main z klasyfikatora — subs puste w nowym modelu)
                genres = [genre_res.main] + genre_res.subs[:2]
                genre_str = ", ".join(genres)
                current_genre = (r.get("genre_suggest") or "").strip()
                # Treat noise-only genres as empty (e.g., "puerto rico, merge" from Last.fm artist tags)
                noise_terms = {"puerto rico", "merge", "various", "compilation"}
                current_parts = [p.strip().lower() for p in current_genre.split(",")]
                is_noise_only = current_genre and all(p in noise_terms for p in current_parts if p)
                # Override existing genre if: force_genres flag, or no current genre, or noise-only, or significantly better confidence
                if force_genres or not current_genre or is_noise_only or genre_res.confidence > 0.08:
                    r["genre_suggest"] = genre_str
                    any_change = True
                    # Update meta_source: classifier has no breakdown → use its source label.
                    sources = [s.source for s in genre_res.breakdown]
                    if sources:
                        r["meta_source"] = f"{r.get('meta_source', '')}+genres({','.join(sources)})".strip("+")
                    else:
                        tag = "ai_classifier(nano+WS+LF)"
                        r["meta_source"] = f"{r.get('meta_source', '')}+{tag}".strip("+")

                # Zapisz surowe listy tagów per źródło do dodatkowych kolumn
                try:
                    src_map = {s.source: s.tags for s in genre_res.breakdown}
                    def _top_k(d, k=5):
                        return ", ".join([kv[0] for kv in sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:k]])
                    if src_map.get("beatport") and (force_genres or not (r.get("genres_beatport") or "")):
                        r["genres_beatport"] = _top_k(src_map["beatport"])  # type: ignore[index]
                        any_change = True
                    if src_map.get("musicbrainz") and (force_genres or not (r.get("genres_musicbrainz") or "")):
                        r["genres_musicbrainz"] = _top_k(src_map["musicbrainz"])  # type: ignore[index]
                        any_change = True
                        mb_set += 1
                    if src_map.get("lastfm") and (force_genres or not (r.get("genres_lastfm") or "")):
                        r["genres_lastfm"] = _top_k(src_map["lastfm"])  # type: ignore[index]
                        any_change = True
                        lfm_set += 1
                    if src_map.get("soundcloud") and (force_genres or not (r.get("genres_soundcloud") or "")):
                        r["genres_soundcloud"] = _top_k(src_map["soundcloud"])  # type: ignore[index]
                        any_change = True
                except Exception:
                    pass
            elif not genre_res and force_genres:
                # Classifier produced no usable genre. Fall back to the original
                # file tag so the row is not left with stale data from an older run.
                original_tag_genre = (r.get("tag_genre_original") or "").strip()
                if original_tag_genre:
                    r["genre_suggest"] = original_tag_genre.lower()
                    r["meta_source"] = "filename|tags_fallback"
                    # Clear per-source columns to avoid stale data
                    for col in ("genres_soundcloud", "genres_beatport", "genres_lastfm", "genres_musicbrainz"):
                        if r.get(col):
                            r[col] = None
                    any_change = True
                    print(f"      ⚠️  No online data — falling back to original tag: {original_tag_genre}")
        except Exception as e:
            # Debug: print exception for troubleshooting
            print(f"Genre resolution failed for {a} - {t}: {e}")
            pass

        # Popularność z Last.fm (playcount/listeners) — pomoc dla singalong/party dance/decades
        try:
            a = (r.get("artist_suggest") or r.get("artist") or "").strip()
            t = (r.get("title_suggest") or r.get("title") or "").strip()
            if a and t:
                from djlib.metadata.lastfm import track_info as lf_track_info
                info = lf_track_info(a, t) or {}
                if info:
                    # Zapisz pola popularności, nie nadpisuj istniejących >0
                    if info.get("playcount") and int(info.get("playcount", 0)) > int(r.get("pop_playcount", 0) or 0):
                        r["pop_playcount"] = str(info["playcount"])  # zapis w CSV jako string
                    if info.get("listeners") and int(info.get("listeners", 0)) > int(r.get("pop_listeners", 0) or 0):
                        r["pop_listeners"] = str(info["listeners"])  # zapis w CSV jako string
        except Exception:
            pass

        if any_change:
            changed += 1
        
        # Cover art fetching removed - we use standard DJ Library cover embedded during apply
        # See djlib/legacy/coverart_fetch.py for the original implementation
        
        # Auto-fill artist/title if still empty and we now have suggest values (quality-of-life)
        if not (r.get("artist") or "").strip() and (r.get("artist_suggest") or "").strip():
            r["artist"] = r["artist_suggest"]
        if not (r.get("title") or "").strip() and (r.get("title_suggest") or "").strip():
            r["title"] = r["title_suggest"]
        
        # Always copy year_suggest to year (overwrite if year_suggest has value)
        year_suggest = (r.get("year_suggest") or "").strip()
        if year_suggest:
            current_year = (r.get("year") or "").strip()
            if current_year != year_suggest:
                r["year"] = year_suggest
                any_change = True
        
        # Always copy version_suggest to version_info (overwrite if version_suggest has value)
        # Critical for live recordings to show "(Live)" in filenames and tags
        version_suggest = (r.get("version_suggest") or "").strip()
        if version_suggest:
            current_version = (r.get("version_info") or "").strip()
            if current_version != version_suggest:
                r["version_info"] = version_suggest
                any_change = True
        
        processed += 1
        status_doc["rows_processed"] = processed
        status_doc["updated"] = changed
        status_doc["last_file"] = str(p)
        _flush_status()
    
    # Auto-map genre_suggest -> genre using genres.yml
    from djlib.genre_mapper import map_genre
    genre_mapped = 0
    genre_unmapped = []
    for r in rows:
        genre_suggest = (r.get("genre_suggest") or "").strip()
        if not genre_suggest:
            continue
        
        current_genre = (r.get("genre") or "").strip()
        mapped_genre = map_genre(genre_suggest)
        
        if mapped_genre:
            # Override genre if: force_genres flag, or no current genre, or mapped differs from current
            should_override = force_genres or not current_genre or current_genre != mapped_genre
            if should_override:
                r["genre"] = mapped_genre
                r["genre_mapping_status"] = "OK"
                genre_mapped += 1
                changed += 1  # Count genre mapping as change
            else:
                r["genre_mapping_status"] = "OK"
        else:
            # Mark as UNMAPPED for reporting
            r["genre_mapping_status"] = "UNMAPPED"
            file_path = r.get("file_path", "")
            genre_unmapped.append((file_path, genre_suggest))
    
    # Always save — recalculates final_filename from current artist/title/version/key/bpm
    # (picks up manual edits even when enrich itself changed nothing)
    _save_unsorted(rows)
    # Oblicz źródła użycia na podstawie wypełnionych kolumn per-source
    mb_cnt = lfm_cnt = sc_cnt = 0
    for r in rows:
        if r.get("genres_musicbrainz"):
            mb_cnt += 1
        if r.get("genres_lastfm"):
            lfm_cnt += 1
        if r.get("genres_soundcloud"):
            sc_cnt += 1
    status_doc["sources_counts"] = {
        "musicbrainz": mb_cnt,
        "lastfm": lfm_cnt,
        "soundcloud": sc_cnt,
    }
    status_doc["soundcloud"]["attempted_requests"] = 0
    status_doc["rows_processed"] = processed
    status_doc["updated"] = changed
    status_doc["state"] = "done"
    status_doc["completed_at"] = _now_iso()
    _flush_status()
    print(f"🔎 Enrich online: updated={changed}")
    # Short diagnostics
    if total:
        print(f"   → genres set — MB:{mb_set}, LFM:{lfm_set}")
    if genre_mapped > 0:
        print(f"🎵 Genre auto-mapped: {genre_mapped} tracks")
    if genre_unmapped:
        print(f"⚠️  Genre unmapped ({len(genre_unmapped)} tracks) - need synonym mapping:")
        # Group by genre_suggest to show unique unmapped genres
        from collections import Counter
        unmapped_genres = Counter([g for _, g in genre_unmapped])
        for genre, count in unmapped_genres.most_common():
            print(f"     • '{genre}' ({count} tracks)")
        print(f"   📋 Files with unmapped genres:")
        for file_path, genre_suggest in genre_unmapped[:10]:  # Show first 10
            filename = Path(file_path).name if file_path else "?"
            print(f"      - {filename}: '{genre_suggest}'")
        if len(genre_unmapped) > 10:
            print(f"      ... and {len(genre_unmapped) - 10} more")
    # Cover art fetching disabled - summary removed
    # if fetch_covers:
    #     print(f"🎨 Okładki URL: found={covers_added}, failed={covers_failed}")
    if not _lfm_key_present:
        print("   ⚠ Brak LASTFM_API_KEY (DJLIB_LASTFM_API_KEY) — kolumna genres_lastfm może pozostać pusta.")

def cmd_fix_fingerprints(_: argparse.Namespace) -> None:
    """Uzupełnij brakujące fingerprinty w istniejącym CSV.
    Dla każdego wiersza bez fingerprintu spróbuj wyliczyć go z pliku (preferuj final_path, potem file_path).
    Aktualizuj też duration_suggest jeśli puste.
    Pisz postęp do LOGS/fingerprint_status.json, aby UI mogło pokazywać pasek.
    """
    from djlib.config import LOGS_DIR
    rows = _load_unsorted()
    targets = []
    for r in rows:
        if (r.get("disposition") or "").lower().strip() in EXPORT_DISPOSITIONS:
            continue
        fp = (r.get("fingerprint") or "").strip()
        if fp:
            continue
        # preferuj final_path jeśli istnieje, w przeciwnym razie file_path
        p = None
        fp1 = r.get("final_path") or ""
        fp2 = r.get("file_path") or ""
        f1 = Path(fp1) if fp1 else None
        f2 = Path(fp2) if fp2 else None
        if f1 and f1.exists():
            p = f1
        elif f2 and f2.exists():
            p = f2
        if p is not None:
            targets.append((r, p))

    total = len(targets)
    processed = 0
    updated = 0
    errors = 0

    status_path = LOGS_DIR / "fingerprint_status.json"

    def _write_status(state: str, last_file: str = "") -> None:
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            with status_path.open("w", encoding="utf-8") as f:
                json.dump({
                    "state": state,
                    "total": total,
                    "processed": processed,
                    "updated": updated,
                    "errors": errors,
                    "last_file": last_file,
                }, f, ensure_ascii=False)
        except Exception:
            pass

    _write_status("running", "")

    for r, p in targets:
        try:
            dur, fp = fingerprint_info(p)
            if fp:
                r["fingerprint"] = fp
                # uzupełnij duration_suggest jeśli brak
                ds = (r.get("duration_suggest") or "").strip()
                if not ds and dur:
                    mm, ss = divmod(int(dur), 60)
                    r["duration_suggest"] = f"{mm}:{ss:02d}"
                updated += 1
        except Exception:
            errors += 1
        processed += 1
        _write_status("running", str(p))

    if updated:
        _save_unsorted(rows)
    _write_status("done", "")
    print(f"🧩 Fix fingerprints: updated={updated}, errors={errors}")

def cmd_fix_titles_from_filenames(_: argparse.Namespace) -> None:
    """Napraw rekordy z pustym/niewłaściwym artist/title korzystając z nazwy pliku."""
    from djlib.filename import parse_from_filename
    rows = _load_unsorted()
    if not rows:
        print("Brak rekordów do korekty.")
        return

    def _should_replace(current: str | None, base_tokens: set[str]) -> bool:
        v = (current or "").strip()
        if not v:
            return True
        low = v.lower()
        if low.isdigit():
            return True
        if low.startswith("track") and len(low.split()) <= 2:
            return True
        if base_tokens:
            cur_tokens = {tok for tok in low.split() if len(tok) > 1}
            if not cur_tokens:
                return True
            # jeśli brak wspólnych tokenów z nazwą pliku – traktuj jako błędne
            if cur_tokens.isdisjoint(base_tokens):
                return True
        return False

    updated = 0
    for r in rows:
        if (r.get("disposition") or "").lower().strip() in EXPORT_DISPOSITIONS:
            continue
        fp = (r.get("file_path") or "").strip()
        if not fp:
            continue
        p = Path(fp)
        a, t, v = parse_from_filename(p)
        if not a and not t:
            continue
        changed = False
        base_artist_tokens = {tok for tok in a.lower().split() if len(tok) > 1}
        base_title_tokens = {tok for tok in t.lower().split() if len(tok) > 1}
        if _should_replace(r.get("artist"), base_artist_tokens) and a:
            r["artist"] = a
            changed = True
        if _should_replace(r.get("title"), base_title_tokens) and t:
            r["title"] = t
            changed = True
        if _should_replace(r.get("artist_suggest"), base_artist_tokens) and a:
            r["artist_suggest"] = a
            changed = True
        if _should_replace(r.get("title_suggest"), base_title_tokens) and t:
            r["title_suggest"] = t
            changed = True
        if not (r.get("version_info") or "").strip() and v:
            r["version_info"] = v
            changed = True
        if not (r.get("version_suggest") or "").strip() and v:
            r["version_suggest"] = v
            changed = True
        if changed:
            meta = (r.get("meta_source") or "").strip()
            if "fix_filename" not in meta:
                r["meta_source"] = (meta + "+fix_filename").strip("+")
            updated += 1

    if updated:
        _save_unsorted(rows)
    print(f"🛠️  Fix titles from filenames: updated={updated}")

def cmd_apply(args: argparse.Namespace) -> None:
    """Apply approved changes from unsorted.csv.

    Dispositions: library (→ LIB_ROOT/Artist/), reject (→ REJECT_ROOT/),
    mixes (→ MIXES_ROOT/). Legacy fallback: target_subfolder if disposition empty.
    """
    from djlib.logistics import build_library_path, build_reject_path, build_mixes_path
    
    # Check if Rekordbox is running before starting
    try:
        from pyrekordbox.utils import get_rekordbox_pid
        pid = get_rekordbox_pid()
        if pid:
            print(f"\n⚠️  WARNING: Rekordbox is currently running (PID {pid})")
            print("    Cover art will NOT be updated in Rekordbox database.")
            print("    Please close Rekordbox and re-run apply for automatic cover art sync.")
            print("    (You can continue, but will need to manually 'Reload Tags' in Rekordbox)\n")
    except ImportError:
        pass  # pyrekordbox not available
    
    rows = _load_unsorted()
    ready = [r for r in rows if (r.get("disposition") or "").lower().strip() in EXPORT_DISPOSITIONS]
    if not ready:
        print("Brak wierszy z ustawionym disposition (library/reject/mixes).")
        return
    library_rows = load_records(CSV_PATH)
    rejected_registry = load_rejected(REJECTED_CSV_PATH)  # Load rejected registry for appending
    processed_ids: set[str] = set()
    # Track DJ software IDs to remove for rejected files
    rejected_rekordbox_ids: List[str] = []
    rejected_traktor_ids: List[str] = []
    tags_written = 0
    tags_errors = 0
    tags_cleaned = 0
    tags_clean_errors = 0
    covers_applied = 0
    covers_skipped = 0
    covers_failed = 0

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = LOGS_DIR / f"moves-{stamp}.csv"
    log_rows = []
    
    # Get current DJ software mappings for ID validation
    from djlib.external_sync import get_rekordbox_track_ids, get_traktor_track_ids
    rekordbox_mapping = get_rekordbox_track_ids()
    traktor_mapping = get_traktor_track_ids()
    
    # Build library index for duplicate detection when exporting to library
    # OPTIMIZATION: Only build index if there are files destined for "library"
    from djlib.dedup import get_audio_info, normalize_for_match, format_quality, format_duration
    from djlib.config import load_config
    _cfg = load_config()
    _library_path = Path(_cfg.get("LIB_ROOT", "")).expanduser()
    library_index: Dict[str, Any] = {}  # match_key -> AudioInfo
    _library_index_loaded = False
    _audio_exts = {'.mp3', '.flac', '.wav', '.aiff', '.aif', '.m4a', '.ogg', '.opus'}

    def _load_library_index():
        """Lazy-load library index for duplicate detection."""
        nonlocal _library_index_loaded
        if _library_index_loaded:
            return
        _library_index_loaded = True
        if _library_path and _library_path.exists():
            for _path in _library_path.rglob("*"):
                if _path.suffix.lower() in _audio_exts:
                    _info = get_audio_info(_path)
                    if _info and _info.artist and _info.title:
                        library_index[_info.match_key] = _info

    # ── Pre-flight: validate staging for duplicate entries ────────────
    _seen_hashes: Dict[str, str] = {}  # hash -> file_path (first occurrence)
    _seen_paths: set[str] = set()
    _batch_dupes: list[str] = []
    for _r in ready:
        _fp = _r.get("file_path", "")
        _fh = _r.get("file_hash", "")
        if _fp and _fp in _seen_paths:
            _batch_dupes.append(f"  DUPE PATH: {_fp}")
        _seen_paths.add(_fp)
        if _fh and _fh in _seen_hashes:
            _batch_dupes.append(f"  DUPE HASH: {_fp}  (same hash as {_seen_hashes[_fh]})")
        if _fh:
            _seen_hashes[_fh] = _fp
    if _batch_dupes:
        print(f"\n⚠️  WARNING: Found {len(_batch_dupes)} duplicate entries in unsorted.csv:")
        for _d in _batch_dupes:
            print(_d)
        print("   These will be handled during export (only first occurrence exported).\n")

    # Track match_keys seen in THIS batch to catch intra-batch duplicates
    _batch_match_keys: Dict[str, str] = {}  # match_key -> file_path (first in batch)
    skipped_reasons: Dict[str, int] = {}  # reason -> count

    def _skip(reason: str, detail: str) -> None:
        """Log a skip with structured reason."""
        skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
        print(f"[SKIP:{reason}] {detail}")

    for r in ready:
        # Determine destination path (new model or legacy fallback)
        destination = (r.get("disposition") or "").lower().strip()
        target_subfolder = (r.get("target_subfolder") or "").strip()
        
        src = Path(r.get("file_path") or "")
        if not src.exists():
            _skip("FILE_MISSING", str(src))
            continue
        
        # Normalize path to NFC for consistent matching (macOS uses NFD, Rekordbox uses NFC)
        src_nfc = Path(unicodedata.normalize('NFC', str(src)))
        
        # Skip DJ software ID validation for rejected files
        # Rejected files are just moved to reject folder, no DJ software sync needed
        if destination != "reject":
            # ✅ VALIDATE & FIX DJ software IDs before moving
            current_rekordbox_id = rekordbox_mapping.get(src_nfc, '')
            current_traktor_id = traktor_mapping.get(src_nfc, '')
            
            # Also check file tags for rekordbox_id
            file_rekordbox_id = r.get("rekordbox_id", "")
            if not file_rekordbox_id:
                try:
                    from djlib.djlib_tags import read_djlib_tags
                    djlib_tags = read_djlib_tags(src)
                    file_rekordbox_id = djlib_tags.get("rekordbox_id", "")
                except Exception:
                    pass
            
            # Block export if no rekordbox_id found anywhere — unless the
            # DJ explicitly opted in to the tag-only workflow via
            # `--allow-no-rekordbox`. In that mode the track enters library
            # with `analysis_source=tags` so consumers (REVIEW UI, ML export)
            # know BPM/Key came from the audio file, not Rekordbox analysis.
            final_rekordbox_id = current_rekordbox_id or file_rekordbox_id
            if not final_rekordbox_id:
                if not getattr(args, "allow_no_rekordbox", False):
                    _skip(
                        "NO_REKORDBOX_ID",
                        f"{src.name}  → Uruchom 'sync-dj-libraries --write' aby przypisać ID, lub użyj 'apply --allow-no-rekordbox'",
                    )
                    continue
                print(
                    f"   ⚠️  No Rekordbox ID for {src.name} — applying with "
                    f"analysis_source=tags (not yet analyzed by Rekordbox)"
                )
            
            if current_rekordbox_id and current_rekordbox_id != r.get("rekordbox_id", ""):
                print(f"   🔧 Updating Rekordbox ID for {src.name}: {r.get('rekordbox_id', '')} → {current_rekordbox_id}")
                r["rekordbox_id"] = current_rekordbox_id
                # Update tag in file
                try:
                    from djlib.djlib_tags import write_djlib_tags
                    write_djlib_tags(
                        src,
                        track_id=r.get("track_id", ""),
                        rekordbox_id=current_rekordbox_id,
                        traktor_id=r.get("traktor_id") or None,
                        original_path=str(src)
                    )
                except Exception as e:
                    print(f"⚠️  Could not update tags for {src.name}: {e}")
            
            if current_traktor_id and current_traktor_id != r.get("traktor_id", ""):
                print(f"   🔧 Updating Traktor ID for {src.name}: {r.get('traktor_id', '')} → {current_traktor_id}")
                r["traktor_id"] = current_traktor_id
                # Update tag in file
                try:
                    from djlib.djlib_tags import write_djlib_tags
                    write_djlib_tags(
                        src,
                        track_id=r.get("track_id", ""),
                        rekordbox_id=r.get("rekordbox_id") or None,
                        traktor_id=current_traktor_id,
                        original_path=str(src)
                    )
                except Exception as e:
                    print(f"⚠️  Could not update tags for {src.name}: {e}")

        # Build final filename
        title_candidate = r.get("title") or r.get("tag_title_original") or r.get("title_suggest") or ""
        version_pref = (
            r.get("version_info")
            or r.get("version_suggest")
            or ""
        )
        title_base, title_version = split_title_and_version(title_candidate)
        if title_version and not version_pref:
            version_pref = title_version
        final_title = title_base or title_candidate
        if not version_pref:
            _, parsed_version = split_title_and_version(final_title)
            if parsed_version:
                version_pref = parsed_version
        final_name = build_final_filename(
            r.get("artist") or r.get("tag_artist_original") or r.get("artist_suggest") or "",
            final_title,
            version_pref,
            r.get("key_camelot", ""),
            r.get("bpm", ""),
            extension_for(src),
        )
        
        artist = r.get("artist") or r.get("tag_artist_original") or r.get("artist_suggest") or ""
        title = r.get("title") or r.get("tag_title_original") or r.get("title_suggest") or ""
        version = r.get("version_info") or r.get("version_suggest") or ""

        # Check for duplicates in library when exporting
        if destination == "library":
            _load_library_index()  # Lazy-load on first library export
            # Include version_info in the match key so Original Mix and Extended Mix
            # are not treated as the same track within a single export batch.
            match_key = normalize_for_match(artist, f"{title} {version}".strip())
            
            # ── Intra-batch duplicate check ──
            if match_key in _batch_match_keys:
                _skip("DUPLICATE_IN_BATCH",
                      f"{artist} - {title}  (already in this export batch from {_batch_match_keys[match_key]})")
                continue
            
            # ── Library duplicate check ──
            if match_key in library_index:
                existing = library_index[match_key]
                src_info = get_audio_info(src)
                
                print(f"\n⚠️  DUPLICATE DETECTED for: {artist} - {title}")
                print(f"   EXISTING in library:")
                print(f"      {format_quality(existing)}, {format_duration(existing.duration)}")
                print(f"      {existing.path}")
                if src_info:
                    print(f"   NEW (exporting):")
                    print(f"      {format_quality(src_info)}, {format_duration(src_info.duration)}")
                    print(f"      {src}")
                    
                    # Quality comparison
                    if src_info.quality_score > existing.quality_score:
                        print(f"   📈 NEW is BETTER quality ({src_info.quality_score} vs {existing.quality_score})")
                    elif src_info.quality_score < existing.quality_score:
                        print(f"   📉 EXISTING is BETTER quality ({existing.quality_score} vs {src_info.quality_score})")
                    else:
                        print(f"   ⚖️  Same quality score ({src_info.quality_score})")
                    
                    # Duration check
                    dur_diff = abs(src_info.duration - existing.duration)
                    if dur_diff > 3:
                        print(f"   ⏱️  Duration differs by {dur_diff:.0f}s - might be different edits!")
                
                choice = input("   Continue anyway? [y/N]: ").strip().lower()
                if choice != 'y':
                    _skip("ALREADY_IN_LIBRARY", f"{artist} - {title}")
                    continue
        
        # Determine destination path
        dest_path: Path | None = None
        
        if destination == "library":
            dest_path = build_library_path(artist, final_name)
        elif destination == "reject":
            dest_path = build_reject_path(final_name)
        elif destination == "mixes":
            # DJ mixes: flat structure, no artist folders
            dest_path = build_mixes_path(final_name)
        elif target_subfolder:
            # Legacy fallback: use target_subfolder if destination is empty
            dest_dir = resolve_target_path(target_subfolder)
            if dest_dir:
                dest_path = dest_dir / final_name
        
        if not dest_path:
            print(f"[WARN] Brak destination/target_subfolder dla {src.name}")
            continue

        print(f"{'DRY-RUN ' if args.dry_run else ''}MOVE: {src} -> {dest_path}")

        if args.dry_run:
            continue

        # Ensure parent directory exists
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        
        # ── Handle file-already-exists at destination ──
        if dest_path.exists():
            if destination in ("library", "mixes"):
                # NEVER silently create (2) copies in library/mixes
                # This means a duplicate slipped past the artist+title check
                # (e.g. same file re-exported, or metadata-level dedup missed it)
                from djlib.fingerprint import file_sha256 as _fsha
                existing_hash = _fsha(dest_path)
                new_hash = r.get("file_hash") or _fsha(src)
                
                if existing_hash == new_hash:
                    _skip("IDENTICAL_FILE_EXISTS",
                          f"{dest_path.name}  (exact same file already at destination)")
                    # File already at destination from a previous interrupted run —
                    # clean up the source and mark as processed so the row is removed.
                    try:
                        src.unlink()
                    except OSError:
                        pass
                    processed_ids.add(r.get("track_id", ""))
                    continue
                
                # Different file, same name — ask user
                print(f"\n⚠️  FILE CONFLICT at destination:")
                print(f"   Target: {dest_path}")
                print(f"   A file with this name already exists (different content).")
                print(f"   Source hash:   {new_hash[:16]}...")
                print(f"   Existing hash: {existing_hash[:16]}...")
                conflict_choice = input("   [S]kip / [R]ename with (2) / [O]verwrite? [S/r/o]: ").strip().lower()
                
                if conflict_choice == 'o':
                    dest_path.unlink()
                    print(f"   → Overwriting existing file")
                elif conflict_choice == 'r':
                    stem = dest_path.stem
                    ext = dest_path.suffix
                    i = 2
                    while True:
                        cand = dest_path.parent / f"{stem} ({i}){ext}"
                        if not cand.exists():
                            dest_path = cand
                            break
                        i += 1
                    print(f"   → Renamed to: {dest_path.name}")
                else:
                    _skip("FILE_CONFLICT", f"{dest_path.name}  (user chose to skip)")
                    continue
            else:
                # For reject destination: silent (2) rename is OK
                stem = dest_path.stem
                ext = dest_path.suffix
                i = 2
                while True:
                    cand = dest_path.parent / f"{stem} ({i}){ext}"
                    if not cand.exists():
                        dest_path = cand
                        break
                    i += 1
        
        shutil.move(str(src), str(dest_path))
        log_rows.append([str(src), str(dest_path), r.get("track_id", "")])
        processed_ids.add(r.get("track_id", ""))

        # ── Merge cue points from acoustic duplicates into winner ──────────────
        # Only for library destination; winner's pre-move path (str(src)) is still
        # in the Rekordbox/Traktor DB at this point.
        if destination == "library":
            from djlib.cue_merge import parse_duplicate_paths, merge_and_remove_duplicate
            _dup_paths = parse_duplicate_paths(r.get("duplicate_paths") or "")
            if _dup_paths:
                _rb_running = False
                try:
                    from pyrekordbox.utils import get_rekordbox_pid
                    _rb_running = bool(get_rekordbox_pid())
                except Exception:
                    pass
                if _rb_running:
                    print(f"   ⚠️  Rekordbox is running — cue merge skipped for duplicates. "
                          f"Close Rekordbox and re-run apply to merge.")
                else:
                    _tk_path = _get_traktor_collection_path()
                    for _dup in _dup_paths:
                        print(f"   🔀 Merging cues from duplicate: {Path(_dup).name}")
                        try:
                            _mr = merge_and_remove_duplicate(
                                winner_path=str(src),
                                dup_path=_dup,
                                traktor_collection_path=_tk_path,
                                delete_file=True,
                            )
                            for _err in _mr.get("errors", []):
                                print(f"   ⚠️  {_err}")
                            _flags = [
                                f"rb={'✓' if _mr['rb_merged'] else '✗'}",
                                f"tk={'✓' if _mr['tk_merged'] else '✗'}",
                                f"del={'✓' if _mr['file_deleted'] else '✗'}",
                            ]
                            print(f"   ✅ Cue merge: {' '.join(_flags)}")
                        except Exception as _e:
                            print(f"   ⚠️  Cue merge failed for {Path(_dup).name}: {_e} — continuing")

        # ── Update library_index so next files in this batch are caught ──
        if destination == "library":
            _mk = normalize_for_match(artist, f"{title} {version}".strip())
            new_info = get_audio_info(dest_path)
            if new_info:
                library_index[_mk] = new_info
            _batch_match_keys[_mk] = src.name
        
        # Track rejected files for DJ software removal
        if destination == "reject":
            if r.get("rekordbox_id"):
                rejected_rekordbox_ids.append(str(r.get("rekordbox_id") or ""))
                print(f"   📌 Will remove from Rekordbox: {r.get('rekordbox_id')}")
            if r.get("traktor_id"):
                rejected_traktor_ids.append(str(r.get("traktor_id") or ""))
                print(f"   📌 Will remove from Traktor: {r.get('traktor_id')}")
            
            # ── Save to rejected registry (so cmd_scan skips these in the future) ──
            rejected_registry.append({
                "file_hash": r.get("file_hash") or "",
                "fingerprint": r.get("fingerprint") or "",
                "artist": artist,
                "title": title,
                "original_path": r.get("file_path") or "",
                "reject_date": utc_now_str(),
                "reason": "user_reject",
            })
            
            print(f"   🚫 Rejected: {dest_path.name} (moved to reject folder, no further processing)")
            continue
        
        # Provenance of BPM+Key. Consumers (REVIEW UI, ML export) use this to
        # tell an analyzed track from a tag-only import. Rekordbox wins when
        # its ID is present (DJ software re-analyzed the file); Traktor is
        # next; `tags` covers the `--allow-no-rekordbox` path where BPM/Key
        # come from ID3. Empty string means "unknown provenance" (no DJ
        # software ID AND no BPM/Key in tags — defensive default).
        has_rb = bool(r.get("rekordbox_id"))
        has_tr = bool(r.get("traktor_id"))
        has_tag_analysis = bool(r.get("bpm")) and bool(
            r.get("key_camelot") or r.get("key")
        )
        if has_rb:
            analysis_source = "rekordbox"
        elif has_tr:
            analysis_source = "traktor"
        elif has_tag_analysis:
            analysis_source = "tags"
        else:
            analysis_source = ""

        # Update library.csv with final record metadata
        record = {
            "track_id": r.get("track_id", ""),
            "file_path": str(dest_path),
            "original_path": r.get("file_path") or "",
            "file_hash": r.get("file_hash") or "",
            "fingerprint": r.get("fingerprint") or "",
            "added_date": utc_now_str(),
            "final_filename": final_name,
            "final_path": str(dest_path),
            "artist": artist,
            "title": final_title,
            "version_info": version_pref,
            "genre": r.get("genre") or r.get("genre_suggest") or "",
            "bpm": r.get("bpm") or "",
            "key_camelot": r.get("key_camelot") or "",
            "analysis_source": analysis_source,
            "energy_hint": r.get("energy_hint") or "",
            "destination": destination or "library",  # Default to library if not specified
            "must_play": r.get("must_play") or "",
            "occasion_tags": r.get("occasion_tags") or "",
            "notes": r.get("notes") or "",
            "is_duplicate": r.get("is_duplicate") or "",
            "pop_playcount": r.get("pop_playcount") or "",
            "pop_listeners": r.get("pop_listeners") or "",
            # DJ software IDs (preserve for sync)
            "rekordbox_id": r.get("rekordbox_id") or "",
            "traktor_id": r.get("traktor_id") or "",
            # Legacy fields
            "target_subfolder": target_subfolder or "",
        }
        
        # Update library.csv record (library/mixes destinations)
        existing_idx = None
        for idx, lib_row in enumerate(library_rows):
            if lib_row.get("track_id") == record["track_id"]:
                existing_idx = idx
                break

        if existing_idx is not None:
            library_rows[existing_idx] = record
        else:
            library_rows.append(record)
        
        # Po udanym przeniesieniu wyczyść spam tagi i zapisz zaakceptowane metadane
        print(f"\n🔧 Processing tags for: {dest_path.name}")
        try:
            # Najpierw wyczyść spam tagi (musicdjs.club, chomikuj.pl, etc.)
            print(f"   Step 1: Cleaning spam tags...")
            result = clean_tags(dest_path, dry_run=False)
            if result and result.get("removed_tags"):
                tags_cleaned += 1
                print(f"   ✅ Cleaned {len(result['removed_tags'])} spam tags")
            
            # Step 1.5: Apply standard DJ Library cover art
            print(f"   Step 1.5: Applying DJ Library cover art...")
            try:
                from djlib.metadata.coverart import embed_cover_art_from_file
                
                # Cover file is in data/ folder relative to project root
                project_root = Path(__file__).parent.parent
                cover_file = project_root / "data" / "djlibrary-catalog.jpg"
                if cover_file.exists():
                    success = embed_cover_art_from_file(str(dest_path), str(cover_file))
                    if success:
                        covers_applied += 1
                        print(f"   ✅ DJ Library cover art applied")
                        
                        # Refresh cover art in DJ software caches
                        try:
                            from djlib.external_sync import (
                                refresh_rekordbox_cover_art,
                                refresh_traktor_cover_art,
                                PYREKORDBOX_AVAILABLE,
                                TRAKTOR_UTILS_AVAILABLE,
                            )
                            
                            if PYREKORDBOX_AVAILABLE:
                                if refresh_rekordbox_cover_art(dest_path):
                                    print(f"   ✅ Rekordbox cover art cache updated")
                            
                            if TRAKTOR_UTILS_AVAILABLE:
                                if refresh_traktor_cover_art(dest_path):
                                    print(f"   ✅ Traktor cover art cache updated")
                        except Exception as e:
                            print(f"   ⚠️  DJ software cover art sync: {e}")
                    else:
                        covers_failed += 1
                        print(f"   ⚠️  Failed to embed cover art")
                else:
                    covers_failed += 1
                    print(f"   ⚠️  Cover file not found: {cover_file}")
            except Exception as e:
                covers_failed += 1
                print(f"   ⚠️  Cover art error: {e}")
            
            # Teraz zapisz właściwe metadane
            print(f"   Step 2: Writing metadata tags...")
            updates = {}
            if artist:
                updates["artist"] = artist
            title_out = merge_title_and_version(record.get("title", ""), record.get("version_info", ""))
            if title_out:
                updates["title"] = title_out
            
            genre = (record["genre"] or "").strip()
            if genre:
                updates["genre"] = genre
            else:
                print(f"   ⚠️  No genre to write for {dest_path.name}")
                print(f"      record['genre'] = {repr(record.get('genre', 'MISSING'))}")
                print(f"      r['genre'] = {repr(r.get('genre', 'MISSING'))}")
                print(f"      r['genre_suggest'] = {repr(r.get('genre_suggest', 'MISSING'))}")
            
            bpm_raw = (record["bpm"] or "").strip()
            if bpm_raw:
                try:
                    bpm_val = int(round(float(bpm_raw)))
                    updates["bpm"] = str(bpm_val)
                except Exception:
                    updates["bpm"] = bpm_raw
            key_cam = (record["key_camelot"] or "").strip().upper()
            if key_cam:
                updates["key_camelot"] = key_cam
            
            # Zapisz rok z kolumny year (lub fallback na year_suggest)
            year_val = (r.get("year") or r.get("year_suggest") or "").strip()
            if year_val:
                updates["year"] = year_val
            else:
                print(f"   ⚠️  No year to write for {dest_path.name}")
                print(f"      r['year'] = {repr(r.get('year', 'MISSING'))}")
                print(f"      r['year_suggest'] = {repr(r.get('year_suggest', 'MISSING'))}")
            
            # Album intentionally left empty - user manages their own artwork/album organization
            updates["album"] = ""

            # occasion_tags → Grouping tag (TIT1/©grp) — readable in Rekordbox and Traktor
            occasion = (r.get("occasion_tags") or "").strip()
            if occasion:
                updates["grouping"] = occasion

            if updates:
                write_tags(dest_path, updates)
                tags_written += 1
                print(f"   ✅ Written tags to {dest_path.name}: {list(updates.keys())}")
        except Exception as e:
            print(f"[WARN] Tag write/clean failed for {dest_path}: {e}")
            tags_errors += 1
            if "clean_tags" in str(e):
                tags_clean_errors += 1

    if args.dry_run:
        print(f"[DRY-RUN] Gotowe do eksportu: {len(ready)} (disposition: library/reject/mixes).")
        return

    if log_rows:
        with log_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            # Updated headers for Phase 2 path mapping compatibility
            w.writerow(["src", "dest", "track_id"])
            w.writerows(log_rows)
        print(f"Zapisano log: {log_path}")
        print(f"💡 Tip: Use 'create-path-map --move-log {log_path}' to prepare for DJ software sync")

    remaining = [r for r in rows if r.get("track_id") not in processed_ids]
    _save_unsorted(remaining)
    save_records(CSV_PATH, library_rows)

    # Save rejected registry (always — append-only, even if no new rejects this run)
    _new_rejects = len([r for r in ready if (r.get("disposition") or "").lower().strip() == "reject" and r.get("track_id", "") in processed_ids])
    if _new_rejects > 0:
        save_rejected(REJECTED_CSV_PATH, rejected_registry)
        print(f"🚫 Zapisano {len(rejected_registry)} rekordów do library-rejected.csv (+{_new_rejects} nowych)")

    rejected_tracks = max(len(rejected_rekordbox_ids), len(rejected_traktor_ids),
                         len([r for r in ready if (r.get("disposition") or "").lower().strip() == "reject"]))
    library_added = len(processed_ids) - rejected_tracks
    print(f"Przeniesiono: {library_added} do biblioteki, {rejected_tracks} do odrzuconych.")
    print(f"🧹 Czyszczenie spam tagów: cleaned={tags_cleaned}, errors={tags_clean_errors}")
    print(f"🎨 Okładki: applied={covers_applied}, skipped={covers_skipped}, failed={covers_failed}")
    print(f"📀 Zapis tagów audio: ok={tags_written}, errors={tags_errors}")
    
    # ── Skip summary ──
    total_skipped = sum(skipped_reasons.values())
    if total_skipped > 0:
        print(f"\n⏭️  Skipped {total_skipped} files:")
        for reason, count in sorted(skipped_reasons.items()):
            print(f"   {reason}: {count}")
    
    # Remove rejected tracks from DJ software
    all_rekordbox_to_remove = rejected_rekordbox_ids
    all_traktor_to_remove = rejected_traktor_ids

    if not args.dry_run and (all_rekordbox_to_remove or all_traktor_to_remove):
        print()
        print("=" * 60)
        print("🗑️  REMOVING REJECTED FROM DJ SOFTWARE")
        print("=" * 60)
        print()
        
        try:
            from djlib.external_sync import remove_tracks_from_rekordbox, remove_tracks_from_traktor
            
            if all_rekordbox_to_remove:
                removed_rb = remove_tracks_from_rekordbox(all_rekordbox_to_remove, dry_run=False)
                if removed_rb > 0:
                    print(f"✅ Removed {removed_rb} tracks from Rekordbox")
            
            if all_traktor_to_remove:
                removed_tk = remove_tracks_from_traktor(all_traktor_to_remove, dry_run=False)
                if removed_tk > 0:
                    print(f"✅ Removed {removed_tk} tracks from Traktor")
        except Exception as e:
            print(f"⚠️  Error removing from DJ software: {e}")
    
    # Auto-sync with DJ software libraries (Rekordbox + Traktor) - only for library tracks
    if not args.dry_run and library_added > 0:
        print()
        print("=" * 60)
        print("🔄 SYNCING WITH DJ SOFTWARE LIBRARIES")
        print("=" * 60)
        print()
        
        try:
            from djlib.external_sync import sync_dj_libraries_after_export
            
            result = sync_dj_libraries_after_export(
                CSV_PATH,  # library.csv path
                dry_run=False
            )
            
            print()
            print("=" * 60)
            print("✅ DJ SOFTWARE SYNC COMPLETE")
            print("=" * 60)
            if result.get('rekordbox_added', 0) > 0:
                print(f"\n✅ Rekordbox: Added {result['rekordbox_added']} new tracks")
            if result.get('rekordbox_updated', 0) > 0:
                print(f"\n🔄 Rekordbox: Updated {result['rekordbox_updated']} track paths")
            if result.get('traktor_added', 0) > 0:
                print(f"\n✅ Traktor: Added {result['traktor_added']} new tracks")
            if result.get('traktor_updated', 0) > 0:
                print(f"\n🔄 Traktor: Updated {result['traktor_updated']} track paths")
            print()
        except Exception as e:
            print(f"\n⚠️  DJ software sync failed: {e}")
            print("   You can manually run: djlib add-to-traktor --collection <path>")
            print()

def scan_command() -> None:
    """Funkcja wywołująca skanowanie (używana przez webapp i inne moduły)."""
    args = argparse.Namespace()
    cmd_scan(args)

def cmd_undo(_: argparse.Namespace) -> None:
    logs = sorted(LOGS_DIR.glob("moves-*.csv"))
    if not logs:
        print("Brak logów do cofnięcia.")
        return
    log = logs[-1]
    print(f"Cofam ruchy z: {log.name}")

    rows = list(csv.DictReader(log.open("r", encoding="utf-8")))
    reverted = 0
    for r in rows:
        # Support both old (src_before/dest_after) and new (src/dest) column names
        src_before = Path(r.get("src_before") or r.get("src") or "")
        dest_after = Path(r.get("dest_after") or r.get("dest") or "")
        
        if not src_before or not dest_after:
            continue
            
        if dest_after.exists():
            dest_after.rename(src_before)
            reverted += 1
        else:
            print(f"[WARN] Brak pliku do cofnięcia: {dest_after}")
    print(f"Cofnięto {reverted} ruchów.")

def cmd_dupes(_: argparse.Namespace) -> None:
    groups: dict[str, list[dict[str,str]]] = {}
    rows = load_records(CSV_PATH)
    for r in rows:
        fp = (r.get("fingerprint") or "").strip()
        if not fp:
            continue
        groups.setdefault(fp, []).append(r)

    out = LOGS_DIR / "dupes.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["group_fingerprint", "track_id", "artist", "title", "file_path", "final_path", "file_hash"])
        for fp, items in groups.items():
            if len(items) <= 1:
                continue
            for r in items:
                w.writerow([fp, r.get("track_id",""), r.get("artist",""), r.get("title",""),
                            r.get("file_path",""), r.get("final_path",""), r.get("file_hash","")])
    print(f"Zapisano raport duplikatów: {out}")


def cmd_refresh_staging(args: argparse.Namespace) -> None:
    """Re-read unsorted.csv, recalculate final_filename for every row, and save.
    Useful after manual edits (title, artist, version_info, key, bpm) in the review UI."""
    rows = _load_unsorted()
    if not rows:
        print("No rows in unsorted.csv.")
        return
    _save_unsorted(rows)
    print(f"\u2705 Refreshed {len(rows)} rows — final_filename recalculated.")


def cmd_fix_unsorted_dupes(args: argparse.Namespace) -> None:
    """Remove duplicate entries from unsorted.csv (keeps first occurrence)."""
    rows = _load_unsorted()
    if not rows:
        print("unsorted.csv is empty")
        return
    
    seen_paths: set[str] = set()
    unique_rows: List[Dict[str, str]] = []
    removed = 0
    
    for r in rows:
        path = r.get("file_path", "")
        if path in seen_paths:
            removed += 1
            print(f"  DUPE: {Path(path).name}")
        else:
            seen_paths.add(path)
            unique_rows.append(r)
    
    if removed == 0:
        print("✅ No duplicates found in unsorted.csv")
        return
    
    print(f"\nFound {removed} duplicate entries")
    
    if args.write:
        write_unsorted_rows(UNSORTED_CSV, unique_rows, [])  # bucket_choices ignored
        print(f"✅ Removed {removed} duplicates, {len(unique_rows)} entries remain")
    else:
        print(f"\n📝 DRY-RUN: Would remove {removed} duplicates")
        print(f"   Run with --write to apply changes")


def cmd_sync_audio_metrics(args: argparse.Namespace) -> None:
    """DEPRECATED: Essentia analysis is now cache-only and does not write to tags or unsorted.csv.
    
    BPM/Key in unsorted.csv come from Rekordbox tags only.
    Please analyze files in Rekordbox before running scan workflow.
    
    This command has been disabled to maintain data integrity.
    """
    print("❌ DEPRECATED: sync-audio-metrics command is no longer available.")
    print()
    print("   Essentia analysis is cache-only (for ML training features).")
    print("   BPM/Key in unsorted.csv must come from Rekordbox tags.")
    print()
    print("   Please:")
    print("   1. Analyze your files in Rekordbox (sets TBPM/TKEY tags)")
    print("   2. Run: python -m djlib.cli scan")
    print("   3. Optionally run: python -m djlib.cli analyze-audio (for ML features)")
    print()
    return

def cmd_import_canonical_dump(args: argparse.Namespace) -> None:
    """Import MusicBrainz Canonical Data dump into SQLite database."""
    
    db_path = get_canonical_db_path()
    
    # Check if database exists
    if db_path.exists() and not getattr(args, "force", False):
        print(f"✅ Database already exists: {db_path}")
        print(f"   Size: {db_path.stat().st_size / (1024**3):.2f} GB")
        print("\nUse --force to rebuild.")
        return
    
    # Find dump file
    dump_path = getattr(args, "dump", None)
    if dump_path:
        dump_path = Path(dump_path)
        if not dump_path.exists():
            print(f"❌ Dump file not found: {dump_path}")
            return
    else:
        # Auto-detect dump in data/ folder
        data_dir = db_path.parent
        dump_files = list(data_dir.glob("musicbrainz-canonical-dump-*.tar.zst"))
        
        if not dump_files:
            print("❌ No canonical dump found in data/ folder.")
            print("\nDownload from:")
            print("https://data.metabrainz.org/pub/musicbrainz/canonical_data/")
            print("\nExample:")
            print("  cd data/")
            print("  curl -O https://data.metabrainz.org/pub/musicbrainz/canonical_data/musicbrainz-canonical-dump-YYYYMMDD-HHMMSS/musicbrainz-canonical-dump-YYYYMMDD-HHMMSS.tar.zst")
            return
        
        # Use most recent dump
        dump_path = sorted(dump_files)[-1]
        print(f"📦 Found dump: {dump_path.name}")
    
    print(f"\n🔧 Importing canonical data...")
    print(f"   Dump: {dump_path}")
    print(f"   Database: {db_path}")
    print("\nThis will take several minutes. Please wait...\n")
    
    try:
        do_import_canonical_dump(dump_path, db_path)
        print(f"\n✅ Import complete!")
        print(f"   Database: {db_path}")
        print(f"   Size: {db_path.stat().st_size / (1024**3):.2f} GB")
    except Exception as e:
        print(f"\n❌ Import failed: {e}")
        import traceback
        traceback.print_exc()


def cmd_genres_resolve(args: argparse.Namespace) -> None:
    from djlib.metadata.genre_resolver import resolve as resolve_genres
    artist = (getattr(args, "artist", None) or "").strip()
    title = (getattr(args, "title", None) or "").strip()
    dur = getattr(args, "duration", None)
    version = (getattr(args, "version", None) or "").strip()
    res = resolve_genres(artist, title, version=version, duration_s=dur)
    if not res:
        print("Brak wyników z zewnętrznych źródeł (MB/LFM/SoundCloud).")
        return
    print(f"Main: {res.main}")
    if res.subs:
        print(f"Subs: {', '.join(res.subs)}")
    print(f"Confidence: {res.confidence:.2f}")
    print("Breakdown:")
    for s in res.breakdown:
        parts = ", ".join(f"{k}:{v:.2f}" for k, v in sorted(s.tags.items(), key=lambda kv: kv[1], reverse=True)[:5])
        print(f"  - {s.source}: {parts}")

def cmd_analyze_audio(args: argparse.Namespace) -> None:
    """Analiza audio (BPM/Key/Energy) dla INBOX lub wskazanego pliku/katalogu.
    Wyniki zapisywane są do cache SQLite (LOGS/audio_analysis.sqlite).
    """
    # Obsłuż --check-env
    if getattr(args, "check_env", False):
        if audio_check_env is None:
            print("Essentia backend niedostępny (brak modułu).")
            return
        info = audio_check_env()
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return

    if audio_analyze is None:
        print("Audio backend niedostępny. Zainstaluj Essentia lub uruchom z --check-env, aby sprawdzić środowisko.")
        return

    # Zbierz pliki do analizy
    targets = []
    base = Path(getattr(args, "path", "") or INBOX_DIR)
    if base.is_file():
        targets = [base]
    else:
        base = base if base.exists() else INBOX_DIR
        targets = [p for p in base.glob("**/*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS]

    total = len(targets)
    print(f"DEBUG: base={base}, total_targets={total}")  # DEBUG
    processed = 0
    updated = 0
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    status_path = LOGS_DIR / "audio_status.json"

    def _write_status(state: str, last_file: str = "", last_error: str = "") -> None:
        try:
            with status_path.open("w", encoding="utf-8") as f:
                json.dump({
                    "state": state,
                    "total": total,
                    "processed": processed,
                    "updated": updated,
                    "last_file": last_file,
                    "error": last_error,
                }, f, ensure_ascii=False)
        except Exception:
            pass

    # Parsuj zakres BPM
    lo, hi = 80, 180
    tb = getattr(args, "target_bpm", None)
    if tb and ":" in tb:
        try:
            lo, hi = [int(x) for x in tb.split(":", 1)]
        except Exception:
            pass

    _write_status("running", "")
    for p in targets:
        print(f"DEBUG: processing {p}")  # DEBUG
        try:
            res = audio_analyze(p, target_bpm_range=(lo, hi), recompute=bool(args.recompute), config={"target_bpm": [lo, hi]})
            # Jeśli analyze dokonało upsert do cache, liczymy jako updated
            if res:
                updated += 1
            _write_status("running", str(p))
        except Exception as e:
            print(f"DEBUG: exception {e}")  # DEBUG
            _write_status("running", str(p), str(e))
        processed += 1

    _write_status("done", "")
    print(f"🎧 Analyze-audio: files={total}, analyzed={updated}")
    
    # NOTE: Essentia analysis is cache-only. BPM/Key in unsorted.csv come from Rekordbox tags only.
    # If you need to sync, use Rekordbox analysis, not Essentia.

def cmd_ml_predict(_: argparse.Namespace) -> None:
    print(LEGACY_ML_MSG)


def _strip_ready_prefix(target: str) -> str:
    t = (target or "").strip()
    if t.startswith("READY TO PLAY/"):
        return t.split("/", 2)[-1] if "/" in t else t
    if t.startswith("REVIEW QUEUE/"):
        return t.split("/", 2)[-1] if "/" in t else t
    return t


def cmd_ml_train_local(_: argparse.Namespace) -> None:
    print(LEGACY_ML_MSG)


def cmd_ml_export_dataset(args: argparse.Namespace) -> None:
    """Export Essentia features + genre/bucket labels to CSV."""
    out_path = Path(getattr(args, "out", "") or (REPO_ROOT / "data" / "training_dataset_full.csv"))
    require_both = bool(getattr(args, "require_both_labels", False))
    stats = export_training_dataset(out_path=out_path, require_both_labels=require_both)
    print(
        f"ML dataset export: rows={stats['rows_exported']}, "
        f"missing_features={stats['missing_features']}, missing_labels={stats['missing_labels']}"
    )
    print(f" → CSV: {stats['output_path']}")


def cmd_qa_acceptance(args: argparse.Namespace) -> None:
    """Policz acceptance rate po predykcjach ML.

    Domyślnie czyta LOGS/ml_predictions.csv i porównuje z CSV (target_subfolder).
    Zlicza, ile predykcji zostało zaakceptowanych (predykcja == docelowy bucket).
    """
    log_path = LOGS_DIR / "ml_predictions.csv"
    if not log_path.exists():
        print("Brak LOGS/ml_predictions.csv — najpierw uruchom ml-predict.")
        return
    rows = load_records(CSV_PATH)
    by_path = {r.get("file_path"): r for r in rows}

    import csv as _csv
    total = 0
    accepted = 0
    min_conf = float(getattr(args, "min_confidence", 0.65))
    per_bucket = {}
    with log_path.open("r", encoding="utf-8") as f:
        reader = _csv.DictReader(f)
        for rec in reader:
            try:
                fp = rec.get("file_path") or rec.get("file") or rec.get("path") or rec.get("0") or rec.get("file_path, bucket, confidence")
                # nasze logi mają prosty format "file_path,bucket,confidence" bez nagłówków przy pierwszym zapisie,
                # ale później dokładamy nagłówek — obsłużmy oba przypadki
                if not fp and len(rec) == 3:
                    # spróbuj odczytu z anonimowych kluczy
                    vals = list(rec.values())
                    fp, pbucket, confs = vals[0], vals[1], vals[2]
                else:
                    pbucket = rec.get("bucket") or ""
                    confs = rec.get("confidence") or "0"
                conf = 0.0
                try:
                    conf = float(confs)
                except Exception:
                    pass
                if conf < min_conf:
                    continue
                total += 1
                r = by_path.get(fp)
                if not r:
                    continue
                tgt = _strip_ready_prefix(r.get("target_subfolder", ""))
                pred = _strip_ready_prefix(pbucket)
                if tgt and pred and tgt == pred:
                    accepted += 1
                    per_bucket[pred] = per_bucket.get(pred, 0) + 1
            except Exception:
                continue

    rate = (accepted / total) if total else 0.0
    print(f"Acceptance: {accepted}/{total} = {rate:.2%}")
    if per_bucket:
        print("Akceptacje per bucket:")
        for b, c in sorted(per_bucket.items(), key=lambda x: x[1], reverse=True):
            print(f"  - {b}: {c}")


def cmd_import_rekordbox(args: argparse.Namespace) -> None:
    """
    Phase 1: Import Rekordbox snapshot (READ-ONLY).
    Creates CSV snapshot for path mapping.
    """
    output_path = Path(args.out)
    tag_files = args.tag_files
    workers = args.workers
    
    print("\n" + "=" * 60)
    print("PHASE 1: IMPORT REKORDBOX SNAPSHOT (READ-ONLY)")
    if tag_files:
        print("         + TAGGING FILES WITH DJLIB_TRACK_ID")
        print(f"         + WORKERS: {workers}")
    print("=" * 60)
    print()
    
    try:
        count = import_rekordbox_snapshot(output_path, tag_files=tag_files, workers=workers)
        print()
        print("=" * 60)
        print(f"✅ SUCCESS: Exported {count} tracks")
        print("=" * 60)
        print(f"\nSnapshot saved to: {output_path}")
        if tag_files:
            print("\n🔑 Files tagged with DJLIB_TRACK_ID for reliable path tracking")
        print("\nNext steps:")
        print("  1. Run 'apply' to move tracks")
        print("  2. Use this snapshot to create path map")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


def cmd_import_traktor(args: argparse.Namespace) -> None:
    """
    Phase 1: Import Traktor snapshot (READ-ONLY).
    Creates CSV snapshot for path mapping.
    """
    collection_path = Path(args.collection)
    output_path = Path(args.out)
    tag_files = args.tag_files
    workers = args.workers
    
    print("\n" + "=" * 60)
    print("PHASE 1: IMPORT TRAKTOR SNAPSHOT (READ-ONLY)")
    if tag_files:
        print("         + TAGGING FILES WITH DJLIB_TRACK_ID")
        print(f"         + WORKERS: {workers}")
    print("=" * 60)
    print()
    
    try:
        count = import_traktor_snapshot(collection_path, output_path, tag_files=tag_files, workers=workers)
        print()
        print("=" * 60)
        print(f"✅ SUCCESS: Exported {count} tracks")
        print("=" * 60)
        print(f"\nSnapshot saved to: {output_path}")
        if tag_files:
            print("\n🔑 Files tagged with DJLIB_TRACK_ID for reliable path tracking")
        print("\nNext steps:")
        print("  1. Run 'apply' to move tracks")
        print("  2. Use this snapshot to create path map")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


def cmd_sync_dj_libraries(args: argparse.Namespace) -> None:
    """
    WORKFLOW 0: Sync library.csv with DJ software databases.
    Ensures all approved tracks are in Rekordbox + Traktor with custom tags.
    """
    dry_run = not args.write
    
    print("\n" + "=" * 60)
    print("WORKFLOW 0: SYNC DJ LIBRARIES & TAGS")
    if dry_run:
        print("         (DRY-RUN MODE)")
    else:
        print("         ⚠️  WRITE MODE - WILL MODIFY FILES!")
    print("=" * 60)
    print()
    print("This workflow:")
    print("  1. Imports snapshots from Rekordbox + Traktor")
    print("  2. Merges into library.csv (removes duplicates)")
    print("  3. Adds DJLIB custom tags to ALL library files")
    print("     - DJLIB_TRACK_ID (our internal UUID)")
    print("     - DJLIB_REKORDBOX_ID (Rekordbox DB ID)")
    print("     - DJLIB_TRAKTOR_ID (Traktor AUDIO_ID)")
    print("     - DJLIB_ORIGINAL_PATH (reference path)")
    print()
    print("💡 Run this ONCE to prepare your library for tracking")
    print()
    
    # Step 1: Import snapshots from DJ software
    print("=" * 60)
    print("STEP 1: IMPORT DJ SOFTWARE SNAPSHOTS")
    print("=" * 60)
    print()
    
    try:
        # Import Rekordbox
        from djlib.external_sync import import_rekordbox_snapshot, import_traktor_snapshot
        import pandas as pd
        
        rekordbox_snapshot_path = Path("LOGS/external_snapshots/rekordbox_snapshot.csv")
        rekordbox_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Import WITHOUT tagging (we'll tag later after merge)
        rekordbox_count = import_rekordbox_snapshot(
            rekordbox_snapshot_path,
            tag_files=False,  # Don't tag yet
            workers=1
        )
        print(f"✅ Imported {rekordbox_count} tracks from Rekordbox")
        
        # Import Traktor
        traktor_snapshot_path = Path("LOGS/external_snapshots/traktor_snapshot.csv")
        
        # Auto-detect Traktor collection.nml
        collection_nml_path = None
        docs = Path.home() / "Documents" / "Native Instruments"
        if docs.exists():
            for traktor_dir in docs.glob("Traktor*"):
                nml = traktor_dir / "collection.nml"
                if nml.exists():
                    collection_nml_path = nml
                    break
        
        traktor_count = 0
        if collection_nml_path and collection_nml_path.exists():
            # Import WITHOUT tagging (we'll tag later after merge)
            traktor_count = import_traktor_snapshot(
                collection_nml_path,
                traktor_snapshot_path,
                tag_files=False,  # Don't tag yet
                workers=1
            )
            print(f"✅ Imported {traktor_count} tracks from Traktor")
        else:
            print("⚠️  Traktor collection.nml not found - skipping Traktor import")
        
        # Merge snapshots into library.csv
        if rekordbox_snapshot_path.exists() or traktor_snapshot_path.exists():
            dfs = []
            if rekordbox_snapshot_path.exists():
                rb_df = pd.read_csv(rekordbox_snapshot_path)
                # Rename external_track_id to rekordbox_id for Rekordbox rows
                if 'external_track_id' in rb_df.columns:
                    rb_df['rekordbox_id'] = rb_df['external_track_id']
                    rb_df['traktor_id'] = ''
                dfs.append(rb_df)
            if traktor_snapshot_path.exists():
                tr_df = pd.read_csv(traktor_snapshot_path)
                # Rename external_track_id to traktor_id for Traktor rows
                if 'external_track_id' in tr_df.columns:
                    tr_df['traktor_id'] = tr_df['external_track_id']
                    tr_df['rekordbox_id'] = ''
                dfs.append(tr_df)
            
            df = pd.concat(dfs, ignore_index=True)
            
            # Filter out unwanted tracks
            before_filter = len(df)
            
            # 1. Apple Music streaming tracks (not local files)
            df = df[~df['old_full_path'].str.startswith('apple-music:', na=False)]
            apple_music_filtered = before_filter - len(df)
            
            # 2. Rekordbox sample tracks (artist = "rekordbox")
            df = df[~(df['artist'].fillna('').str.lower() == 'rekordbox')]
            rekordbox_samples_filtered = before_filter - apple_music_filtered - len(df)
            
            # 3. Short tracks (< 5 seconds) - likely loops/samples
            if 'duration_seconds' in df.columns:
                df['duration_seconds'] = pd.to_numeric(df['duration_seconds'], errors='coerce')
                df = df[(df['duration_seconds'].isna()) | (df['duration_seconds'] >= 5)]
                short_tracks_filtered = before_filter - apple_music_filtered - rekordbox_samples_filtered - len(df)
            else:
                short_tracks_filtered = 0
            
            total_filtered = before_filter - len(df)
            
            # Merge duplicates intelligently - PRIMARY key is track_id (from file tags)
            # This ensures moved files are recognized as same track
            df['old_full_path'] = df['old_full_path'].fillna('').astype(str)
            df['old_full_path_norm'] = df['old_full_path'].str.strip().map(
                lambda p: os.path.normpath(p) if p else ''
            )
            df['track_id'] = df['track_id'].fillna('').astype(str)
            
            # Group by track_id FIRST (primary), then by path (fallback for new files)
            before_merge = len(df)
            merged_rows = []
            seen_track_ids = set()
            
            # First pass: group by track_id (for tagged files)
            for track_id, group in df.groupby('track_id'):
                if not track_id or track_id == '':
                    continue  # Will handle these in second pass
                
                seen_track_ids.add(track_id)
                
                # Prefer Rekordbox data for metadata (BPM, key, etc) but keep both IDs
                rb_rows = group[group['external_source'] == 'rekordbox']
                tr_rows = group[group['external_source'] == 'traktor']
                
                if len(rb_rows) > 0:
                    # Use Rekordbox as base (more complete metadata)
                    base_row = rb_rows.iloc[0].copy()
                    
                    # Smart rating merge: prefer Rekordbox, but use Traktor if RB empty
                    if len(tr_rows) > 0:
                        rb_rating = float(base_row.get('rating', 0) or 0)
                        tr_rating = float(tr_rows.iloc[0].get('rating', 0) or 0)
                        
                        if rb_rating == 0 and tr_rating > 0:
                            # Rekordbox has no rating, use Traktor's
                            base_row['rating'] = tr_rating
                        # else: Rekordbox has rating (or both empty), keep Rekordbox
                else:
                    # Use Traktor if no Rekordbox entry
                    base_row = group.iloc[0].copy()
                
                # CRITICAL: Pick the path that actually exists (file may have moved)
                existing_path = None
                for _, row in group.iterrows():
                    path = row.get('old_full_path', '')
                    if path and Path(path).exists():
                        existing_path = path
                        break
                
                if existing_path:
                    base_row['old_full_path'] = existing_path
                # else: keep whichever path was in base_row (file missing from both)
                
                # Merge IDs from both sources
                rb_id = rb_rows.iloc[0]['rekordbox_id'] if len(rb_rows) > 0 else ''
                tr_id = tr_rows.iloc[0]['traktor_id'] if len(tr_rows) > 0 else ''
                
                base_row['rekordbox_id'] = rb_id
                base_row['traktor_id'] = tr_id
                
                # Set external_source based on what we have
                if rb_id and tr_id:
                    base_row['external_source'] = 'rekordbox+traktor'
                elif rb_id:
                    base_row['external_source'] = 'rekordbox'
                elif tr_id:
                    base_row['external_source'] = 'traktor'
                
                merged_rows.append(base_row)
            
            # Second pass: handle rows without track_id (shouldn't happen, but safety)
            no_track_id_rows = df[df['track_id'] == '']
            for _, row in no_track_id_rows.iterrows():
                merged_rows.append(row.copy())
            
            df = pd.DataFrame(merged_rows)
            df = df.drop(columns=['old_full_path_norm'])
            duplicates_merged = before_merge - len(df)
            
            # Third pass: deduplicate by traktor_id (audio fingerprint)
            # This catches files that were re-imported with different track_ids
            before_fp_dedup = len(df)
            df['traktor_id'] = df['traktor_id'].fillna('').astype(str)
            
            # Group by traktor_id and pick best row (prefer existing file path)
            fp_groups: dict[str, list] = {}
            no_fp_rows = []
            
            for _, row in df.iterrows():
                traktor_id = row.get('traktor_id', '')
                if traktor_id and len(traktor_id) > 50:
                    if traktor_id not in fp_groups:
                        fp_groups[traktor_id] = []
                    fp_groups[traktor_id].append(row)
                else:
                    no_fp_rows.append(row)
            
            fp_merged_rows = []
            for traktor_id, rows in fp_groups.items():
                if len(rows) == 1:
                    fp_merged_rows.append(rows[0])
                else:
                    # Multiple rows with same audio fingerprint - pick best one
                    # Priority: 1) file exists, 2) has rekordbox_id, 3) first one
                    best_row = None
                    for r in rows:
                        path = r.get('old_full_path', '')
                        if path and Path(path).exists():
                            # Merge all IDs into this row
                            if best_row is None:
                                best_row = r.copy()
                            # Merge rekordbox_id if missing
                            if not best_row.get('rekordbox_id') and r.get('rekordbox_id'):
                                best_row['rekordbox_id'] = r['rekordbox_id']
                            # Update track_id from the file that exists
                            best_row['track_id'] = r.get('track_id', best_row.get('track_id', ''))
                            break
                    
                    if best_row is None:
                        # No existing file - use first row
                        best_row = rows[0].copy()
                        # But merge IDs from all rows
                        for r in rows[1:]:
                            if not best_row.get('rekordbox_id') and r.get('rekordbox_id'):
                                best_row['rekordbox_id'] = r['rekordbox_id']
                    
                    fp_merged_rows.append(best_row)
            
            # Add rows without fingerprint
            fp_merged_rows.extend(no_fp_rows)
            
            df = pd.DataFrame(fp_merged_rows)
            fp_duplicates_merged = before_fp_dedup - len(df)
            duplicates_merged += fp_duplicates_merged
            
            from djlib.library_schema import (
                LIBRARY_FIELDNAMES as _CANONICAL,
                apply_gig_track_guard,
                load_library_csv,
                merge_with_existing_library,
                save_library_csv,
            )
            from djlib.locks import csv_lock

            # Wrap the whole load → merge → save in a cross-process lock so
            # the Review UI cannot save between our read and write (silent
            # data loss otherwise — last writer wins, SHA-256 sidecar is
            # valid for whichever wrote last, masking the loss).
            with csv_lock(CSV_PATH):
                # Merge-by-track_id: preserve djlib-owned fields (file_hash,
                # original_path, added_date, …) from the existing library.csv.
                # Without this step the DJ-software snapshot nukes everything
                # djlib computed or tracked itself. Orphan rows (previously in
                # library, no longer returned by RB/Traktor) are kept with their
                # external IDs cleared.
                existing_rows = load_library_csv(CSV_PATH)

                # Build lookup of existing cue_points by track_id so we can
                # preserve them when the fresh sync didn't produce data (e.g.
                # read error, or track temporarily not in RB).
                existing_cues_by_tid: Dict[str, Dict[str, str]] = {
                    str(r.get("track_id", "")): {
                        "rb": str(r.get("cue_points_rb", "")),
                        "tk": str(r.get("cue_points_tk", "")),
                    }
                    for r in existing_rows
                    if r.get("track_id")
                }

                new_rows = df.fillna("").to_dict(orient="records")
                merged_rows = merge_with_existing_library(new_rows, existing_rows)
                merged_rows, live_skipped = apply_gig_track_guard(merged_rows, existing_rows)

                # Preserve existing cue_points when the fresh sync produced
                # no data (read error or track genuinely absent from RB/Traktor).
                for row in merged_rows:
                    tid = str(row.get("track_id", ""))
                    ex = existing_cues_by_tid.get(tid, {})
                    if not row.get("cue_points_rb") and ex.get("rb"):
                        row["cue_points_rb"] = ex["rb"]
                    if not row.get("cue_points_tk") and ex.get("tk"):
                        row["cue_points_tk"] = ex["tk"]

                if live_skipped:
                    print(f"   ⚠️  Skipped DJ-software update for {live_skipped} track(s) "
                          f"currently on an active gig (live_location != nas).")

                # Preserve any columns pandas produced that aren't in the
                # canonical schema yet. The canonical writer drops unknowns by
                # default; `extra_fieldnames` is the escape hatch for transient
                # legacy columns.
                extra = [c for c in df.columns if c not in _CANONICAL]
                backup_path = save_library_csv(
                    CSV_PATH,
                    merged_rows,
                    extra_fieldnames=extra,
                )
                if backup_path:
                    log.info("library.csv backed up to %s", backup_path)
            orphan_count = sum(
                1 for r in merged_rows
                if not r.get("rekordbox_id") and not r.get("traktor_id")
            )
            print(f"✅ Merged {len(merged_rows)} unique tracks into library.csv")
            if orphan_count:
                print(f"   ({orphan_count} orphan rows — in library but no longer in RB/Traktor)")
            if total_filtered > 0:
                print(f"   (Filtered out {total_filtered} unwanted tracks:")
                if apple_music_filtered > 0:
                    print(f"    • {apple_music_filtered} Apple Music streaming tracks")
                if rekordbox_samples_filtered > 0:
                    print(f"    • {rekordbox_samples_filtered} Rekordbox sample tracks")
                if short_tracks_filtered > 0:
                    print(f"    • {short_tracks_filtered} short tracks (< 5 seconds)")
                print("   )")
            print(f"   (Merged {duplicates_merged} duplicates - combined Rekordbox + Traktor IDs)")
        else:
            if not CSV_PATH.exists():
                raise FileNotFoundError("No snapshots imported and library.csv doesn't exist")
            print(f"⚠️  Using existing library.csv")
    
    except Exception as e:
        print(f"⚠️  Snapshot import failed: {e}")
        if not CSV_PATH.exists():
            print()
            print("=" * 60)
            print("⚠️  library.csv NOT FOUND")
            print("=" * 60)
            print()
            print(f"Expected location: {CSV_PATH}")
            print()
            print("This could mean:")
            print("  • First-time setup (file never existed)")
            print("  • File was deleted accidentally")
            print("  • Wrong configuration (check config.yml)")
            print()
            response = input("Create new empty library.csv? [y/N]: ").strip().lower()
            if response == 'y':
                CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
                # Create with minimal required columns
                import pandas as pd
                from djlib.csvdb import FIELDNAMES
                empty_df = pd.DataFrame(columns=FIELDNAMES)
                empty_df.to_csv(CSV_PATH, index=False)
                print(f"✅ Created new library.csv at {CSV_PATH}")
                print("   Run 'scan' to populate it with tracks from UNSORTED/")
            else:
                print("❌ Cannot proceed without library.csv")
                raise FileNotFoundError(f"library.csv not found: {CSV_PATH}")
        print(f"   Continuing with existing library.csv...")
    
    # --- OPTIMIZATION: Cache UNSORTED scan for all recovery steps ---
    # Single scan replaces 3 separate rglob scans (Step 1.5a, 1.5b, 1.5c)
    from djlib.djlib_tags import read_djlib_tags, has_djlib_tags
    from djlib.config import load_config
    import csv
    
    cfg = load_config()
    unsorted_path = Path(cfg.get('unsorted_path', '~/Music Unsorted')).expanduser()
    
    # Pre-scan UNSORTED (once, not 3 times)
    _unsorted_audio_files: list[Path] = []
    _unsorted_file_tags: dict[Path, dict] = {}  # file → djlib_tags
    
    if unsorted_path.exists():
        print("📂 Scanning UNSORTED folder (single pass for all recovery steps)...")
        for audio_file in unsorted_path.rglob('*'):
            if audio_file.suffix.lower() in AUDIO_EXTS:
                _unsorted_audio_files.append(audio_file)
                try:
                    _unsorted_file_tags[audio_file] = read_djlib_tags(audio_file)
                except Exception:
                    _unsorted_file_tags[audio_file] = {}
        print(f"   Found {len(_unsorted_audio_files)} audio files")
    
    # Helper: skip files whose filename is not safe (mojibake / NFD double-encoding etc.)
    # macOS stores filenames in NFD; we accept NFD but reject:
    #   (a) names containing C0/C1 control chars — typical double-encoded UTF-8 mojibake
    #   (b) paths that don't round-trip: the name must appear in the parent directory
    #       listing under NFC-normalized comparison. Fake/stale Path objects (stale
    #       glob results, broken symlinks, mid-rename races) fail this check.
    # Guard applies at every step that performs or depends on filesystem writes:
    # 1.5a track-id reconciliation, 1.5b Rekordbox recovery, 1.5c Traktor recovery,
    # and Step 2 (actual DJLIB tag writes on library files).
    _unsafe_files: list[tuple[Path, str]] = []
    _listdir_cache: Dict[str, frozenset[str]] = {}

    def _path_is_safe(p: Path) -> tuple[bool, str]:
        return _check_path_is_safe(p, parent_names_cache=_listdir_cache)

    def _warn_unsafe(p: Path, why: str) -> None:
        _unsafe_files.append((p, why))
        print(f"   ⚠️  Skipping file — broken characters in name: {p.name!r}")
        print(f"      reason: {why}")
        print(f"      (You'll be offered an automatic rename at the end of this run.)")

    def _sanitize_filename(name: str) -> str:
        """Turn a mojibake/typography-polluted filename into a safe ASCII-ish one.

        Preserves the extension. Returns a string that, when written to disk,
        will pass _path_is_safe().
        """
        # First, try to undo double-encoded UTF-8 (mojibake): bytes like "â\x80\x9c"
        # are the latin-1 interpretation of UTF-8 bytes \xe2\x80\x9c = "“".
        # macOS stores filenames in NFD (a + combining circumflex); normalize to NFC
        # before the latin-1 roundtrip, otherwise combining marks can't encode.
        try:
            nfc = unicodedata.normalize("NFC", name)
            fixed = nfc.encode("latin-1").decode("utf-8")
            if fixed != nfc and any(ch in fixed for ch in "“”‘’–—…"):
                name = fixed
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass

        stem, dot, ext = name.rpartition(".")
        if not dot:
            stem, ext = name, ""
        replacements = {
            "“": '"', "”": '"', "„": '"', "‟": '"',
            "‘": "'", "’": "'", "‚": "'", "‛": "'",
            "–": "-", "—": "-", "−": "-",
            "…": "...",
            " ": " ",
        }
        for src, dst in replacements.items():
            stem = stem.replace(src, dst)
        # Strip C0/C1 control characters (typical mojibake residue)
        stem = "".join(
            ch for ch in stem
            if not (ord(ch) < 0x20 or 0x80 <= ord(ch) <= 0x9F)
        )
        # Collapse runs of whitespace
        stem = " ".join(stem.split())
        stem = stem.strip(" .")
        if not stem:
            stem = "untitled"
        return f"{stem}.{ext}" if ext else stem

    # Step 1.5a: Track ID reconciliation - find moved files by their DJLIB_TRACK_ID tag
    print()
    print("=" * 60)
    print("STEP 1.5a: TRACK ID RECONCILIATION")
    print("=" * 60)
    print()
    print("Looking for files with DJLIB_TRACK_ID tags...")
    print("(This finds files that were tagged before being moved)")
    print()
    
    try:
        if unsorted_path.exists() and _unsorted_audio_files:
            # Build track_id → current_path from library.csv
            track_id_to_path: dict[str, str] = {}
            with open(CSV_PATH, 'r', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    tid = row.get('track_id', '')
                    path = row.get('old_full_path', '')
                    if tid and path:
                        track_id_to_path[tid] = path
            
            # Use cached UNSORTED scan instead of re-scanning
            reconciled_paths: dict[str, str] = {}  # old_path → new_path
            
            for audio_file in _unsorted_audio_files:
                ok, why = _path_is_safe(audio_file)
                if not ok:
                    _warn_unsafe(audio_file, why)
                    continue
                tags = _unsorted_file_tags.get(audio_file, {})
                file_track_id = tags.get('track_id', '')
                
                if file_track_id and file_track_id in track_id_to_path:
                    csv_path = track_id_to_path[file_track_id]
                    current_path = str(audio_file)
                    
                    # Path changed - needs update
                    if csv_path != current_path:
                        reconciled_paths[csv_path] = current_path
                        if dry_run:
                            print(f"   🔍 Would reconcile: {audio_file.name}")
                            print(f"      track_id: {file_track_id}")
                        else:
                            print(f"   ✅ Reconciled: {audio_file.name}")
            
            if reconciled_paths:
                print(f"\n🔄 Found {len(reconciled_paths)} files with changed paths")
                
                if not dry_run:
                    # Update library.csv
                    rows = []
                    with open(CSV_PATH, 'r', newline='', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        fieldnames = reader.fieldnames or []
                        for row in reader:
                            old_path = row.get('old_full_path', '')
                            if old_path in reconciled_paths:
                                row['old_full_path'] = reconciled_paths[old_path]
                            rows.append(row)
                    
                    with open(CSV_PATH, 'w', newline='', encoding='utf-8') as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(rows)
                    
                    print(f"   📝 Updated {len(reconciled_paths)} paths in library.csv")
            else:
                print("ℹ️  All tagged files are at their expected paths")
        else:
            print(f"⚠️  UNSORTED path not found: {unsorted_path}")
    except Exception as e:
        print(f"⚠️  Track ID reconciliation failed: {e}")
        import traceback
        traceback.print_exc()
    
    # Step 1.5b: Filename fallback for Rekordbox - tag files in UNSORTED that match by filename
    print()
    print("=" * 60)
    print("STEP 1.5b: FILENAME FALLBACK (Rekordbox)")
    print("=" * 60)
    print()
    print("Looking for files that match Rekordbox entries by filename...")
    print("(This recovers files moved after Rekordbox analysis but before Workflow 0)")
    print()
    
    try:
        from djlib.external_sync import get_rekordbox_track_ids
        from djlib.djlib_tags import write_djlib_tags
        
        if unsorted_path.exists() and _unsorted_audio_files:
            # Build filename → rekordbox_id map (for files where path no longer exists)
            # Guard with SIGALRM timeout — if Rekordbox DB is locked/missing, hang here can block WF0.
            import signal as _signal

            class _RbTimeout(Exception):
                pass

            def _rb_handler(signum, frame):
                raise _RbTimeout()

            _old_handler = _signal.signal(_signal.SIGALRM, _rb_handler)
            _signal.alarm(60)
            try:
                rekordbox_mapping = get_rekordbox_track_ids()  # {Path: rb_id}
            except _RbTimeout:
                print("   ⚠️  Rekordbox mapping timed out (60s) — skipping 1.5b. Is Rekordbox running / DB locked?")
                rekordbox_mapping = {}
            finally:
                _signal.alarm(0)
                _signal.signal(_signal.SIGALRM, _old_handler)
            
            # Create filename → (rb_id, original_path) for files that don't exist at their DB path
            filename_to_rb: dict[str, tuple[str, Path]] = {}
            for db_path, rb_id in rekordbox_mapping.items():
                if not db_path.exists():
                    # File moved - add to filename fallback
                    filename_to_rb[db_path.name] = (rb_id, db_path)
            
            if filename_to_rb:
                print(f"📋 Found {len(filename_to_rb)} Rekordbox entries with missing files")
                
                # Use cached UNSORTED scan instead of re-scanning
                recovered = 0
                already_tagged = 0
                recovered_paths: dict[str, str] = {}  # old_path → new_path
                
                for audio_file in _unsorted_audio_files:
                    ok, why = _path_is_safe(audio_file)
                    if not ok:
                        _warn_unsafe(audio_file, why)
                        continue
                    if audio_file.name in filename_to_rb:
                        rb_id, original_path = filename_to_rb[audio_file.name]
                        
                        # Always track path update (file was found at new location)
                        if str(audio_file) != str(original_path):
                            recovered_paths[str(original_path)] = str(audio_file)
                        
                        # Check if already tagged with this rekordbox_id (use cached tags)
                        cached_tags = _unsorted_file_tags.get(audio_file, {})
                        if cached_tags.get('rekordbox_id') == rb_id:
                            already_tagged += 1
                            continue
                        
                        if dry_run:
                            print(f"   🔍 Would recover: {audio_file.name}")
                            print(f"      Rekordbox ID: {rb_id}")
                            print(f"      Original path: {original_path}")
                            recovered += 1
                        else:
                            try:
                                from djlib.djlib_tags import generate_track_id
                                track_id = generate_track_id(audio_file, '', '')  # Will read from file
                                
                                write_djlib_tags(
                                    audio_file,
                                    track_id=track_id,
                                    rekordbox_id=rb_id,
                                    original_path=str(original_path)
                                )
                                print(f"   ✅ Recovered: {audio_file.name} → rb_id={rb_id}")
                                recovered += 1
                            except Exception as e:
                                print(f"   ⚠️  Failed to recover {audio_file.name}: {e}")
                
                if recovered > 0:
                    print(f"\n🔄 Recovered {recovered} lost files by filename match")
                
                # Update library.csv with new paths (even for already-tagged files)
                if not dry_run and recovered_paths:
                    import csv
                    
                    # Read current library.csv
                    rows = []
                    with open(CSV_PATH, 'r', newline='', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        fieldnames = reader.fieldnames or []
                        for row in reader:
                            # Check if this row needs path update
                            old_path = row.get('old_full_path', '')
                            if old_path in recovered_paths:
                                row['old_full_path'] = recovered_paths[old_path]
                            rows.append(row)
                    
                    # Write updated library.csv
                    with open(CSV_PATH, 'w', newline='', encoding='utf-8') as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(rows)
                    
                    print(f"   📝 Updated {len(recovered_paths)} paths in library.csv")
                
                if already_tagged > 0:
                    print(f"⏭️  Skipped {already_tagged} already tagged files")
                if recovered == 0 and already_tagged == 0:
                    print("ℹ️  No lost files found in UNSORTED")
            else:
                print("ℹ️  All Rekordbox files exist at their expected paths")
        else:
            print(f"⚠️  UNSORTED path not found: {unsorted_path}")
    except Exception as e:
        print(f"⚠️  Recovery step failed: {e}")
        print("   Continuing with normal tagging...")
    
    # Step 1.5c: Filename fallback for Traktor - tag files in UNSORTED that match by filename
    print()
    print("=" * 60)
    print("STEP 1.5c: FILENAME FALLBACK (Traktor)")
    print("=" * 60)
    print()
    print("Looking for files that match Traktor entries by filename...")
    print("(This recovers files moved after Traktor analysis but before Workflow 0)")
    print()
    
    try:
        from djlib.external_sync import get_traktor_track_ids
        from djlib.djlib_tags import write_djlib_tags
        
        if unsorted_path.exists() and _unsorted_audio_files:
            # Build filename → traktor_id map (for files where path no longer exists)
            traktor_mapping = get_traktor_track_ids()  # {Path: tr_id}
            
            # Create filename → (tr_id, original_path) for files that don't exist at their DB path
            filename_to_tr: dict[str, tuple[str, Path]] = {}
            for db_path, tr_id in traktor_mapping.items():
                if not db_path.exists():
                    # File moved - add to filename fallback
                    filename_to_tr[db_path.name] = (tr_id, db_path)
            
            if filename_to_tr:
                print(f"📋 Found {len(filename_to_tr)} Traktor entries with missing files")
                
                # Use cached UNSORTED scan instead of re-scanning
                recovered = 0
                already_tagged = 0
                recovered_paths: dict[str, str] = {}  # old_path → new_path
                
                for audio_file in _unsorted_audio_files:
                    ok, why = _path_is_safe(audio_file)
                    if not ok:
                        _warn_unsafe(audio_file, why)
                        continue
                    if audio_file.name in filename_to_tr:
                        tr_id, original_path = filename_to_tr[audio_file.name]
                        
                        # Always track path update (file was found at new location)
                        if str(audio_file) != str(original_path):
                            recovered_paths[str(original_path)] = str(audio_file)
                        
                        # Check if already tagged with this traktor_id (use cached tags)
                        cached_tags = _unsorted_file_tags.get(audio_file, {})
                        if cached_tags.get('traktor_id') == tr_id:
                            already_tagged += 1
                            continue
                        
                        if dry_run:
                            print(f"   🔍 Would recover: {audio_file.name}")
                            print(f"      Traktor ID: {tr_id[:50]}..." if len(tr_id) > 50 else f"      Traktor ID: {tr_id}")
                            print(f"      Original path: {original_path}")
                            recovered += 1
                        else:
                            try:
                                from djlib.djlib_tags import generate_track_id
                                track_id = generate_track_id(audio_file, '', '')  # Will read from file
                                
                                write_djlib_tags(
                                    audio_file,
                                    track_id=track_id,
                                    traktor_id=tr_id,
                                    original_path=str(original_path)
                                )
                                print(f"   ✅ Recovered: {audio_file.name} → traktor_id")
                                recovered += 1
                            except Exception as e:
                                print(f"   ⚠️  Failed to recover {audio_file.name}: {e}")
                
                if recovered > 0:
                    print(f"\n🔄 Recovered {recovered} lost Traktor files by filename match")
                
                # Update library.csv with new paths (even for already-tagged files)
                if not dry_run and recovered_paths:
                    import csv
                    
                    # Read current library.csv
                    rows = []
                    with open(CSV_PATH, 'r', newline='', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        fieldnames = reader.fieldnames or []
                        for row in reader:
                            # Check if this row needs path update
                            old_path = row.get('old_full_path', '')
                            if old_path in recovered_paths:
                                row['old_full_path'] = recovered_paths[old_path]
                            rows.append(row)
                    
                    # Write updated library.csv
                    with open(CSV_PATH, 'w', newline='', encoding='utf-8') as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(rows)
                    
                    print(f"   📝 Updated {len(recovered_paths)} paths in library.csv")
                
                if already_tagged > 0:
                    print(f"⏭️  Skipped {already_tagged} already tagged files")
                if recovered == 0 and already_tagged == 0:
                    print("ℹ️  No lost Traktor files found in UNSORTED")
            else:
                print("ℹ️  All Traktor files exist at their expected paths")
        else:
            print(f"⚠️  UNSORTED path not found: {unsorted_path}")
    except Exception as e:
        print(f"⚠️  Traktor recovery step failed: {e}")
        print("   Continuing with normal tagging...")
    
    # Step 2: Add DJLIB tags to all unique library files (ONE TIME)
    print()
    print("=" * 60)
    print("STEP 2: ADD DJLIB TAGS TO LIBRARY FILES")
    print("=" * 60)
    print()
    
    from djlib.csvdb import load_records
    from djlib.djlib_tags import write_djlib_tags
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    library_rows = load_records(CSV_PATH)
    print(f"📋 Loaded {len(library_rows)} unique tracks from library.csv")
    
    if dry_run:
        print("   (DRY-RUN mode - skipping actual file tagging)")
    else:
        tags_written = 0
        tags_skipped = 0
        tags_errors = 0
        error_list = []  # Collect all errors for later review
        skipped_list = []  # Collect skipped files
        
        def tag_file(row):
            """Tag a single file with DJLIB metadata."""
            file_path_str = row.get('old_full_path', '') or row.get('file_path', '')
            if not file_path_str:
                return 'skip', ('no_path', None)

            file_path = Path(file_path_str)
            if not file_path.exists():
                return 'skip', ('not_found', file_path.name)

            # Guard against mojibake / NFC-NFD mismatched names before any write.
            # mutagen tag writes can corrupt files or raise obscure errors on
            # control-char filenames (see WF0 double-encoded UTF-8 incident).
            ok, why = _path_is_safe(file_path)
            if not ok:
                return 'skip', ('unsafe_name', f"{file_path.name}: {why}")

            track_id = row.get('track_id', '')
            if not track_id:
                return 'skip', ('no_track_id', file_path.name)

            try:
                # Write all DJLIB tags including external IDs
                write_djlib_tags(
                    file_path,
                    track_id=track_id,
                    rekordbox_id=row.get('external_track_id') if row.get('external_source') == 'rekordbox' else None,
                    traktor_id=row.get('external_track_id') if row.get('external_source') == 'traktor' else None,
                    original_path=file_path_str
                )
                return 'ok', file_path.name
            except Exception as e:
                return 'error', (file_path.name, str(e))
        
        print(f"📝 Tagging {len(library_rows)} files with DJLIB_TRACK_ID (parallel, 4 workers)...")
        
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(tag_file, row): row for row in library_rows}
            
            for future in as_completed(futures):
                status, result = future.result()
                
                if status == 'ok':
                    tags_written += 1
                    if tags_written % 20 == 0:
                        print(f"  ✓ {tags_written}/{len(library_rows)} tagged...")
                elif status == 'skip':
                    tags_skipped += 1
                    if result and result[0] == 'not_found':
                        skipped_list.append(result[1])
                    elif result and result[0] == 'unsafe_name':
                        skipped_list.append(f"[unsafe] {result[1]}")
                elif status == 'error':
                    tags_errors += 1
                    if result:  # result is tuple (filename, error)
                        error_list.append(result)
        
        print()
        print(f"✅ Tags written: {tags_written}")
        if tags_skipped > 0:
            print(f"⏭️  Skipped (file not found): {tags_skipped}")
        if tags_errors > 0:
            print(f"❌ Errors: {tags_errors}")
        
        # Log issues to file
        if error_list or skipped_list:
            log_path = LOGS_DIR / "tagging_issues.log"
            with open(log_path, 'w') as f:
                f.write(f"=== Tagging Issues Log ({datetime.now().isoformat()}) ===\n\n")
                if skipped_list:
                    f.write(f"--- SKIPPED ({len(skipped_list)} files not found) ---\n")
                    for fn in skipped_list:
                        f.write(f"  {fn}\n")
                    f.write("\n")
                if error_list:
                    f.write(f"--- ERRORS ({len(error_list)} files failed) ---\n")
                    for fn, err in error_list:
                        f.write(f"  {fn}: {err}\n")
            print(f"📋 Issue details saved to: {log_path}")
        
        # Offer to show details if there are issues
        if tags_errors > 0 or tags_skipped > 0:
            try:
                show = input("\n⚠️  Show issue details? [y/N]: ").strip().lower()
                if show == 'y':
                    if skipped_list:
                        print(f"\n--- SKIPPED ({len(skipped_list)} files not found) ---")
                        for fn in skipped_list[:20]:  # Show first 20
                            print(f"  {fn}")
                        if len(skipped_list) > 20:
                            print(f"  ... and {len(skipped_list) - 20} more")
                    if error_list:
                        print(f"\n--- ERRORS ({len(error_list)} files failed) ---")
                        for fn, err in error_list[:20]:  # Show first 20
                            print(f"  {fn}: {err}")
                        if len(error_list) > 20:
                            print(f"  ... and {len(error_list) - 20} more")
            except EOFError:
                pass  # Non-interactive mode
    
    # Step 3: Sync ratings to DJ software
    print()
    print("=" * 60)
    print("STEP 3: SYNC RATINGS TO DJ SOFTWARE")
    print("=" * 60)
    print()
    
    from djlib.external_sync import sync_ratings_to_dj_software
    
    rb_updates = 0
    tr_updates = 0
    
    try:
        rb_updates, tr_updates = sync_ratings_to_dj_software(CSV_PATH, dry_run=dry_run)
        
        if dry_run:
            print(f"🔍 DRY-RUN: Rating sync preview complete")
        else:
            if rb_updates > 0 or tr_updates > 0:
                print(f"✅ Rating sync complete")
            else:
                print("ℹ️  No rating updates needed (all ratings already in sync)")
    except Exception as e:
        print(f"⚠️  Rating sync failed: {e}")
        print("   (Continuing anyway - ratings can be synced manually later)")
    
    # Done!
    print()
    print("=" * 60)
    print("✅ WORKFLOW 0 COMPLETE")
    print("=" * 60)
    print()
    print("Summary:")
    print(f"  • Imported snapshots from Rekordbox + Traktor")
    print(f"  • Created library.csv with {len(library_rows)} tracks")
    if not dry_run:
        print(f"  • Tagged {tags_written} files with DJLIB metadata")
        if rb_updates > 0 or tr_updates > 0:
            print(f"  • Synced ratings: {rb_updates} Rekordbox, {tr_updates} Traktor")
    else:
        print(f"  • DRY-RUN: No files were modified")
    print()
    print("💡 Your library is now ready for tracking!")
    print("   Files have DJLIB_TRACK_ID, DJLIB_REKORDBOX_ID, DJLIB_TRAKTOR_ID tags")
    print()

    if _unsafe_files:
        print("=" * 60)
        print(f"⚠️  {len(_unsafe_files)} FILE(S) SKIPPED — broken characters in names")
        print("=" * 60)
        print()
        print("These files have broken characters in their names (usually copied from")
        print("Beatport / SoundCloud / web pages, where quotes and dashes are fancy")
        print("typography). macOS can display them, but tools can't process them.")
        print()
        print("I can rename them automatically. You'll see each proposal before it happens.")
        print()

        import sys as _sys

        can_prompt = _sys.stdin.isatty() and not dry_run
        renamed_count = 0
        remaining: list[tuple[Path, str]] = []
        accept_all = False

        if not can_prompt:
            reason = "dry-run" if dry_run else "non-interactive stdin"
            print(f"(Skipping interactive rename: {reason}.)")
            print("To fix manually: rename each file in Finder, then re-run")
            print("    python -m djlib.cli sync-dj-libraries --write")
            print()
            for p, why in _unsafe_files:
                proposed = _sanitize_filename(p.name)
                print(f"   • {p}")
                print(f"       reason:   {why}")
                print(f"       suggested new name: {proposed}")
            print()
        else:
            for idx, (p, why) in enumerate(_unsafe_files, 1):
                proposed_name = _sanitize_filename(p.name)
                new_path = p.with_name(proposed_name)

                print(f"[{idx}/{len(_unsafe_files)}] Proposed rename:")
                print(f"   folder: {p.parent}")
                print(f"   BEFORE: {p.name!r}")
                print(f"   AFTER:  {proposed_name}")
                print(f"   reason: {why}")

                if accept_all:
                    choice = "y"
                else:
                    try:
                        choice = input("   Rename? [y]es / [n]o / [a]ll remaining / [q]uit: ").strip().lower()
                    except EOFError:
                        choice = "n"

                if choice in ("a", "all"):
                    accept_all = True
                    choice = "y"
                if choice in ("q", "quit"):
                    remaining.append((p, why))
                    for rest in _unsafe_files[idx:]:
                        remaining.append(rest)
                    print("   (aborted; remaining files left untouched)")
                    print()
                    break
                if choice not in ("y", "yes"):
                    print("   → skipped")
                    remaining.append((p, why))
                    print()
                    continue

                # Suffix is reactive: only when the clean target is actually
                # taken in the SAME folder. Different-folder copies (most of
                # the user's real-world case) rename straight through with
                # no suffix. POSIX `os.rename` would silently overwrite an
                # existing file, so we pre-check — Python stdlib has no
                # "rename-fail-if-exists" primitive on POSIX.
                if new_path.exists() and new_path != p:
                    new_path = _ensure_unique_path(new_path)
                    print(
                        f"   ℹ️  '{proposed_name}' already exists here — "
                        f"using {new_path.name} instead to avoid overwriting."
                    )

                try:
                    os.rename(str(p), str(new_path))
                    print(f"   ✅ renamed")
                    renamed_count += 1
                except Exception as e:
                    print(f"   ❌ rename failed: {e}")
                    remaining.append((p, why))
                print()

            print(f"Renamed {renamed_count} / {len(_unsafe_files)} file(s).")
            if remaining:
                print(f"{len(remaining)} still need manual attention:")
                for p, _why in remaining:
                    print(f"   • {p}")
            if renamed_count > 0:
                print()
                print("💡 Re-run to pick up the renamed files:")
                print("   python -m djlib.cli sync-dj-libraries --write")
            print()


def cmd_create_path_map(args: argparse.Namespace) -> None:
    """
    Phase 2: Create path mapping from move log + snapshots (READ-ONLY).
    """
    move_log = Path(args.move_log)
    rekordbox_snapshot = Path(args.rekordbox_snapshot) if args.rekordbox_snapshot else None
    traktor_snapshot = Path(args.traktor_snapshot) if args.traktor_snapshot else None
    output_path = Path(args.out) if args.out else None
    
    print("\n" + "=" * 60)
    print("PHASE 2: CREATE PATH MAP (READ-ONLY)")
    print("=" * 60)
    print()
    
    try:
        path_map_path = create_path_map(
            move_log,
            rekordbox_snapshot,
            traktor_snapshot,
            output_path
        )
        print()
        print("=" * 60)
        print("✅ SUCCESS: Path map created")
        print("=" * 60)
        print(f"\nPath map saved to: {path_map_path}")
        print("\nNext steps:")
        print("  Phase 3 not yet implemented (path sync to DJ software DBs)")
        print("  For now, use this map for manual verification or custom scripts")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


def cmd_sync_rekordbox(args: argparse.Namespace) -> None:
    """
    Phase 3: Sync paths to Rekordbox DB (WRITE - NOT YET IMPLEMENTED).
    """
    print("\n" + "=" * 60)
    print("PHASE 3: SYNC REKORDBOX PATHS (NOT YET IMPLEMENTED)")
    print("=" * 60)
    print()
    print("⚠️  This feature is planned but not yet implemented.")
    print()
    print("Why not implemented yet:")
    print("  • Requires extensive safety testing")
    print("  • Needs automatic backup/restore")
    print("  • Must support dry-run + confirmation")
    print("  • Transaction support for atomic updates")
    print()
    print("Current status: Use Phase 1 + Phase 2 for preparation")
    print()


def cmd_sync_traktor(args: argparse.Namespace) -> None:
    """
    Phase 3: Sync paths to Traktor collection.nml (WRITE - NOT YET IMPLEMENTED).
    """
    print("\n" + "=" * 60)
    print("PHASE 3: SYNC TRAKTOR PATHS (NOT YET IMPLEMENTED)")
    print("=" * 60)
    print()
    print("⚠️  This feature is planned but not yet implemented.")
    print()
    print("Why not implemented yet:")
    print("  • Requires extensive safety testing")
    print("  • Needs automatic backup/restore")
    print("  • Must preserve XML structure")
    print("  • Transaction support for atomic updates")
    print()
    print("Current status: Use Phase 1 + Phase 2 for preparation")
    print()


def cmd_add_to_traktor(args: argparse.Namespace) -> None:
    """
    Add tracks from unsorted.csv to Traktor collection.nml.
    """
    from djlib.external_sync import add_tracks_to_traktor
    
    collection_path = Path(args.collection)
    dry_run = not args.write
    
    print("\n" + "=" * 60)
    print("ADD TRACKS TO TRAKTOR")
    if dry_run:
        print("         (DRY-RUN MODE)")
    else:
        print("         ⚠️  WRITE MODE - WILL MODIFY TRAKTOR DB!")
    print("=" * 60)
    print()
    
    # Load tracks from unsorted.csv
    staging_rows = _load_unsorted()
    if not staging_rows:
        print("❌ No tracks in unsorted.csv")
        return
    
    print(f"📋 Found {len(staging_rows)} tracks in unsorted.csv")
    print()
    
    # Convert to format expected by add_tracks_to_traktor
    tracks = []
    for row in staging_rows:
        track_id = row.get('track_id', '')
        traktor_id = row.get('traktor_id', '')
        file_path = row.get('file_path', '')
        
        if not file_path:
            continue
        
        tracks.append({
            'file_path': file_path,
            'artist': row.get('artist', ''),
            'title': row.get('title', ''),
            'bpm': row.get('bpm', ''),
            'key': row.get('key_camelot', ''),
            'traktor_id': traktor_id if traktor_id else track_id,  # Use track_id as fallback
        })
    
    try:
        added_count, updated_count = add_tracks_to_traktor(collection_path, tracks, dry_run=dry_run)
        
        print()
        print("=" * 60)
        if dry_run:
            if added_count > 0:
                print(f"🔍 DRY-RUN: Would add {added_count} new tracks")
            if updated_count > 0:
                print(f"🔍 DRY-RUN: Would update {updated_count} existing track paths")
            print("\nTo actually apply changes, run with --write flag")
        else:
            if added_count > 0:
                print(f"✅ SUCCESS: Added {added_count} new tracks to Traktor")
            if updated_count > 0:
                print(f"🔄 SUCCESS: Updated {updated_count} existing track paths")
        print("=" * 60)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


def cmd_add_to_rekordbox(args: argparse.Namespace) -> None:
    """
    Add tracks from unsorted.csv to Rekordbox database.
    """
    from djlib.external_sync import add_tracks_to_rekordbox
    
    dry_run = not args.write
    
    print("\n" + "=" * 60)
    print("ADD TRACKS TO REKORDBOX")
    if dry_run:
        print("         (DRY-RUN MODE)")
    else:
        print("         ⚠️  WRITE MODE - WILL MODIFY REKORDBOX DB!")
    print("=" * 60)
    print()
    
    # Load tracks from unsorted.csv
    staging_rows = _load_unsorted()
    if not staging_rows:
        print("❌ No tracks in unsorted.csv")
        return
    
    print(f"📋 Found {len(staging_rows)} tracks in unsorted.csv")
    print()
    
    # Convert to format expected by add_tracks_to_rekordbox
    tracks = []
    for row in staging_rows:
        track_id = row.get('track_id', '')
        rekordbox_id = row.get('rekordbox_id', '')
        file_path = row.get('file_path', '')
        
        if not file_path:
            continue
        
        tracks.append({
            'file_path': file_path,
            'artist': row.get('artist', ''),
            'title': row.get('title', ''),
            'bpm': row.get('bpm', ''),
            'key': row.get('key_camelot', ''),
            'rekordbox_id': rekordbox_id,
        })
    
    try:
        added_count, updated_count = add_tracks_to_rekordbox(tracks, dry_run=dry_run)
        
        print()
        print("=" * 60)
        if dry_run:
            if added_count > 0:
                print(f"🔍 DRY-RUN: Would add {added_count} new tracks")
            if updated_count > 0:
                print(f"🔍 DRY-RUN: Would update {updated_count} existing track paths")
            print("\nTo actually apply changes, run with --write flag")
        else:
            if added_count > 0:
                print(f"✅ SUCCESS: Added {added_count} new tracks to Rekordbox")
            if updated_count > 0:
                print(f"🔄 SUCCESS: Updated {updated_count} existing track paths")
        print("=" * 60)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


def cmd_setup_beatport(args: argparse.Namespace) -> None:
    """Configure Beatport credentials for API access."""
    from djlib.metadata.beatport import set_beatport_credentials
    import getpass
    
    print("🎵 Beatport Credentials Setup")
    print("=" * 50)
    print("\nYour credentials will be securely stored in system keyring")
    print("(macOS Keychain / Windows Credential Manager / Linux Secret Service)")
    print()
    
    username = input("Beatport username: ").strip()
    if not username:
        print("❌ Username cannot be empty")
        return
    
    password = getpass.getpass("Beatport password: ")
    if not password:
        print("❌ Password cannot be empty")
        return
    
    try:
        set_beatport_credentials(username, password)
        print("\n✅ Credentials saved successfully!")
        print("\nTesting token refresh (first time takes ~10 seconds)...")
        
        from djlib.metadata.beatport import get_valid_token
        token = get_valid_token()
        
        if token:
            print("✅ Token obtained successfully - Beatport is ready to use!")
        else:
            print("⚠️  Credentials saved but token refresh failed - check your login details")
    except Exception as e:
        print(f"\n❌ Setup failed: {e}")


def cmd_traktor_dedup(args: argparse.Namespace) -> None:
    """Find and remove duplicate entries in Traktor collection."""
    from djlib.external_sync import remove_traktor_duplicates
    from djlib.config import load_config
    
    dry_run = not args.write
    
    print("\n" + "=" * 60)
    print("TRAKTOR DUPLICATE FINDER")
    if dry_run:
        print("         (DRY-RUN MODE)")
    else:
        print("         ⚠️  WRITE MODE - WILL MODIFY TRAKTOR DB!")
    print("=" * 60)
    print()
    
    collection_path = _get_traktor_collection_path()
    if not collection_path:
        return
    
    print(f"📁 Collection: {collection_path}")
    print()
    
    try:
        removed = remove_traktor_duplicates(
            collection_path,
            dry_run=dry_run,
            interactive=True,  # Always ask for confirmation
        )
        
        if removed > 0 and not dry_run:
            print(f"\n✅ Removed {removed} duplicate entries")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


def _get_traktor_collection_path() -> Optional[Path]:
    """Helper to get Traktor collection.nml path from config or default."""
    from djlib.config import load_config
    
    cfg = load_config()
    traktor_cfg = cfg.get('traktor', {})
    collection_path_str = traktor_cfg.get('collection_nml', '')
    collection_path = Path(collection_path_str).expanduser() if collection_path_str else None
    
    if not collection_path or not collection_path.exists():
        # Try default location
        docs = Path.home() / "Documents" / "Native Instruments"
        collection_path = None
        for traktor_dir in docs.glob("Traktor*"):
            nml = traktor_dir / "collection.nml"
            if nml.exists():
                collection_path = nml
                break
    
    if not collection_path or not collection_path.exists():
        print("❌ Traktor collection.nml not found")
        print("   Configure traktor.collection_nml in config.local.yml")
        return None
    
    return collection_path


def cmd_traktor_cleanup(args: argparse.Namespace) -> None:
    """Find and remove dead entries (missing files) from Traktor collection."""
    from djlib.external_sync import remove_traktor_dead_entries
    
    dry_run = not args.write
    
    print("\n" + "=" * 60)
    print("TRAKTOR DEAD ENTRY CLEANUP")
    if dry_run:
        print("         (DRY-RUN MODE)")
    else:
        print("         ⚠️  WRITE MODE - WILL MODIFY TRAKTOR DB!")
    print("=" * 60)
    print()
    
    collection_path = _get_traktor_collection_path()
    if not collection_path:
        return
    
    print(f"📁 Collection: {collection_path}")
    print(f"🔍 Scanning for entries where file no longer exists...")
    print()
    
    try:
        removed = remove_traktor_dead_entries(
            collection_path,
            dry_run=dry_run,
            interactive=True,  # Always ask for confirmation
        )
        
        if removed > 0 and not dry_run:
            print(f"\n✅ Cleaned up {removed} dead entries")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


def cmd_traktor_repair(args: argparse.Namespace) -> None:
    """Find dead entries and repair them by finding live duplicates."""
    from djlib.external_sync import repair_traktor_dead_entries
    
    dry_run = not args.write
    
    print("\n" + "=" * 60)
    print("TRAKTOR DEAD ENTRY REPAIR")
    if dry_run:
        print("         (DRY-RUN MODE)")
    else:
        print("         ⚠️  WRITE MODE - WILL MODIFY TRAKTOR DB!")
    print("=" * 60)
    print()
    
    collection_path = _get_traktor_collection_path()
    if not collection_path:
        return
    
    print(f"📁 Collection: {collection_path}")
    print(f"🔍 Scanning for dead entries with live duplicates...")
    print()
    
    try:
        result = repair_traktor_dead_entries(
            collection_path,
            dry_run=dry_run,
            interactive=True,
        )
        
        if not dry_run and result['repaired'] > 0:
            print(f"\n✅ Repaired {result['repaired']} entries")
            print(f"   Removed {result['duplicates_removed']} duplicates")
        if result['unrepairable'] > 0:
            print(f"\n⚠️  {result['unrepairable']} entries could not be repaired (no live match)")
            print("   Use 'traktor-cleanup' to remove them if desired")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


def cmd_library_dedup(args: argparse.Namespace) -> None:
    """Find and handle duplicate tracks in LIBRARY based on artist+title."""
    from djlib.config import load_config
    from djlib.dedup import find_duplicates_in_library, interactive_dedup
    
    dry_run = not args.write
    
    print("\n" + "=" * 60)
    print("LIBRARY DUPLICATE FINDER")
    if dry_run:
        print("         (DRY-RUN MODE)")
    else:
        print("         ⚠️  WRITE MODE - MAY MOVE FILES!")
    print("=" * 60)
    print()
    
    cfg = load_config()
    library_path = Path(cfg.get("LIB_ROOT", "")).expanduser()
    
    if not library_path or not library_path.exists():
        print("❌ Library path not configured or doesn't exist")
        print("   Run 'djlib configure' to set up paths")
        return
    
    print(f"📁 Library: {library_path}")
    print(f"🔍 Scanning for duplicates (artist + title match)...")
    print()
    
    try:
        duplicates = find_duplicates_in_library(library_path)
        
        if not duplicates:
            print("✅ No duplicates found!")
            return
        
        print(f"Found {len(duplicates)} duplicate groups.\n")
        
        stats = interactive_dedup(duplicates, dry_run=dry_run)
        
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"  Kept:    {stats['kept']}")
        print(f"  Removed: {stats['removed']}")
        print(f"  Skipped: {stats['skipped']}")
        
        if dry_run and stats['removed'] > 0:
            print(f"\n📝 Run with --write to apply changes")
            
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()


# ============ REVIEW UI ============

def cmd_review(args: argparse.Namespace) -> None:
    """Launch interactive review UI in browser.

    Serves unsorted.csv and library.csv as a sortable, filterable table
    with inline audio playback. Keyboard-driven: Space=play/pause,
    arrows=navigate, A/R/V=accept/reject/review.
    """
    try:
        from djlib.review.server import run_server
    except ImportError as e:
        print(f"\n❌  Missing dependency: {e}")
        print("   Install Flask:  pip install flask")
        return
    run_server(
        host=args.host,
        port=args.port,
        no_browser=args.no_browser,
    )


# ============ GIG PREP ============

def cmd_gig_prep(args: argparse.Namespace) -> None:
    """Parse playlist, validate, print plan (--dry-run) or copy tracks."""
    from djlib.gig import (
        GigDir,
        GigPrepLock,
        _fmt_bytes,
        estimate_total_bytes,
        parse_m3u,
        resolve_tracks,
        run_gig_prep_copy,
        validate_gig_prep,
    )
    from djlib.library_schema import load_library_csv

    m3u_path = Path(args.from_m3u)
    if not m3u_path.exists():
        print(f"ERROR: playlist not found: {m3u_path}")
        raise SystemExit(1)

    playlist_paths = parse_m3u(m3u_path)
    library_rows = load_library_csv(CSV_PATH)
    resolved = resolve_tracks(playlist_paths, library_rows)
    errors = validate_gig_prep(args.gig_id, resolved, check_files_exist=True)

    found = [r for r in resolved if r.track_id]
    not_found = [r for r in resolved if r.match_type == "not_found"]
    on_gig = [e for e in errors if e.kind == "ON_ANOTHER_GIG"]
    missing_file = [e for e in errors if e.kind == "FILE_MISSING"]
    total_bytes = estimate_total_bytes(resolved)

    print(f"\nGig prep plan: {args.gig_id}")
    print(f"  Playlist : {m3u_path} ({len(playlist_paths)} tracks)")
    print(f"  Resolved : {len(found)} found in library.csv")
    if not_found:
        print(f"  Not in library      : {len(not_found)}")
    if on_gig:
        print(f"  On another gig      : {len(on_gig)}")
    if missing_file:
        print(f"  File missing on disk: {len(missing_file)}")
    print(f"  Est. size: {_fmt_bytes(total_bytes)}")

    if errors:
        print("\n  ERRORS:")
        for e in errors:
            detail = f" ({e.detail})" if e.detail else ""
            print(f"    [{e.kind}]{detail} {e.path}")
        print()
        raise SystemExit(1)

    if args.dry_run:
        print(f"\n  Plan is clean — {len(found)} tracks ready to copy.\n")
        return

    # ── Live copy ─────────────────────────────────────────────────────────
    gig_dir = GigDir(gig_id=args.gig_id)
    gig_dir.ensure()

    lock = GigPrepLock(gig_dir.lock_path)
    if not lock.acquire():
        print(f"\nERROR: another gig-prep is already running for '{args.gig_id}'.")
        print(f"  Lock file: {gig_dir.lock_path}")
        raise SystemExit(1)

    try:
        print(f"\n  Copying {len(found)} tracks to {gig_dir.audio_dir} …")
        result = run_gig_prep_copy(
            gig_id=args.gig_id,
            resolved=resolved,
            csv_path=CSV_PATH,
            gig_dir=gig_dir,
            resume=getattr(args, "resume", False),
            source_playlist=str(m3u_path),
        )
        print(f"\n  Done.")
        print(f"    Copied   : {result.copied}")
        if result.skipped:
            print(f"    Skipped  : {result.skipped} (already verified)")
        print(f"    Committed: {result.committed}")
        if result.failed:
            print(f"    FAILED   : {result.failed}")
            raise SystemExit(1)
    finally:
        lock.release()


def cmd_gig_merge(args: argparse.Namespace) -> None:
    """Phase 3: merge post-gig Rekordbox state back to library.csv."""
    from djlib.gig import GigDir, GigMergeResult, run_gig_merge

    gig_dir = GigDir(gig_id=args.gig_id)
    dry_run = getattr(args, "dry_run", False)
    resume  = getattr(args, "resume", False)
    create_missing_dirs = getattr(args, "create_missing_dirs", False)

    print(f"\n{'[DRY RUN] ' if dry_run else ''}gig-merge: {args.gig_id}")
    print(f"  Gig dir : {gig_dir.path}")

    try:
        result: GigMergeResult = run_gig_merge(
            gig_id=args.gig_id,
            csv_path=CSV_PATH,
            gig_dir=gig_dir,
            resume=resume,
            dry_run=dry_run,
            create_missing_dirs=create_missing_dirs,
        )
    except FileNotFoundError as exc:
        print(f"\nERROR: {exc}")
        raise SystemExit(1)

    if dry_run:
        return

    print(f"\n  Done.")
    print(f"    Merged  : {result.merged}")
    if result.skipped_already_merged:
        print(f"    Skipped : {result.skipped_already_merged} (already on NAS)")
    if result.quarantined:
        print(f"    Quarant : {result.quarantined} unknown files")
    if result.conflicts:
        print(f"    Conflicts: {result.conflicts} (fresh won — see audit log)")
    if result.failed_sha:
        print(f"    FAILED (SHA mismatch) : {result.failed_sha}")
    if result.failed_nas_missing:
        print(f"    FAILED (NAS missing)  : {result.failed_nas_missing}"
              "  (re-run with --create-missing-dirs to create directories)")
    if result.failed_sha or result.failed_nas_missing:
        raise SystemExit(1)


def cmd_gig_cleanup(args: argparse.Namespace) -> None:
    """Phase 4: delete MacBook audio copies after a successful gig-merge."""
    from djlib.gig import GigDir, GigCleanupResult, run_gig_cleanup

    gig_dir = GigDir(gig_id=args.gig_id)
    dry_run    = getattr(args, "dry_run", False)
    verify_nas = getattr(args, "verify_nas", False)

    print(f"\n{'[DRY RUN] ' if dry_run else ''}gig-cleanup: {args.gig_id}")
    print(f"  Gig dir : {gig_dir.path}")
    print(f"  Audio   : {gig_dir.audio_dir}")

    try:
        result: GigCleanupResult = run_gig_cleanup(
            gig_id=args.gig_id,
            csv_path=CSV_PATH,
            gig_dir=gig_dir,
            verify_nas=verify_nas,
            dry_run=dry_run,
        )
    except FileNotFoundError as exc:
        print(f"\nERROR: {exc}")
        raise SystemExit(1)
    except Exception as exc:
        print(f"\nERROR: {type(exc).__name__}: {exc}")
        raise SystemExit(1)

    if result.not_merged:
        print(f"\nERROR: {result.not_merged} track(s) not yet on NAS — run gig-merge first.")
        raise SystemExit(1)

    print(f"\n  Done.")
    print(f"    Deleted : {result.deleted_files} file(s)")
    if result.sha_failures:
        print(f"    Kept (NAS SHA mismatch): {result.sha_failures} — investigate before deleting")
    if result.delete_failures:
        print(f"    Failed to delete: {result.delete_failures} — check file permissions")
    if result.sha_failures or result.delete_failures:
        raise SystemExit(1)


def cmd_rewind(args: argparse.Namespace) -> None:
    """Move WAV/FLAC from library back to unsorted as verified AIFF."""
    from djlib.rewind import run_rewind, RewindResult

    dry_run = getattr(args, "dry_run", False)
    resume  = getattr(args, "resume", False)

    print(f"\n{'[DRY RUN] ' if dry_run else ''}rewind: WAV/FLAC → AIFF → unsorted")
    print(f"  Library  : {CSV_PATH}")
    print(f"  Unsorted : {INBOX_DIR}")
    print(f"  Originals: {INBOX_DIR / '.originals'}")
    print()

    try:
        result: RewindResult = run_rewind(
            csv_path=CSV_PATH,
            unsorted_dir=INBOX_DIR,
            logs_dir=LOGS_DIR,
            dry_run=dry_run,
            resume=resume,
        )
    except RuntimeError as exc:
        print(f"\nERROR: {exc}")
        raise SystemExit(1)
    except Exception as exc:
        print(f"\nERROR: {type(exc).__name__}: {exc}")
        raise SystemExit(1)

    if dry_run:
        return

    print(f"\n  Done.")
    print(f"    Rewound             : {result.rewound}")
    if result.skipped_wal:
        print(f"    Skipped (resumed)   : {result.skipped_wal}")
    if result.failed_hash_mismatch:
        print(f"    Failed (hash mismatch): {result.failed_hash_mismatch}  ← audio may be corrupt")
    if result.failed_conversion:
        print(f"    Failed (conversion) : {result.failed_conversion}")
    if result.failed_other:
        print(f"    Failed (other)      : {result.failed_other}")

    if result.rewound:
        print(f"\n  Next steps:")
        print(f"    1. Import AIFF files from {INBOX_DIR} to Rekordbox")
        print(f"       (let Rekordbox analyze BPM + key before scanning)")
        print(f"    2. Run: python -m djlib.cli scan")
        print(f"    3. Review UI → apply")
        print(f"    4. Delete originals from {INBOX_DIR / '.originals'} when ready")

    if result.failed_hash_mismatch or result.failed_conversion or result.failed_other:
        raise SystemExit(1)


# ============ PARSER ============

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="djlib", description="DJ Library Manager CLI")
    sp = p.add_subparsers(dest="cmd", required=True)

    sp.add_parser("configure").set_defaults(func=cmd_configure)
    
    # Setup Beatport credentials
    sp.add_parser("setup-beatport", help="Configure Beatport credentials for API access").set_defaults(func=cmd_setup_beatport)
    
    # ========== EXTERNAL DJ SOFTWARE INTEGRATION ==========
    
    # Phase 1: Import snapshots (READ-ONLY)
    irb = sp.add_parser("import-rekordbox", help="Import Rekordbox collection snapshot (Phase 1 - READ-ONLY)")
    irb.add_argument("--out", default="LOGS/external_snapshots/rekordbox_snapshot.csv", help="Output CSV path")
    irb.add_argument("--tag-files", action="store_true", help="Write DJLIB_TRACK_ID to audio files (recommended)")
    irb.add_argument("--workers", type=int, default=4, help="Number of parallel workers for tagging (default: 4)")
    irb.set_defaults(func=cmd_import_rekordbox)
    
    itr = sp.add_parser("import-traktor", help="Import Traktor collection snapshot (Phase 1 - READ-ONLY)")
    itr.add_argument("--collection", required=True, help="Path to Traktor collection.nml")
    itr.add_argument("--out", default="LOGS/external_snapshots/traktor_snapshot.csv", help="Output CSV path")
    itr.add_argument("--tag-files", action="store_true", help="Write DJLIB_TRACK_ID to audio files (recommended)")
    itr.add_argument("--workers", type=int, default=4, help="Number of parallel workers for tagging (default: 4)")
    itr.set_defaults(func=cmd_import_traktor)
    
    # WORKFLOW 0: Sync DJ libraries with library.csv
    sdl = sp.add_parser("sync-dj-libraries", help="Sync library.csv with Rekordbox/Traktor databases (WORKFLOW 0)")
    sdl.add_argument("--write", action="store_true", help="Actually write changes (default is dry-run)")
    sdl.set_defaults(func=cmd_sync_dj_libraries)
    
    # Phase 2: Create path map (READ-ONLY)
    cpm = sp.add_parser("create-path-map", help="Create path mapping from move log + snapshots (Phase 2 - READ-ONLY)")
    cpm.add_argument("--move-log", required=True, help="Path to move log from 'apply' command")
    cpm.add_argument("--rekordbox-snapshot", default=None, help="Path to Rekordbox snapshot CSV")
    cpm.add_argument("--traktor-snapshot", default=None, help="Path to Traktor snapshot CSV")
    cpm.add_argument("--out", default=None, help="Output path map CSV (auto-generated if not specified)")
    cpm.set_defaults(func=cmd_create_path_map)
    
    # Phase 3: Sync paths (WRITE - NOT YET IMPLEMENTED)
    srb = sp.add_parser("sync-rekordbox-paths", help="Sync paths to Rekordbox DB (Phase 3 - NOT IMPLEMENTED)")
    srb.set_defaults(func=cmd_sync_rekordbox)
    
    str_parser = sp.add_parser("sync-traktor-paths", help="Sync paths to Traktor collection.nml (Phase 3 - NOT IMPLEMENTED)")
    str_parser.set_defaults(func=cmd_sync_traktor)
    
    # Add tracks to Traktor (NEW)
    att = sp.add_parser("add-to-traktor", help="Add tracks from unsorted.csv to Traktor collection.nml")
    att.add_argument("--collection", required=True, help="Path to Traktor collection.nml")
    att.add_argument("--write", action="store_true", help="Actually write changes (default is dry-run)")
    att.set_defaults(func=cmd_add_to_traktor)
    
    # Add tracks to Rekordbox (NEW)
    arb = sp.add_parser("add-to-rekordbox", help="Add tracks from unsorted.csv to Rekordbox database")
    arb.add_argument("--write", action="store_true", help="Actually write changes (default is dry-run)")
    arb.set_defaults(func=cmd_add_to_rekordbox)
    
    # Remove duplicate entries from Traktor
    tdd = sp.add_parser("traktor-dedup", help="Find and remove duplicate entries in Traktor collection")
    tdd.add_argument("--write", action="store_true", help="Actually remove duplicates (default is dry-run)")
    tdd.set_defaults(func=cmd_traktor_dedup)
    
    # Remove dead entries (missing files) from Traktor
    tcl = sp.add_parser("traktor-cleanup", help="Find and remove dead entries (missing files) from Traktor")
    tcl.add_argument("--write", action="store_true", help="Actually remove dead entries (default is dry-run)")
    tcl.set_defaults(func=cmd_traktor_cleanup)
    
    # Repair dead entries by finding live duplicates
    trp = sp.add_parser("traktor-repair", help="Repair dead entries by finding live duplicates (preserves play history)")
    trp.add_argument("--write", action="store_true", help="Actually repair entries (default is dry-run)")
    trp.set_defaults(func=cmd_traktor_repair)
    
    # Library deduplication by quality
    ldp = sp.add_parser("library-dedup", help="Find and handle duplicate tracks in LIBRARY (artist+title match)")
    ldp.add_argument("--write", action="store_true", help="Actually move files (default is dry-run)")
    ldp.set_defaults(func=cmd_library_dedup)
    
    # ========== END EXTERNAL INTEGRATION ==========
    
    scan_parser = sp.add_parser("scan", help="Scan UNSORTED folder for new tracks")
    scan_parser.add_argument(
        "--strict",
        action="store_true",
        help="Require Rekordbox DB confirmation (reject tag-only files from Traktor/Serato)"
    )
    scan_parser.set_defaults(func=cmd_scan)

    dedup_parser = sp.add_parser(
        "dedup-staging",
        help="Deduplicate unsorted.csv: merge duplicate rows scanned before automatic dedup was added",
    )
    dedup_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    dedup_parser.set_defaults(func=cmd_dedup_staging)

    # REMOVED: auto-decide parser (legacy bucketing system)

    ap2 = sp.add_parser("apply")
    ap2.add_argument("--dry-run", action="store_true")
    ap2.add_argument(
        "--allow-no-rekordbox",
        action="store_true",
        help=(
            "Move tracks to library even when Rekordbox has not seen them yet. "
            "BPM/Key fall back to audio tags and the row is marked "
            "analysis_source=tags, ready_for_rekordbox=false. Without this "
            "flag, tracks missing rekordbox_id are skipped (default behavior)."
        ),
    )
    ap2.set_defaults(func=cmd_apply)

    sp.add_parser("undo").set_defaults(func=cmd_undo)
    sp.add_parser("dupes").set_defaults(func=cmd_dupes)
    sp.add_parser("refresh-staging", help="Recalculate final_filename after manual edits").set_defaults(func=cmd_refresh_staging)
    fud = sp.add_parser("fix-unsorted-dupes", help="Remove duplicate entries from unsorted.csv")
    fud.add_argument("--write", action="store_true", help="Actually remove duplicates (default is dry-run)")
    fud.set_defaults(func=cmd_fix_unsorted_dupes)
    sap = sp.add_parser("sync-audio-metrics")
    sap.add_argument("--force", action="store_true")
    sap.add_argument("--write-tags", action="store_true", help="Zapisz metadane (BPM/Key) do plików audio")
    sap.set_defaults(func=cmd_sync_audio_metrics)
    sp.add_parser("fix-fingerprints").set_defaults(func=cmd_fix_fingerprints)
    sp.add_parser("fix-filenames").set_defaults(func=cmd_fix_titles_from_filenames)
    ep = sp.add_parser("enrich-online")
    ep.add_argument("--force-genres", action="store_true", help="Nadpisz kolumny genres_musicbrainz/lastfm nawet jeśli już wypełnione")
    ep.add_argument("--skip-soundcloud", action="store_true", help="Legacy no-op: enrich-online no longer uses the SoundCloud API")
    ep.set_defaults(func=cmd_enrich_online)

    # analyze-audio
    aap = sp.add_parser("analyze-audio")
    aap.add_argument("--path", default=str(INBOX_DIR), help="Ścieżka pliku lub folderu (domyślnie INBOX)")
    aap.add_argument("--check-env", action="store_true", help="Sprawdź środowisko Essentia")
    aap.add_argument("--recompute", action="store_true", help="Pomiń cache i przelicz na nowo")
    aap.add_argument("--workers", type=int, default=1, help="Liczba workerów (na razie ignorowane; skeleton)")
    aap.add_argument("--target-bpm", default="80:180", help="Zakres docelowy BPM, np. 80:180")
    aap.set_defaults(func=cmd_analyze_audio)

    # ml predict
    mp = sp.add_parser("ml-predict")
    mp.add_argument("--model", default=str(REPO_ROOT / "models" / "bucket_model.pkl"))
    mp.add_argument("--path", default=str(INBOX_DIR), help="Plik lub folder (domyślnie INBOX)")
    mp.add_argument("--recompute", action="store_true", help="Przelicz analizę audio na nowo")
    mp.add_argument("--set-target", action="store_true", help="Ustawiaj docelowy kubełek powyżej progu hard")
    mp.add_argument("--suggest", action="store_true", help="Ustawiaj tylko ai_guess_* powyżej progu suggest")
    mp.add_argument("--hard-threshold", type=float, default=0.85)
    mp.add_argument("--suggest-threshold", type=float, default=0.65)
    mp.add_argument("--min-confidence", type=float, default=0.40, help="Nie zapisuj żadnych sugestii poniżej tego progu")
    mp.set_defaults(func=cmd_ml_predict)

    # ml-train-local: trenuj model na Twoich zaakceptowanych bucketach
    tl = sp.add_parser("ml-train-local")
    tl.add_argument("--min-per-class", type=int, default=20, help="Minimalna liczba próbek na klasę (odfiltruj rzadkie)")
    tl.add_argument("--limit", type=int, default=None, help="Limit próbek do szybkiego treningu (opcjonalnie)")
    tl.add_argument("--recompute", action="store_true", help="Przelicz analizę Essentia jeśli brak w cache")
    tl.add_argument("--out", default=str(REPO_ROOT / "models" / "bucket_model.pkl"))
    tl.set_defaults(func=cmd_ml_train_local)

    ds = sp.add_parser("ml-export-training-dataset")
    ds.add_argument("--out", default=str(REPO_ROOT / "data" / "training_dataset_full.csv"))
    ds.add_argument("--require-both-labels", action="store_true", help="Uwzględnij tylko rekordy z kompletnymi etykietami")
    ds.set_defaults(func=cmd_ml_export_dataset)

    # QA: acceptance rate
    qa = sp.add_parser("qa-acceptance")
    qa.add_argument("--min-confidence", type=float, default=0.65, help="Licz tylko predykcje powyżej tego progu")
    qa.set_defaults(func=cmd_qa_acceptance)

    # Genre resolution commands
    # genres resolve (single lookup)
    gp = sp.add_parser("genres")
    gsp = gp.add_subparsers(dest="subcmd", required=True)
    res = gsp.add_parser("resolve")
    res.add_argument("--artist", required=True)
    res.add_argument("--title", required=True)
    res.add_argument("--duration", type=int, default=None, help="Duration in seconds (optional)")
    res.add_argument("--version", default="", help="Version/remix info to improve SoundCloud lookup")
    res.set_defaults(func=cmd_genres_resolve)

    # REMOVED: detect-taxonomy and taxonomy-backup commands (legacy bucketing system)
    
    # Import MusicBrainz Canonical Data dump
    icd = sp.add_parser("import-canonical-dump", help="Import MusicBrainz Canonical Data dump to SQLite")
    icd.add_argument("--dump", required=False, help="Path to .tar.zst dump file (auto-detect if not provided)")
    icd.add_argument("--force", action="store_true", help="Rebuild database even if it exists")
    icd.set_defaults(func=cmd_import_canonical_dump)

    # ========== GIG PREP ==========
    gig = sp.add_parser("gig-prep", help="Prepare a gig: copy tracks from NAS to MacBook (Phase 2)")
    gig.add_argument("gig_id", help="Unique gig identifier, e.g. friday-2026-05-15")
    gig.add_argument("--from-m3u", required=True, metavar="PATH", help="Path to M3U/M3U8 playlist file")
    gig.add_argument("--dry-run", action="store_true", help="Print plan without copying anything")
    gig.add_argument("--resume", action="store_true", help="Resume interrupted prep (skips already-verified tracks)")
    gig.set_defaults(func=cmd_gig_prep)

    merge = sp.add_parser("gig-merge", help="Merge post-gig Rekordbox state back to library.csv (Phase 3)")
    merge.add_argument("gig_id", help="Gig identifier used during gig-prep, e.g. friday-2026-05-15")
    merge.add_argument("--dry-run", action="store_true", help="Print plan without writing anything")
    merge.add_argument("--resume", action="store_true", help="Resume interrupted merge (skips already-merged tracks)")
    merge.add_argument("--create-missing-dirs", action="store_true",
                       help="Create NAS parent directories if missing (default: abort track)")
    merge.set_defaults(func=cmd_gig_merge)

    cleanup = sp.add_parser("gig-cleanup", help="Delete MacBook audio copies after a successful gig-merge (Phase 4)")
    cleanup.add_argument("gig_id", help="Gig identifier used during gig-prep, e.g. friday-2026-05-15")
    cleanup.add_argument("--dry-run", action="store_true", help="Print what would be deleted without deleting")
    cleanup.add_argument("--verify-nas", action="store_true",
                         help="SHA-256 verify NAS copy before deleting each MacBook file")
    cleanup.set_defaults(func=cmd_gig_cleanup)

    rewind = sp.add_parser(
        "rewind",
        help="Move WAV/FLAC from library back to unsorted as verified AIFF (for re-scan)",
    )
    rewind.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be converted and moved without doing anything",
    )
    rewind.add_argument(
        "--resume", action="store_true",
        help="Resume an interrupted rewind using the existing WAL",
    )
    rewind.set_defaults(func=cmd_rewind)

    # ========== REVIEW UI ==========
    rev = sp.add_parser("review", help="Open track review UI in browser (Space=play, A/R/V=accept/reject/review)")
    rev.add_argument("--host", default="127.0.0.1", help="Server host (default: 127.0.0.1)")
    rev.add_argument("--port", type=int, default=8899, help="Server port (default: 8899)")
    rev.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    rev.set_defaults(func=cmd_review)

    return p

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
