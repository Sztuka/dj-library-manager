from __future__ import annotations
import csv
from pathlib import Path
from typing import List, Dict

FIELDNAMES = [
    "track_id",
    "rekordbox_id",
    "traktor_id",
    "file_path",
    "original_path",
    "file_hash",
    "fingerprint",
    "skip_fingerprint",  # Set to "yes" to force using tags instead of AcoustID
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
    # MusicBrainz canonical first release data
    "recording_mbid",
    "release_group_id",  # MusicBrainz release-group MBID for cover art
    "original_album_title",
    "original_release_date",
    "original_release_year",
    "original_release_mbid",
    "original_release_group_mbid",
    "original_release_category",
    "original_release_source",
    # Archive.org live concert metadata
    "archive_org_identifier",  # Archive.org identifier (e.g., 'brucespringsteenbornintheusalivelondon2013')
    "archive_org_cover_url",   # Cover art URL from Archive.org
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
