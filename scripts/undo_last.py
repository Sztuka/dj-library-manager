#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import csv

from djlib.config import LOGS_DIR
from djlib.csvdb import load_records, save_records, FIELDNAMES

def find_last_log() -> Path | None:
    logs = sorted(LOGS_DIR.glob("moves-*.csv"))
    return logs[-1] if logs else None

def main():
    log = find_last_log()
    if not log:
        print("Brak logów do cofnięcia.")
        return

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

if __name__ == "__main__":
    main()
