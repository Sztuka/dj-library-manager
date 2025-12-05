from __future__ import annotations
import argparse, csv, time, os, json, shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List
import warnings

# Suppress Python 3.13 deprecation warnings from audioread (aifc/sunau modules)
warnings.filterwarnings("ignore", category=DeprecationWarning, module="audioread")

# --- Core importy (nasze moduły) ---
from djlib.config import (
    reconfigure, ensure_base_dirs, CONFIG_FILE,
    INBOX_DIR, LOGS_DIR, CSV_PATH, AUDIO_EXTS, UNSORTED_XLSX
)
from djlib.csvdb import load_records, save_records
from djlib.tags import read_tags, write_tags
from djlib.rekordbox_status import was_analyzed, extract_metadata_from_db
from djlib.enrich import suggest_metadata, enrich_online_for_row, derive_local_metadata
from djlib.metadata.genre_resolver import resolve as resolve_genres
from djlib.fingerprint import file_sha256, fingerprint_info
from djlib.filename import build_final_filename, extension_for, split_title_and_version, merge_title_and_version
from djlib.mover import resolve_target_path, move_with_rename, utc_now_str
from djlib.ml.export_dataset import export_training_dataset
from djlib.tag_cleaner import clean_tags
from djlib.unsorted import load_unsorted_rows, write_unsorted_rows, is_done
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
    return load_unsorted_rows(UNSORTED_XLSX)


def _save_unsorted(rows: List[Dict[str, str]]) -> None:
    """Save rows to unsorted.xlsx."""
    # No longer needs bucket choices (legacy system removed)
    write_unsorted_rows(UNSORTED_XLSX, rows, [])

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
        return  # Abort without generating unsorted.xlsx
    
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

    new_rows: List[Dict[str, str]] = []
    for p in all_files:
        fhash = file_sha256(p)
        if fhash in known_hashes:
            processed += 1
            _write_status(
                {
                    "state": "running",
                    "total": total,
                    "processed": processed,
                    "added": added,
                    "errors": errors,
                    "last_file": str(p),
                }
            )
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

        is_dup = "true" if (fp and fp in known_fps) else "false"

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
            current_rekordbox_id = rekordbox_mapping.get(p, '')
            current_traktor_id = traktor_mapping.get(p, '')
            
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
            rekordbox_id = rekordbox_mapping.get(p, '')
            traktor_id = traktor_mapping.get(p, '')
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
            "is_duplicate": is_dup,
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
            "done": "FALSE",
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
        added += 1
        processed += 1
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

    if new_rows:
        _save_unsorted(staging_rows)
        print(f"Zeskanowano {len(new_rows)} plików. Zapisano {UNSORTED_XLSX}.")
    else:
        print("Brak nowych plików do dodania.")

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
# Auto-bucketing logic is no longer relevant. Use manual genre selection in unsorted.xlsx instead.

def cmd_enrich_online(args: argparse.Namespace) -> None:
    """Wzbogaca metadane (suggest_*) dla pozycji pending korzystając z MusicBrainz/AcoustID/Last.fm (+ SoundCloud).
    Prowadzi status w LOGS/enrich_status.json, aby UI mogło pokazywać postęp.
    Nie nadpisuje już zaakceptowanych. Nie zmienia BPM/Key.
    
    Opcjonalnie pobiera okładki albumów (--fetch-covers) z MusicBrainz/Last.fm/SoundCloud.
    """
    rows = _load_unsorted()
    force_genres = bool(getattr(args, "force_genres", False))
    fetch_covers = bool(getattr(args, "fetch_covers", False))
    todo = [r for r in rows if not is_done(r.get("done"))]
    total = len(todo)
    processed = 0
    changed = 0
    mb_set = 0
    lfm_set = 0
    covers_added = 0
    covers_skipped = 0
    covers_failed = 0
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

    _flush_status()

    # Beatport token validation with auto-refresh
    beatport_available = True
    try:
        from djlib.metadata.beatport import get_valid_token
        # Attempt to get valid token (triggers auto-refresh if expired)
        token = get_valid_token()
        print("✅ Beatport: Token ready")
    except Exception as e:
        beatport_available = False
        error_msg = str(e)
        print(f"\n⚠️  Beatport: {error_msg}")
        
        # Provide helpful guidance based on error type
        if "credentials" in error_msg.lower() or "missing" in error_msg.lower():
            print(f"   Setup: python -m djlib.cli setup-beatport")
        
        # Only prompt if user hasn't already set skip flag
        if not getattr(args, "skip_beatport", False):
            _flush_status()
            try:
                choice = input("Kontynuować bez Beatport? [Y/n]: ").strip().lower()
            except Exception:
                choice = "y"
            
            if choice in {"n", "no"}:
                print("Przerwano na prośbę użytkownika.")
                status_doc["state"] = "done"
                status_doc["completed_at"] = _now_iso()
                _flush_status()
                return
            else:
                print("→ Pomiń Beatport w tym przebiegu.")
                setattr(args, "skip_beatport", True)
    _flush_status()

    # SoundCloud client id health (informative, does not block)
    sc_health_msg = ""
    try:
        from djlib.metadata.soundcloud import client_id_status
        h = client_id_status()
        if h:
            status_doc["soundcloud"]["client_id_status"] = h.get("status", "unknown")
        sc_health_msg = f"soundcloud_client_id_status={h.get('status')}" if h else ""
        if h and h.get("status") == "ok":
            print(f"✅ SoundCloud: {h.get('message')}")
            if not getattr(args, "skip_soundcloud", False):
                status_doc["soundcloud"]["decision"] = "active"
        elif h and h.get("status") == "expired":
            print(f"ℹ SoundCloud: {h.get('message')} - auto-refresh dostępny")
            # Auto-refresh will happen automatically when needed
            if not getattr(args, "skip_soundcloud", False):
                status_doc["soundcloud"]["decision"] = "active"
        elif h and h.get("status") in {"invalid", "error"}:
            print(f"⚠ SoundCloud client_id: {h.get('message')}")
            if getattr(args, "skip_soundcloud", False):
                status_doc["soundcloud"]["decision"] = "skipped"
            else:
                status_doc["soundcloud"]["prompt_shown"] = True
                _flush_status()
                try:
                    choice = input("Kontynuować bez SoundCloud? [Y/n]: ").strip().lower()
                except Exception:
                    choice = "y"
                if choice in {"n", "no"}:
                    print("Przerwano na prośbę użytkownika (SoundCloud invalid).")
                    status_doc["soundcloud"]["decision"] = "aborted"
                    status_doc["state"] = "done"
                    status_doc["completed_at"] = _now_iso()
                    _flush_status()
                    return
                else:
                    print("→ Pomiń SoundCloud w tym przebiegu.")
                    setattr(args, "skip_soundcloud", True)
                    status_doc["soundcloud"]["decision"] = "skipped"
        elif h and h.get("status") == "missing":
            if getattr(args, "skip_soundcloud", False):
                status_doc["soundcloud"]["decision"] = "skipped"
            else:
                print("ℹ Brak SoundCloud client_id (można ustawić DJLIB_SOUNDCLOUD_CLIENT_ID).")
                status_doc["soundcloud"]["decision"] = "skipped"  # treat missing as skipped
    except Exception:
        pass
    _flush_status()

    for r in rows:
        if is_done(r.get("done")):
            continue
        p = Path(r.get("file_path",""))
        online = enrich_online_for_row(p, r)
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
            if k in {"artist_suggest","title_suggest","version_suggest","genre_suggest","album_suggest","year_suggest","duration_suggest"}:
                cur = (r.get(k) or "").strip()
                if (not cur and v) or (allow_override and v and cur != v):
                    r[k] = v
                    any_change = True
        # ustaw meta_source jeśli zrobiliśmy jakąkolwiek aktualizację i online podał źródło
        if any_change and (online.get("meta_source") or "").strip():
            r["meta_source"] = online["meta_source"]
        
    # Zawsze spróbuj wzbogacić gatunki używając wszystkich źródeł (MB + Last.fm + SoundCloud)
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
            
            from djlib.metadata.genre_resolver import resolve as resolve_genres
            genre_res = resolve_genres(
                a,
                t,
                version=v,
                duration_s=dur_s,
                disable_soundcloud=bool(getattr(args, "skip_soundcloud", False)),
                disable_beatport=bool(getattr(args, "skip_beatport", False)),
            )
            if genre_res and genre_res.confidence >= 0.03:  # lower threshold for missing genres
                # Ustaw 3 gatunki: main + subs
                genres = [genre_res.main] + genre_res.subs[:2]  # max 3 total
                genre_str = ", ".join(genres)
                current_genre = (r.get("genre_suggest") or "").strip()
                # Override existing genre if: force_genres flag, or no current genre, or significantly better confidence
                if force_genres or not current_genre or genre_res.confidence > 0.08:
                    r["genre_suggest"] = genre_str
                    any_change = True
                    # Update meta_source to reflect all sources used
                    sources = [src for src, _, _ in genre_res.breakdown]
                    if sources:
                        r["meta_source"] = f"{r.get('meta_source', '')}+genres({','.join(sources)})".strip("+")

                # Zapisz surowe listy tagów per źródło do dodatkowych kolumn
                try:
                    src_map = {src: local for (src, _, local) in genre_res.breakdown}
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
        
        # Fetch cover art if --fetch-covers flag is set
        if fetch_covers:
            try:
                from djlib.metadata.coverart import fetch_cover_art
                from djlib.config import get_lastfm_api_key
                from djlib.metadata.soundcloud import get_valid_client_id
                
                # Get API keys
                lastfm_key = get_lastfm_api_key()
                soundcloud_id = get_valid_client_id() if not getattr(args, "skip_soundcloud", False) else None
                
                # Extract metadata for cover fetching
                artist = (r.get("artist_suggest") or r.get("artist") or "").strip()
                title = (r.get("title_suggest") or r.get("title") or "").strip()
                album = (r.get("album_suggest") or r.get("album") or "").strip()
                version = (r.get("version_suggest") or r.get("version_info") or "").strip()
                
                # Try to get MusicBrainz release_group_id from online enrichment
                release_group_id = online.get("release_group_id") if online else None
                
                # Try to get Beatport artwork URL
                beatport_artwork_url = None
                if not getattr(args, "skip_beatport", False):
                    try:
                        from djlib.metadata.beatport import search_track as bp_search
                        dur_s = None
                        if r.get("duration_suggest"):
                            try:
                                dur_parts = r["duration_suggest"].split(":")
                                if len(dur_parts) == 2:
                                    dur_s = int(dur_parts[0]) * 60 + int(dur_parts[1])
                            except Exception:
                                pass
                        bp_result = bp_search(artist, title, dur_s)
                        if bp_result:
                            beatport_artwork_url = bp_result.get("artwork_url")
                    except Exception:
                        pass
                
                if artist and title:
                    success, source = fetch_cover_art(
                        filepath=str(p),
                        artist=artist,
                        album=album,
                        title=title,
                        version=version,
                        release_group_id=release_group_id,
                        beatport_artwork_url=beatport_artwork_url,
                        lastfm_api_key=lastfm_key,
                        soundcloud_client_id=soundcloud_id,
                        skip_if_exists=True,
                        disable_beatport=getattr(args, "skip_beatport", False)
                    )
                    
                    if source == 'exists':
                        covers_skipped += 1
                    elif success:
                        covers_added += 1
                        print(f"   🎨 {p.name}: okładka dodana ({source})")
                    else:
                        covers_failed += 1
            except Exception as e:
                covers_failed += 1
                print(f"   ⚠ Cover art error for {p.name}: {e}")
        
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
    
    if changed or genre_mapped or genre_unmapped:
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
    # Uzupełnij attempted_requests z modułu SoundCloud
    try:
        from djlib.metadata.soundcloud import soundcloud_request_count
        status_doc["soundcloud"]["attempted_requests"] = soundcloud_request_count()
    except Exception:
        pass
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
    if fetch_covers:
        print(f"🎨 Okładki: added={covers_added}, skipped={covers_skipped}, failed={covers_failed}")
    if not _lfm_key_present:
        print("   ⚠ Brak LASTFM_API_KEY (DJLIB_LASTFM_API_KEY) — kolumna genres_lastfm może pozostać pusta.")
    if sc_health_msg:
        print(f"   ℹ {sc_health_msg}")

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
        if is_done(r.get("done")):
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
        if is_done(r.get("done")):
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
    """Apply approved changes from unsorted.xlsx.
    
    New model: Uses status/destination columns (library/reject/archive/mixes).
    Legacy model: Falls back to target_subfolder if destination is empty.
    """
    from djlib.logistics import build_library_path, build_reject_path, build_archive_path, build_mixes_path
    
    rows = _load_unsorted()
    ready = [r for r in rows if is_done(r.get("done"))]
    if not ready:
        print("Brak wierszy z oznaczeniem done=TRUE.")
        return
    library_rows = load_records(CSV_PATH)
    processed_ids: set[str] = set()
    tags_written = 0
    tags_errors = 0
    tags_cleaned = 0
    tags_clean_errors = 0

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = LOGS_DIR / f"moves-{stamp}.csv"
    log_rows = []
    
    # Get current DJ software mappings for ID validation
    from djlib.external_sync import get_rekordbox_track_ids, get_traktor_track_ids
    rekordbox_mapping = get_rekordbox_track_ids()
    traktor_mapping = get_traktor_track_ids()

    for r in ready:
        # Determine destination path (new model or legacy fallback)
        destination = (r.get("destination") or "").lower().strip()
        target_subfolder = (r.get("target_subfolder") or "").strip()
        
        src = Path(r.get("file_path") or "")
        if not src.exists():
            print(f"[WARN] Nie znaleziono pliku: {src}")
            continue
        
        # ✅ VALIDATE & FIX DJ software IDs before moving
        current_rekordbox_id = rekordbox_mapping.get(src, '')
        current_traktor_id = traktor_mapping.get(src, '')
        
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
        
        # Determine destination path
        dest_path: Path | None = None
        
        if destination == "library":
            dest_path = build_library_path(artist, final_name)
        elif destination == "reject":
            dest_path = build_reject_path(final_name)
        elif destination == "archive":
            dest_path = build_archive_path(artist, final_name)
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

        # Ensure parent directory exists and handle naming conflicts
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        if dest_path.exists():
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
        
        # Update record
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
        
        # Update existing record or append new one (avoid duplicates by track_id)
        existing_idx = None
        for idx, lib_row in enumerate(library_rows):
            if lib_row.get("track_id") == record["track_id"]:
                existing_idx = idx
                break
        
        if existing_idx is not None:
            # Update existing record (file was moved/renamed)
            library_rows[existing_idx] = record
        else:
            # Add new record
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
            
            # ALWAYS clear album tag (DJ libraries don't need compilation names)
            updates["album"] = ""
            
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
        print(f"[DRY-RUN] Gotowe do eksportu: {len(ready)} (oznaczone done=TRUE).")
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
    print(f"Przeniesiono {len(processed_ids)} pozycji do biblioteki.")
    print(f"🧹 Czyszczenie spam tagów: cleaned={tags_cleaned}, errors={tags_clean_errors}")
    print(f"📀 Zapis tagów audio: ok={tags_written}, errors={tags_errors}")
    
    # Auto-sync with DJ software libraries (Rekordbox + Traktor)
    if not args.dry_run and processed_ids:
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

def cmd_sync_audio_metrics(args: argparse.Namespace) -> None:
    """DEPRECATED: Essentia analysis is now cache-only and does not write to tags or unsorted.xlsx.
    
    BPM/Key in unsorted.xlsx come from Rekordbox tags only.
    Please analyze files in Rekordbox before running scan workflow.
    
    This command has been disabled to maintain data integrity.
    """
    print("❌ DEPRECATED: sync-audio-metrics command is no longer available.")
    print()
    print("   Essentia analysis is cache-only (for ML training features).")
    print("   BPM/Key in unsorted.xlsx must come from Rekordbox tags.")
    print()
    print("   Please:")
    print("   1. Analyze your files in Rekordbox (sets TBPM/TKEY tags)")
    print("   2. Run: python -m djlib.cli scan")
    print("   3. Optionally run: python -m djlib.cli analyze-audio (for ML features)")
    print()
    return

def cmd_genres_resolve(args: argparse.Namespace) -> None:
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
    for src, _, local in res.breakdown:
        parts = ", ".join(f"{k}:{v:.2f}" for k, v in sorted(local.items(), key=lambda kv: kv[1], reverse=True)[:5])
        print(f"  - {src}: {parts}")

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
    
    # NOTE: Essentia analysis is cache-only. BPM/Key in unsorted.xlsx come from Rekordbox tags only.
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
            
            # Merge duplicates intelligently - combine rekordbox_id + traktor_id for same files
            df['old_full_path'] = df['old_full_path'].fillna('').astype(str)
            df['old_full_path_norm'] = df['old_full_path'].str.strip().map(
                lambda p: os.path.normpath(p) if p else ''
            )
            
            # Group by normalized path to merge IDs
            before_merge = len(df)
            merged_rows = []
            
            for path, group in df.groupby('old_full_path_norm'):
                if path == '':  # Skip empty paths
                    continue
                
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
            
            df = pd.DataFrame(merged_rows)
            df = df.drop(columns=['old_full_path_norm'])
            duplicates_merged = before_merge - len(df)
            
            CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(CSV_PATH, index=False)
            print(f"✅ Merged {len(df)} unique tracks into library.csv")
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
        
        def tag_file(row):
            """Tag a single file with DJLIB metadata."""
            file_path_str = row.get('old_full_path', '') or row.get('file_path', '')
            if not file_path_str:
                return 'skip', None
            
            file_path = Path(file_path_str)
            if not file_path.exists():
                return 'skip', file_path.name
            
            track_id = row.get('track_id', '')
            if not track_id:
                return 'skip', file_path.name
            
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
                elif status == 'error':
                    tags_errors += 1
                    if result:  # result is tuple (filename, error)
                        filename, error = result
                        if tags_errors <= 3:  # Show first 3 errors
                            print(f"  ⚠️  Failed: {filename}: {error}")
        
        print()
        print(f"✅ Tags written: {tags_written}")
        print(f"⏭️  Skipped (file not found): {tags_skipped}")
        if tags_errors > 0:
            print(f"❌ Errors: {tags_errors}")
    
    # Step 3: Sync ratings to DJ software
    print()
    print("=" * 60)
    print("STEP 3: SYNC RATINGS TO DJ SOFTWARE")
    print("=" * 60)
    print()
    
    from djlib.external_sync import sync_ratings_to_dj_software
    
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
    Add tracks from unsorted.xlsx to Traktor collection.nml.
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
    
    # Load tracks from unsorted.xlsx
    staging_rows = _load_unsorted()
    if not staging_rows:
        print("❌ No tracks in unsorted.xlsx")
        return
    
    print(f"📋 Found {len(staging_rows)} tracks in unsorted.xlsx")
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
    Add tracks from unsorted.xlsx to Rekordbox database.
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
    
    # Load tracks from unsorted.xlsx
    staging_rows = _load_unsorted()
    if not staging_rows:
        print("❌ No tracks in unsorted.xlsx")
        return
    
    print(f"📋 Found {len(staging_rows)} tracks in unsorted.xlsx")
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
    att = sp.add_parser("add-to-traktor", help="Add tracks from unsorted.xlsx to Traktor collection.nml")
    att.add_argument("--collection", required=True, help="Path to Traktor collection.nml")
    att.add_argument("--write", action="store_true", help="Actually write changes (default is dry-run)")
    att.set_defaults(func=cmd_add_to_traktor)
    
    # Add tracks to Rekordbox (NEW)
    arb = sp.add_parser("add-to-rekordbox", help="Add tracks from unsorted.xlsx to Rekordbox database")
    arb.add_argument("--write", action="store_true", help="Actually write changes (default is dry-run)")
    arb.set_defaults(func=cmd_add_to_rekordbox)
    
    # ========== END EXTERNAL INTEGRATION ==========
    
    scan_parser = sp.add_parser("scan", help="Scan UNSORTED folder for new tracks")
    scan_parser.add_argument(
        "--strict",
        action="store_true",
        help="Require Rekordbox DB confirmation (reject tag-only files from Traktor/Serato)"
    )
    scan_parser.set_defaults(func=cmd_scan)

    # REMOVED: auto-decide parser (legacy bucketing system)

    ap2 = sp.add_parser("apply")
    ap2.add_argument("--dry-run", action="store_true")
    ap2.set_defaults(func=cmd_apply)

    sp.add_parser("undo").set_defaults(func=cmd_undo)
    sp.add_parser("dupes").set_defaults(func=cmd_dupes)
    sap = sp.add_parser("sync-audio-metrics")
    sap.add_argument("--force", action="store_true")
    sap.add_argument("--write-tags", action="store_true", help="Zapisz metadane (BPM/Key) do plików audio")
    sap.set_defaults(func=cmd_sync_audio_metrics)
    sp.add_parser("fix-fingerprints").set_defaults(func=cmd_fix_fingerprints)
    sp.add_parser("fix-filenames").set_defaults(func=cmd_fix_titles_from_filenames)
    ep = sp.add_parser("enrich-online")
    ep.add_argument("--force-genres", action="store_true", help="Nadpisz kolumny genres_musicbrainz/lastfm nawet jeśli już wypełnione")
    ep.add_argument("--skip-soundcloud", action="store_true", help="Pomiń źródło SoundCloud nawet jeśli client_id jest ustawiony")
    ep.add_argument("--fetch-covers", action="store_true", help="Pobierz okładki albumów (MusicBrainz → Last.fm → SoundCloud)")
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

    # XLSX export/import z dropdownem na bucket
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
    
    return p

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
