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

if __name__ == "__main__":
    main()
