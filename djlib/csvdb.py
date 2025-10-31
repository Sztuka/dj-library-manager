from __future__ import annotations
import csv
from pathlib import Path
from typing import List, Dict

FIELDNAMES = [
    "track_id",
    "file_path",
    "artist",
    "title",
    "version_info",
    "genre",
    "bpm",
    "key_camelot",
    "energy_hint",
    "file_hash",
    "fingerprint",
    "is_duplicate",
    "ai_guess_bucket",
    "ai_guess_comment",
    "target_subfolder",
    "must_play",
    "occasion_tags",
    "notes",
    "final_filename",
    "final_path",
    "added_date",
    # --- proposed metadata (to review/accept) ---
    "artist_suggest",
    "title_suggest",
    "version_suggest",
    "genre_suggest",
    "album_suggest",
    "year_suggest",
    "duration_suggest",
    "meta_source",
    "review_status",
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
            w.writerow(r)
