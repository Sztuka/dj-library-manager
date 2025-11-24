from __future__ import annotations
import argparse, csv, time, os, json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

# --- Core importy (nasze moduły) ---
from djlib.config import (
    reconfigure, ensure_base_dirs, CONFIG_FILE,
    INBOX_DIR, READY_TO_PLAY_DIR, REVIEW_QUEUE_DIR, LOGS_DIR, CSV_PATH, AUDIO_EXTS, UNSORTED_XLSX
)
from djlib.csvdb import load_records, save_records
from djlib.tags import read_tags, write_tags
from djlib.rekordbox_status import was_analyzed
from djlib.enrich import suggest_metadata, enrich_online_for_row, derive_local_metadata
from djlib.genre import external_genre_votes, load_taxonomy_map, suggest_bucket_from_votes
from djlib.metadata.genre_resolver import resolve as resolve_genres
from djlib.classify import guess_bucket
from djlib.fingerprint import file_sha256, fingerprint_info
from djlib.filename import build_final_filename, extension_for, split_title_and_version, merge_title_and_version
from djlib.mover import resolve_target_path, move_with_rename, utc_now_str
from djlib.buckets import is_valid_target
from djlib.placement import decide_bucket
from djlib.ml.export_dataset import export_training_dataset
from djlib.tag_cleaner import clean_tags
from djlib.taxonomy import load_taxonomy, allowed_targets
from djlib.unsorted import load_unsorted_rows, write_unsorted_rows, is_done
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
    try:
        choices = allowed_targets()
    except Exception:
        choices = []
    write_unsorted_rows(UNSORTED_XLSX, rows, choices)

# ============ KOMENDY ============

def cmd_configure(_: argparse.Namespace) -> None:
    cfg, path = reconfigure()
    ensure_base_dirs()
    print(f"\n✅ Zapisano konfigurację do: {path}")
    print(f"   library_root: {cfg.library_root}")
    print(f"   inbox_dir:    {cfg.inbox_dir}\n")

def cmd_scan(args: argparse.Namespace) -> None:
    ensure_base_dirs()
    strict = getattr(args, "strict", False)
    library_rows = load_records(CSV_PATH)
    staging_rows = _load_unsorted()
    known_hashes = {r.get("file_hash", "") for r in library_rows if r.get("file_hash")}
    known_fps = {r.get("fingerprint", "") for r in library_rows if r.get("fingerprint")}
    known_hashes.update({r.get("file_hash", "") for r in staging_rows if r.get("file_hash")})
    known_fps.update({r.get("fingerprint", "") for r in staging_rows if r.get("fingerprint")})

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

        ai_bucket, ai_comment = guess_bucket(
            tags["artist"], tags["title"], tags["bpm"], tags["genre"], tags["comment"]
        )

        sugg = suggest_metadata(p, tags)
        if (sugg.get("duration_suggest") or "").strip() == "" and dur:
            mm = dur // 60
            ss = dur % 60
            sugg["duration_suggest"] = f"{mm}:{ss:02d}"

        track_id = f"{fhash[:12]}_{int(time.time())}"
        rec: Dict[str, str] = {
            "track_id": track_id,
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
            "ai_guess_bucket": _safe_str(ai_bucket),
            "ai_guess_comment": _safe_str(ai_comment),
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

def _load_rules(path: Path) -> Dict[str, Any]:
    import yaml
    if not path.exists():
        return {"rules": [], "fallbacks": {}}
    with path.open("r", encoding="utf-8") as f:
        return (yaml.safe_load(f) or {"rules": [], "fallbacks": {}})

def _decide_for_row(row: Dict[str, str], rules: Dict[str, Any]) -> str:
    artist = (row.get("artist") or "").lower()
    title  = (row.get("title") or "").lower()
    genre  = (row.get("genre") or "").lower()
    comm   = (row.get("ai_guess_comment") or row.get("comment") or "").lower()
    haystack = " ".join([artist, title, genre, comm])

    for rule in rules.get("rules", []):
        words = [w.lower() for w in rule.get("contains", [])]
        if any(w in haystack for w in words):
            return rule.get("target", "")

    fb = rules.get("fallbacks", {})
    guess = row.get("ai_guess_bucket") or ""
    if guess in fb:
        return fb[guess]
    return fb.get("default", "REVIEW QUEUE/UNDECIDED")

def cmd_auto_decide(args: argparse.Namespace) -> None:
    rules_path = Path(args.rules or (REPO_ROOT / "rules.yml"))
    rules = _load_rules(rules_path)
    rows = _load_unsorted()
    updated = 0

    for r in rows:
        if is_done(r.get("done")):
            continue
        if args.only_empty and (r.get("target_subfolder") or "").strip():
            continue
        proposal = _decide_for_row(r, rules)
        if is_valid_target(proposal):
            r["target_subfolder"] = proposal
            updated += 1

    if updated:
        _save_unsorted(rows)
    print(f"Auto-decide: updated={updated}")

def cmd_auto_decide_smart(_: argparse.Namespace) -> None:
    """Lepsze auto-decide: używa heurystyk z djlib.placement z progami ufności.
    ≥0.85: ustaw docelowy kubełek; 0.65..0.85: tylko sugestia (ai_guess_*)."""
    HARDCOMMIT_CONF = 0.85
    SUGGEST_CONF = 0.65
    rows = _load_unsorted()
    set_cnt = sug_cnt = 0
    for r in rows:
        if is_done(r.get("done")):
            continue
        if r.get("target_subfolder"):
            continue
        tgt, conf, reason = decide_bucket(r)
        if not tgt:
            continue
        if conf >= HARDCOMMIT_CONF:
            r["target_subfolder"] = f"READY TO PLAY/{tgt}"
            r["ai_guess_bucket"] = ""
            r["ai_guess_comment"] = f"rule:{reason}; conf={conf:.2f}"
            set_cnt += 1
        elif conf >= SUGGEST_CONF:
            r["ai_guess_bucket"]  = f"READY TO PLAY/{tgt}"
            r["ai_guess_comment"] = f"rule:{reason}; conf={conf:.2f}"
            sug_cnt += 1
    if set_cnt or sug_cnt:
        _save_unsorted(rows)
    print(f"✅ Auto-decide (smart): set={set_cnt}, suggested={sug_cnt}")

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
        return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

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

    # Beatport token health (informative)
    try:
        from djlib.metadata.beatport import token_health
        bp_health = token_health()
        if bp_health.get("status") == "missing":
            print(f"ℹ Beatport: {bp_health.get('message')} (można ustawić: python -m djlib.metadata.beatport --setup)")
        elif bp_health.get("status") == "ok":
            print(f"✅ Beatport: {bp_health.get('message')}")
        elif bp_health.get("status") in {"expired", "error"}:
            print(f"⚠ Beatport: {bp_health.get('message')}")
    except Exception:
        pass
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

    # przygotuj mapowanie tagów → bucket
    tag_map = load_taxonomy_map()

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
            )
            if genre_res and genre_res.confidence >= 0.03:  # lower threshold for missing genres
                # Ustaw 3 gatunki: main + subs
                genres = [genre_res.main] + genre_res.subs[:2]  # max 3 total
                genre_str = ", ".join(genres)
                current_genre = (r.get("genre_suggest") or "").strip()
                if not current_genre or genre_res.confidence > 0.08:  # override existing only if significantly better
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

        # Zaproponuj kubełek na podstawie gatunków
        try:
            genre_str = (r.get("genre_suggest") or "").strip()
            if genre_str and tag_map:
                # Parse genres back to individual tags for voting
                genre_tags = [g.strip() for g in genre_str.split(",") if g.strip()]
                votes = {tag: 1.0 for tag in genre_tags}  # equal weight for each genre
                bucket, conf, breakdown = suggest_bucket_from_votes(votes, tag_map)
                if bucket and conf >= 0.65:
                    r["ai_guess_bucket"]  = f"READY TO PLAY/{bucket}"
                    # zbuduj krótki komentarz z top tagów
                    top_tags = [tag for tag, _, mapped in breakdown if mapped][:3]
                    tags_str = ", ".join(top_tags) if top_tags else genre_str.split(",")[0]
                    r["ai_guess_comment"] = f"genres; conf={conf:.2f}; tags: {tags_str}"
                    any_change = True
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
                        skip_if_exists=True
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
        processed += 1
        status_doc["rows_processed"] = processed
        status_doc["updated"] = changed
        status_doc["last_file"] = str(p)
        _flush_status()
    if changed:
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

    for r in ready:
        target = (r.get("target_subfolder") or "").strip()
        if not target:
            continue
        src = Path(r.get("file_path") or "")
        if not src.exists():
            print(f"[WARN] Nie znaleziono pliku: {src}")
            continue

        dest_dir = resolve_target_path(target)
        if dest_dir is None:
            continue

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

        dest_path = dest_dir / final_name
        print(f"{'DRY-RUN ' if args.dry_run else ''}MOVE: {src} -> {dest_path}")

        if args.dry_run:
            continue

        dest_real = move_with_rename(src, dest_dir, final_name)
        log_rows.append([str(src), str(dest_real), r.get("track_id", "")])
        processed_ids.add(r.get("track_id", ""))
        record = {
            "track_id": r.get("track_id", ""),
            "file_path": str(dest_real),
            "original_path": r.get("file_path") or "",
            "file_hash": r.get("file_hash") or "",
            "fingerprint": r.get("fingerprint") or "",
            "added_date": utc_now_str(),
            "final_filename": final_name,
            "final_path": str(dest_real),
            "artist": r.get("artist") or r.get("tag_artist_original") or r.get("artist_suggest") or "",
            "title": final_title,
            "version_info": version_pref,
            "genre": r.get("genre") or r.get("genre_suggest") or "",
            "bpm": r.get("bpm") or "",
            "key_camelot": r.get("key_camelot") or "",
            "energy_hint": r.get("energy_hint") or "",
            "target_subfolder": target,
            "must_play": r.get("must_play") or "",
            "occasion_tags": r.get("occasion_tags") or "",
            "notes": r.get("notes") or "",
            "is_duplicate": r.get("is_duplicate") or "",
            "pop_playcount": r.get("pop_playcount") or "",
            "pop_listeners": r.get("pop_listeners") or "",
        }
        library_rows.append(record)
        # Po udanym przeniesieniu wyczyść spam tagi i zapisz zaakceptowane metadane
        try:
            # Najpierw wyczyść spam tagi (musicdjs.club, chomikuj.pl, etc.)
            result = clean_tags(dest_real, dry_run=False)
            if result and result.get("removed_tags"):
                tags_cleaned += 1
            # Teraz zapisz właściwe metadane
            updates = {}
            artist = (record["artist"] or "").strip()
            title_out = merge_title_and_version(record.get("title", ""), record.get("version_info", ""))
            if artist:
                updates["artist"] = artist
            if title_out:
                updates["title"] = title_out
            genre = (record["genre"] or "").strip()
            if genre:
                updates["genre"] = genre
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
            if updates:
                write_tags(dest_real, updates)
                tags_written += 1
        except Exception as e:
            print(f"[WARN] Tag write/clean failed for {dest_real}: {e}")
            tags_errors += 1
            if "clean_tags" in str(e):
                tags_clean_errors += 1

    if args.dry_run:
        print(f"[DRY-RUN] Gotowe do eksportu: {len(ready)} (oznaczone done=TRUE).")
        return

    if log_rows:
        with log_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["src_before", "dest_after", "track_id"])
            w.writerows(log_rows)
        print(f"Zapisano log: {log_path}")

    remaining = [r for r in rows if r.get("track_id") not in processed_ids]
    _save_unsorted(remaining)
    save_records(CSV_PATH, library_rows)
    print(f"Przeniesiono {len(processed_ids)} pozycji do biblioteki.")
    print(f"🧹 Czyszczenie spam tagów: cleaned={tags_cleaned}, errors={tags_clean_errors}")
    print(f"📀 Zapis tagów audio: ok={tags_written}, errors={tags_errors}")

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
        src_before = Path(r["src_before"])
        dest_after = Path(r["dest_after"])
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

def cmd_detect_taxonomy(_: argparse.Namespace) -> None:
    """Wykrywa istniejącą strukturę folderów i zapisuje jako taxonomy.local.yml."""
    from djlib.taxonomy import detect_taxonomy_from_fs, save_taxonomy, load_taxonomy
    from djlib.config import LIB_ROOT

    # Załaduj istniejącą taksonomię
    existing = load_taxonomy()
    existing_ready = set(existing["ready_buckets"])
    existing_review = set(existing["review_buckets"])

    # Wykryj nową z filesystem
    detected = detect_taxonomy_from_fs(LIB_ROOT)
    detected_ready = set(detected["ready_buckets"])
    detected_review = set(detected["review_buckets"])

    # Merge: dodaj nowe wykryte, zachowaj istniejące
    merged_ready = existing_ready | detected_ready
    merged_review = existing_review | detected_review

    merged = {
        "ready_buckets": sorted(merged_ready),
        "review_buckets": sorted(merged_review),
    }

    save_taxonomy(merged)
    print(f"Zaktualizowano taksonomię: {len(merged_ready)} ready buckets, {len(merged_review)} review buckets")
    if merged_ready:
        print("Ready buckets:", ", ".join(merged_ready))
    if merged_review:
        print("Review buckets:", ", ".join(merged_review))


def cmd_taxonomy_backup(_: argparse.Namespace) -> None:
    """Zrób snapshot taksonomii na podstawie realnej struktury folderów (LIB_ROOT) i zapisz do backupów.

    Nie modyfikuje istniejącego taxonomy.local.yml. Tworzy:
    - taxonomy.local.yml.backup (nadpisywalny snapshot)
    - taxonomy.local.<timestamp>.yml (archiwalny snapshot)
    """
    from djlib.taxonomy import detect_taxonomy_from_fs
    from djlib.config import LIB_ROOT
    import yaml as _yaml

    lib_root = LIB_ROOT
    data = detect_taxonomy_from_fs(lib_root)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = REPO_ROOT / "taxonomy.local.yml.backup"
    archive_path = REPO_ROOT / f"taxonomy.local.{stamp}.yml"
    try:
        with backup_path.open("w", encoding="utf-8") as f:
            _yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        with archive_path.open("w", encoding="utf-8") as f:
            _yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        print(f"📦 Snapshot zapisany: {backup_path} oraz {archive_path}")
    except Exception as e:
        print(f"[ERR] Nie udało się zapisać backupu taksonomii: {e}")

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



# ============ PARSER ============

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="djlib", description="DJ Library Manager CLI")
    sp = p.add_subparsers(dest="cmd", required=True)

    sp.add_parser("configure").set_defaults(func=cmd_configure)
    scan_parser = sp.add_parser("scan", help="Scan UNSORTED folder for new tracks")
    scan_parser.add_argument(
        "--strict",
        action="store_true",
        help="Require Rekordbox DB confirmation (reject tag-only files from Traktor/Serato)"
    )
    scan_parser.set_defaults(func=cmd_scan)

    ap = sp.add_parser("auto-decide")
    ap.add_argument("--rules", default=str(REPO_ROOT / "rules.yml"))
    ap.add_argument("--only-empty", action="store_true")
    ap.set_defaults(func=cmd_auto_decide)

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

    sp.add_parser("detect-taxonomy").set_defaults(func=cmd_detect_taxonomy)

    # --- Meta-komendy: round-1 i round-2 ---
    tb = sp.add_parser("taxonomy-backup", help="Zrób snapshot taksonomii na podstawie folderów i zapisz backup")
    tb.set_defaults(func=cmd_taxonomy_backup)
    return p

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
