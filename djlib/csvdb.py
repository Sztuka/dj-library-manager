from __future__ import annotations
import csv
from pathlib import Path
from typing import List, Dict

FIELDNAMES = [
    "track_id",
    "file_path",
    "original_path",
    "file_hash",
    "fingerprint",
    "added_date",
    "final_filename",
    "final_path",
    "artist",
    "title",
    "version_info",
    "genre",
    "bpm",
    "key_camelot",
    "energy_hint",
    "target_subfolder",
    "must_play",
    "occasion_tags",
    "notes",
    "is_duplicate",
    "pop_playcount",
    "pop_listeners",
]

def load_records(csv_path: Path) -> List[Dict[str, str]]:
    if not csv_path.exists():
        return []
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def save_records(csv_path: Path, rows: List[Dict[str, str]]) -> None:
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            clean = {k: r.get(k, "") for k in FIELDNAMES}
            w.writerow(clean)
