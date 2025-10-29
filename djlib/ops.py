from __future__ import annotations
from pathlib import Path
from typing import List
import csv
import shutil

from djlib.config import READY_TO_PLAY_DIR, LOGS_DIR, CSV_PATH
from djlib.csvdb import load_records, save_records
from djlib.taxonomy import _read_taxonomy, _write_taxonomy, ensure_taxonomy_dirs, target_to_path
from djlib.mover import move_with_rename, utc_now_str

def ensure_taxonomy_folders() -> None:
    """Utwórz wszystkie foldery z taksonomii."""
    ensure_taxonomy_dirs()

def rename_ready_bucket(old_rel: str, new_rel: str) -> None:
    """
    Zmienia nazwę bucketu READY_TO_PLAY/<old_rel> -> READY_TO_PLAY/<new_rel>.
    - podmienia w taxonomy.yml
    - przenosi pliki/foldery (scala jeśli dest istnieje)
    - aktualizuje library.csv: target_subfolder i final_path
    - zapisuje LOGS/rename-bucket-*.csv
    """
    old_rel = old_rel.strip().strip("/")
    new_rel = new_rel.strip().strip("/")
    if not old_rel or not new_rel or old_rel == new_rel:
        return

    data = _read_taxonomy()
    ready = list(data["ready_buckets"])
    if old_rel not in ready:
        # nic do zrobienia
        return

    # 1) taxonomy -> podmiana
    if new_rel not in ready:
        ready.append(new_rel)
    ready = [b for b in ready if b != old_rel]
    _write_taxonomy(sorted(set(ready)), data["review_buckets"])
    ensure_taxonomy_dirs()

    # 2) ścieżki
    old_dir = READY_TO_PLAY_DIR / old_rel
    new_dir = READY_TO_PLAY_DIR / new_rel
    new_dir.mkdir(parents=True, exist_ok=True)

    # 3) przenieś pliki
    moves: List[list[str]] = []
    if old_dir.exists():
        # jeśli dest nie istnieje lub jest pusty -> spróbuj prostego przeniesienia katalogu
        try:
            if not any(new_dir.iterdir()):
                shutil.move(str(old_dir), str(new_dir))
            else:
                # merge: przenieś dzieci pojedynczo
                for p in old_dir.iterdir():
                    if p.is_file():
                        dest = move_with_rename(p, new_dir, p.name)
                        moves.append([str(p), str(dest)])
                    elif p.is_dir():
                        # zagnieżdżone katalogi: przenieś rekurencyjnie
                        dest_sub = new_dir / p.name
                        dest_sub.mkdir(parents=True, exist_ok=True)
                        for sub in p.rglob("*"):
                            if sub.is_file():
                                rel = sub.relative_to(p)
                                dest_file = dest_sub / rel
                                dest_file.parent.mkdir(parents=True, exist_ok=True)
                                if dest_file.exists():
                                    dest_file = dest_file.parent / f"{dest_file.stem} (1){dest_file.suffix}"
                                shutil.move(str(sub), str(dest_file))
                        shutil.rmtree(p, ignore_errors=True)
                # spróbuj posprzątać stary katalog
                try: old_dir.rmdir()
                except Exception: pass
        except Exception:
            # fallback: merge per-plik
            new_dir.mkdir(parents=True, exist_ok=True)
            for sub in old_dir.rglob("*"):
                if sub.is_file():
                    rel = sub.relative_to(old_dir)
                    dest_file = new_dir / rel
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    if dest_file.exists():
                        dest_file = dest_file.parent / f"{dest_file.stem} (1){dest_file.suffix}"
                    shutil.move(str(sub), str(dest_file))
            shutil.rmtree(old_dir, ignore_errors=True)

    # 4) aktualizacja CSV
    rows = load_records(CSV_PATH)
    updated = 0

    old_target = f"READY TO PLAY/{old_rel}"
    new_target = f"READY TO PLAY/{new_rel}"

    for r in rows:
        # target_subfolder
        cur = (r.get("target_subfolder") or "")
        if cur == old_target:
            r["target_subfolder"] = new_target
            updated += 1

        # final_path
        fpath = r.get("final_path") or ""
        if fpath:
            try:
                p = Path(fpath)
                # jeśli był pod starym bucketem -> zamień na nową ścieżkę, ale tylko jeśli plik realnie przenieśliśmy
                old_root = target_to_path(old_target)
                new_root = target_to_path(new_target)
                if old_root and new_root:
                    try:
                        rel = p.resolve().relative_to(old_root.resolve())
                    except Exception:
                        rel = None
                    if rel is not None:
                        cand = new_root / rel
                        if cand.exists():
                            r["final_path"] = str(cand)
                            updated += 1
            except Exception:
                pass

    if updated:
        save_records(CSV_PATH, rows)


    # 5) log
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"rename-bucket-{utc_now_str().replace(':','').replace(' ','_')}.csv"
    with log_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["old_bucket_rel", "new_bucket_rel"])
        w.writerow([old_rel, new_rel])
    # (opcjonalnie mógłby tu być szczegółowy log ruchów plików)
