#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import csv
from collections import defaultdict

from djlib.config import CSV_PATH, LOGS_DIR
from djlib.csvdb import load_records

def main():
    rows = load_records(CSV_PATH)
    groups = defaultdict(list)

    for r in rows:
        fp = (r.get("fingerprint") or "").strip()
        if fp:
            groups[fp].append(r)

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

if __name__ == "__main__":
    main()
