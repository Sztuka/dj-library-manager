from __future__ import annotations
import argparse, csv, time, os
from pathlib import Path
from typing import Dict, Any

# --- Core importy (nasze moduły) ---
from djlib.config import (
    reconfigure, ensure_base_dirs, CONFIG_FILE,
    INBOX_DIR, READY_TO_PLAY_DIR, REVIEW_QUEUE_DIR, LOGS_DIR, CSV_PATH, AUDIO_EXTS
)
from djlib.csvdb import load_records, save_records
from djlib.tags import read_tags
from djlib.classify import guess_bucket
from djlib.fingerprint import file_sha256, audio_fingerprint
from djlib.filename import build_final_filename, extension_for
from djlib.mover import resolve_target_path, move_with_rename, utc_now_str
from djlib.buckets import is_valid_target

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

    new_rows = []
    for p in INBOX_DIR.glob("**/*"):
        if not p.is_file() or p.suffix.lower() not in AUDIO_EXTS:
            continue

        fhash = file_sha256(p)
        if fhash in known_hashes:
            continue

        tags = read_tags(p)
        fp = audio_fingerprint(p)
        is_dup = "true" if (fp and fp in known_fps) else "false"

        ai_bucket, ai_comment = guess_bucket(
            tags["artist"], tags["title"], tags["bpm"], tags["genre"], tags["comment"]
        )

        track_id = f"{fhash[:12]}_{int(time.time())}"
        rec = {
            "track_id": track_id,
            "file_path": str(p),
            "artist": tags["artist"],
            "title": tags["title"],
            "version_info": tags["version_info"],
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
        }
        new_rows.append(rec)
        known_hashes.add(fhash)
        if fp:
            known_fps.add(fp)

    if new_rows:
        rows.extend(new_rows)
        save_records(CSV_PATH, rows)
        print(f"Zeskanowano {len(new_rows)} plików. Zapisano {CSV_PATH}.")
    else:
        print("Brak nowych plików do dodania.")

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
            r.get("artist", ""), r.get("title", ""),
            r.get("version_info", ""), r.get("key_camelot", ""),
            r.get("bpm", ""), extension_for(src)
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
    return p

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
