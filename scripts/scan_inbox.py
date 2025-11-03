#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import time

from djlib.config import INBOX_DIR, CSV_PATH, AUDIO_EXTS, ensure_base_dirs
from djlib.csvdb import load_records, save_records, FIELDNAMES
from djlib.tags import read_tags
from djlib.classify import guess_bucket
from djlib.fingerprint import file_sha256, audio_fingerprint

def main():
    ensure_base_dirs()

    rows = load_records(CSV_PATH)
    known_hashes = {r.get("file_hash", "") for r in rows if r.get("file_hash")}
    known_fps = {r.get("fingerprint", "") for r in rows if r.get("fingerprint")}

    new_rows = []
    for p in INBOX_DIR.glob("**/*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in AUDIO_EXTS:
            continue

        fhash = file_sha256(p)
        if fhash in known_hashes:
            # już widzieliśmy IDENTYCZNY plik
            continue

        tags = read_tags(p)
        fp = audio_fingerprint(p)
        is_dup = "true" if (fp and fp in known_fps) else "false"

        ai_bucket, ai_comment = guess_bucket(
            tags["artist"], tags["title"], tags["bpm"], tags["genre"], tags["comment"]
        )

        track_id = f"{fhash[:12]}_{int(time.time())}"

        # Dodaj fingerprint i duration do tagów dla suggest_metadata
        tags_for_suggest = tags.copy()
        tags_for_suggest["fingerprint"] = fp or ""
        
        # Pobierz duration z pliku
        try:
            from djlib.fingerprint import fingerprint_info
            duration_sec, _ = fingerprint_info(p)
            if duration_sec:
                mm, ss = divmod(int(duration_sec), 60)
                tags_for_suggest["duration"] = f"{mm}:{ss:02d}"
        except Exception:
            tags_for_suggest["duration"] = ""

        from djlib.enrich import suggest_metadata
        suggested = suggest_metadata(p, tags_for_suggest)

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
            # Dodaj sugerowane metadane
            "artist_suggest": suggested.get("artist_suggest", ""),
            "title_suggest": suggested.get("title_suggest", ""),
            "version_suggest": suggested.get("version_suggest", ""),
            "genre_suggest": suggested.get("genre_suggest", ""),
            "album_suggest": suggested.get("album_suggest", ""),
            "year_suggest": suggested.get("year_suggest", ""),
            "duration_suggest": suggested.get("duration_suggest", ""),
            "meta_source": suggested.get("meta_source", ""),
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

if __name__ == "__main__":
    main()
