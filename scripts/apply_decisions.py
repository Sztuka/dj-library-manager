#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import argparse, csv, time

from djlib.config import CSV_PATH, LOGS_DIR
from djlib.csvdb import load_records, save_records
from djlib.filename import build_final_filename, extension_for
from djlib.mover import resolve_target_path, move_with_rename, utc_now_str

def main():
    ap = argparse.ArgumentParser(description="Zastosuj decyzje z library.csv (przenoszenie plików)")
    ap.add_argument("--dry-run", action="store_true", help="Pokaż co zostanie zrobione, ale nic nie przenoś")
    args = ap.parse_args()

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
            r.get("artist", ""),
            r.get("title", ""),
            r.get("version_info", ""),
            r.get("key_camelot", ""),
            r.get("bpm", ""),
            extension_for(src),
        )

        dest_path = dest_dir / final_name

        print(f"{'DRY-RUN ' if args.dry_run else ''}MOVE: {src}  ->  {dest_path}")

        if not args.dry_run:
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

if __name__ == "__main__":
    main()
