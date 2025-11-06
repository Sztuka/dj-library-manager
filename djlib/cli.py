from __future__ import annotations
import argparse, csv, time, os, json
from pathlib import Path
from typing import Dict, Any

# --- Core importy (nasze moduły) ---
from djlib.config import (
    reconfigure, ensure_base_dirs, CONFIG_FILE,
    INBOX_DIR, READY_TO_PLAY_DIR, REVIEW_QUEUE_DIR, LOGS_DIR, CSV_PATH, AUDIO_EXTS
)
from djlib.csvdb import load_records, save_records
from djlib.tags import read_tags
from djlib.enrich import suggest_metadata, enrich_online_for_row
from djlib.genre import external_genre_votes, load_taxonomy_map, suggest_bucket_from_votes
from djlib.metadata.genre_resolver import resolve as resolve_genres
from djlib.classify import guess_bucket
from djlib.fingerprint import file_sha256, fingerprint_info
from djlib.filename import build_final_filename, extension_for
from djlib.mover import resolve_target_path, move_with_rename, utc_now_str
from djlib.buckets import is_valid_target
from djlib.placement import decide_bucket
try:
    from djlib.audio import check_env as audio_check_env
    from djlib.audio import analyze as audio_analyze
    from djlib.audio.cache import get_analysis
except Exception:
    audio_check_env = None  # type: ignore
    audio_analyze = None  # type: ignore
    get_analysis = None  # type: ignore

# --- Pomocnicze ---
REPO_ROOT = Path(__file__).resolve().parents[1]

# ============ KOMENDY ============

def cmd_configure(_: argparse.Namespace) -> None:
    cfg, path = reconfigure()
    ensure_base_dirs()
    print(f"\n✅ Zapisano konfigurację do: {path}")
    print(f"   library_root: {cfg.library_root}")
    print(f"   inbox_dir:    {cfg.inbox_dir}\n")

def cmd_scan(_: argparse.Namespace) -> None:
    ensure_base_dirs()
    rows = load_records(CSV_PATH)
    known_hashes = {r.get("file_hash", "") for r in rows if r.get("file_hash")}
    known_fps    = {r.get("fingerprint", "") for r in rows if r.get("fingerprint")}

    # przygotuj plik statusu skanowania
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
    processed = 0
    added = 0
    errors = 0
    missing_fpcalc = False
    _write_status({
        "state": "running",
        "total": total,
        "processed": 0,
        "added": 0,
        "errors": 0,
        "last_file": "",
    })

    new_rows = []
    for p in all_files:
        fhash = file_sha256(p)
        if fhash in known_hashes:
            processed += 1
            _write_status({"state": "running", "total": total, "processed": processed, "added": added, "errors": errors, "last_file": str(p)})
            continue

        tags = read_tags(p)
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

        # Proponowane metadane (preferuj online w przyszłości; teraz filename+fallback)
        sugg = suggest_metadata(p, tags)
        # jeśli nie mamy duration z online, wstaw lokalny czas z fingerprintu
        if (sugg.get("duration_suggest") or "").strip() == "" and dur:
            mm = dur // 60
            ss = dur % 60
            sugg["duration_suggest"] = f"{mm}:{ss:02d}"

        track_id = f"{fhash[:12]}_{int(time.time())}"
        rec = {
            "track_id": track_id,
            "file_path": str(p),
            # Główne pola pozostają puste do czasu akceptacji; korzystamy z suggest_*
            "artist": "",
            "title": "",
            "version_info": "",
            "bpm": tags["bpm"],
            "key_camelot": tags["key_camelot"],
            "energy_hint": tags["energy_hint"],
            "file_hash": fhash,
            "fingerprint": fp,
            "is_duplicate": is_dup,
            "ai_guess_bucket": ai_bucket,
            "ai_guess_comment": ai_comment,
            "target_subfolder": "",
            "must_play": "",
            "occasion_tags": "",
            "notes": "",
            "final_filename": "",
            "final_path": "",
            "added_date": "",
            # propozycje do weryfikacji
            **sugg,
            "review_status": "pending",
        }
        new_rows.append(rec)
        known_hashes.add(fhash)
        if fp:
            known_fps.add(fp)
        added += 1
        processed += 1
        _write_status({
            "state": "running",
            "total": total,
            "processed": processed,
            "added": added,
            "errors": errors,
            "last_file": str(p),
            "missing_fpcalc": missing_fpcalc,
        })

    if new_rows:
        rows.extend(new_rows)
        save_records(CSV_PATH, rows)
        print(f"Zeskanowano {len(new_rows)} plików. Zapisano {CSV_PATH}.")
    else:
        print("Brak nowych plików do dodania.")

    _write_status({
        "state": "done",
        "total": total,
        "processed": processed,
        "added": added,
        "errors": errors,
        "csv_rows": len(load_records(CSV_PATH)),
        "missing_fpcalc": missing_fpcalc,
    })

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
    rows = load_records(CSV_PATH)
    updated = 0

    for r in rows:
        if args.only_empty and (r.get("target_subfolder") or "").strip():
            continue
        proposal = _decide_for_row(r, rules)
        if is_valid_target(proposal):
            r["target_subfolder"] = proposal
            updated += 1

    if updated:
        save_records(CSV_PATH, rows)
        print(f"Zaktualizowano {updated} wierszy.")
    else:
        print("Brak zmian.")

def cmd_auto_decide_smart(_: argparse.Namespace) -> None:
    """Lepsze auto-decide: używa heurystyk z djlib.placement z progami ufności.
    ≥0.85: ustaw docelowy kubełek; 0.65..0.85: tylko sugestia (ai_guess_*)."""
    HARDCOMMIT_CONF = 0.85
    SUGGEST_CONF = 0.65
    rows = load_records(CSV_PATH)
    set_cnt = sug_cnt = 0
    for r in rows:
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
        save_records(CSV_PATH, rows)
    print(f"✅ Auto-decide (smart): set={set_cnt}, suggested={sug_cnt}")

def cmd_enrich_online(_: argparse.Namespace) -> None:
    """Wzbogaca metadane (suggest_*) dla pozycji pending korzystając z MusicBrainz/AcoustID/Last.fm/Spotify.
    Prowadzi status w LOGS/enrich_status.json, aby UI mogło pokazywać postęp.
    Nie nadpisuje już zaakceptowanych. Nie zmienia BPM/Key.
    """
    rows = load_records(CSV_PATH)
    todo = [r for r in rows if (r.get("review_status") or "").lower() != "accepted"]
    total = len(todo)
    processed = 0
    changed = 0

    # status plik
    status_path = LOGS_DIR / "enrich_status.json"
    def _write_status(state: str, last_file: str = "") -> None:
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            with status_path.open("w", encoding="utf-8") as f:
                json.dump({
                    "state": state,
                    "total": total,
                    "processed": processed,
                    "updated": changed,
                    "last_file": last_file,
                }, f, ensure_ascii=False)
        except Exception:
            pass

    _write_status("running", "")

    # przygotuj mapowanie tagów → bucket
    tag_map = load_taxonomy_map()

    for r in rows:
        if (r.get("review_status") or "").lower() == "accepted":
            continue
        p = Path(r.get("file_path",""))
        online = enrich_online_for_row(p, r)
        if not online:
            processed += 1
            _write_status("running", str(p))
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
        
        # Zawsze spróbuj wzbogacić gatunki używając wszystkich źródeł (MB + Last.fm + Spotify)
        try:
            a = (r.get("artist_suggest") or r.get("artist") or "").strip()
            t = (r.get("title_suggest") or r.get("title") or "").strip()
            dur_s = None
            if r.get("duration_suggest"):
                try:
                    dur_parts = r["duration_suggest"].split(":")
                    if len(dur_parts) == 2:
                        dur_s = int(dur_parts[0]) * 60 + int(dur_parts[1])
                except Exception:
                    pass
            
            from djlib.metadata.genre_resolver import resolve as resolve_genres
            genre_res = resolve_genres(a, t, duration_s=dur_s)
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
        except Exception as e:
            # Debug: print exception for troubleshooting
            print(f"Genre resolution failed for {a} - {t}: {e}")
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
        processed += 1
        _write_status("running", str(p))
    if changed:
        save_records(CSV_PATH, rows)
    _write_status("done", "")
    print(f"🔎 Enrich online: updated={changed}")

def cmd_fix_fingerprints(_: argparse.Namespace) -> None:
    """Uzupełnij brakujące fingerprinty w istniejącym CSV.
    Dla każdego wiersza bez fingerprintu spróbuj wyliczyć go z pliku (preferuj final_path, potem file_path).
    Aktualizuj też duration_suggest jeśli puste.
    Pisz postęp do LOGS/fingerprint_status.json, aby UI mogło pokazywać pasek.
    """
    from djlib.config import LOGS_DIR
    rows = load_records(CSV_PATH)
    targets = []
    for r in rows:
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
        save_records(CSV_PATH, rows)
    _write_status("done", "")
    print(f"🧩 Fix fingerprints: updated={updated}, errors={errors}")

def cmd_apply(args: argparse.Namespace) -> None:
    rows = load_records(CSV_PATH)
    changed = False

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = LOGS_DIR / f"moves-{stamp}.csv"
    log_rows = []

    for r in rows:
        target = (r.get("target_subfolder") or "").strip()
        if not target or target.upper() == "REJECT":
            continue
        if r.get("final_path"):
            continue

        src = Path(r["file_path"])
        if not src.exists():
            print(f"[WARN] Nie znaleziono pliku: {src}")
            continue

        dest_dir = resolve_target_path(target)
        if dest_dir is None:
            continue

        final_name = build_final_filename(
            r.get("artist_suggest") or r.get("artist", ""), 
            r.get("title_suggest") or r.get("title", ""),
            r.get("version_suggest") or r.get("version_info", ""), 
            r.get("key_camelot", ""),
            r.get("bpm", ""), 
            extension_for(src)
        )

        dest_path = dest_dir / final_name
        print(f"{'DRY-RUN ' if args.dry_run else ''}MOVE: {src} -> {dest_path}")

        if args.dry_run:
            continue

        dest_real = move_with_rename(src, dest_dir, final_name)
        r["final_filename"] = final_name
        r["final_path"] = str(dest_real)
        r["added_date"] = utc_now_str()
        changed = True
        log_rows.append([str(src), str(dest_real), r.get("track_id","")])

    if not args.dry_run and log_rows:
        with log_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["src_before", "dest_after", "track_id"])
            w.writerows(log_rows)
        print(f"Zapisano log: {log_path}")

    if changed and not args.dry_run:
        save_records(CSV_PATH, rows)
        print("Przeniesiono i zaktualizowano CSV.")
    elif not changed and not args.dry_run:
        print("Brak pozycji do przeniesienia.")

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
    """Zsynchronizuj metryki (BPM/Key/Energy) z cache SQLite do głównego CSV.
    Domyślnie uzupełnia tylko puste pola; użyj --force aby nadpisać istniejące.
    Opcjonalnie zapisuje metadane do plików audio jeśli --write-tags.
    """
    if get_analysis is None:
        print("Audio cache backend niedostępny.")
        return
    rows = load_records(CSV_PATH)
    if not rows:
        print("Brak wierszy w CSV.")
        return
    force = bool(getattr(args, "force", False))
    write_tags_flag = bool(getattr(args, "write_tags", False))
    updated = 0
    tags_written = 0
    for r in rows:
        audio_id = (r.get("file_hash") or "").strip()
        if not audio_id:
            # spróbuj policzyć hash jeśli plik istnieje
            try:
                p = Path(r.get("file_path") or "")
                if p.exists():
                    from djlib.audio.cache import compute_audio_id as _cmp
                    audio_id = _cmp(p)
                else:
                    continue
            except Exception:
                continue
        a = get_analysis(audio_id)
        if not a:
            continue
        # przygotuj wartości
        bpm = a.get("bpm")
        key = a.get("key_camelot")
        energy = a.get("energy")
        # uzupełniaj tylko puste chyba że --force
        def _should_set(cur: str) -> bool:
            return force or (not (cur or "").strip())
        changed = False
        if bpm is not None and _should_set(r.get("bpm", "")):
            try:
                r["bpm"] = f"{float(bpm):.2f}".rstrip("0").rstrip(".")
            except Exception:
                r["bpm"] = str(bpm)
            changed = True
        if key and _should_set(r.get("key_camelot", "")):
            r["key_camelot"] = str(key)
            changed = True
        if energy is not None and _should_set(r.get("energy_hint", "")):
            # energy jako 0..1 → wpisz procentowo (0..100) lub float; wybierz prosty procent
            try:
                r["energy_hint"] = f"{round(float(energy)*100)}"
            except Exception:
                r["energy_hint"] = str(energy)
            changed = True
        if changed:
            updated += 1
        
        # Zapisz metadane do pliku jeśli --write-tags
        if write_tags_flag and (bpm or key):
            try:
                p = Path(r.get("file_path") or "")
                if p.exists():
                    from djlib.tags import write_tags
                    tag_updates = {}
                    if bpm is not None:
                        tag_updates["bpm"] = str(bpm)
                    if key:
                        tag_updates["key_camelot"] = str(key)
                    write_tags(p, tag_updates)
                    tags_written += 1
            except Exception as e:
                print(f"[WARN] Nie udało się zapisać tagów do {p}: {e}")
    
    if updated:
        save_records(CSV_PATH, rows)
    print(f"🔄 Sync audio metrics: updated={updated}, tags_written={tags_written}")

def cmd_genres_resolve(args: argparse.Namespace) -> None:
    artist = (getattr(args, "artist", None) or "").strip()
    title = (getattr(args, "title", None) or "").strip()
    dur = getattr(args, "duration", None)
    res = resolve_genres(artist, title, duration_s=dur)
    if not res:
        print("Brak wyników z zewnętrznych źródeł (MB/LFM/Spotify).")
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

# ============ PARSER ============

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="djlib", description="DJ Library Manager CLI")
    sp = p.add_subparsers(dest="cmd", required=True)

    sp.add_parser("configure").set_defaults(func=cmd_configure)
    sp.add_parser("scan").set_defaults(func=cmd_scan)

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
    sp.add_parser("enrich-online").set_defaults(func=cmd_enrich_online)

    # analyze-audio
    aap = sp.add_parser("analyze-audio")
    aap.add_argument("--path", default=str(INBOX_DIR), help="Ścieżka pliku lub folderu (domyślnie INBOX)")
    aap.add_argument("--check-env", action="store_true", help="Sprawdź środowisko Essentia")
    aap.add_argument("--recompute", action="store_true", help="Pomiń cache i przelicz na nowo")
    aap.add_argument("--workers", type=int, default=1, help="Liczba workerów (na razie ignorowane; skeleton)")
    aap.add_argument("--target-bpm", default="80:180", help="Zakres docelowy BPM, np. 80:180")
    aap.set_defaults(func=cmd_analyze_audio)

    # genres resolve (single lookup)
    gp = sp.add_parser("genres")
    gsp = gp.add_subparsers(dest="subcmd", required=True)
    res = gsp.add_parser("resolve")
    res.add_argument("--artist", required=True)
    res.add_argument("--title", required=True)
    res.add_argument("--duration", type=int, default=None, help="Duration in seconds (optional)")
    res.set_defaults(func=cmd_genres_resolve)

    sp.add_parser("detect-taxonomy").set_defaults(func=cmd_detect_taxonomy)
    return p

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
