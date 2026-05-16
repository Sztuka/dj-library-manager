"""Push djlib playlist tags from library.csv to Rekordbox as playlists.

Each unique name in the pipe-separated `playlists` field becomes a Rekordbox
playlist. Existing djlib-managed playlists are rebuilt from scratch (destructive
replace). Playlists not mentioned in library.csv are left untouched.

Tracks are matched by rekordbox_id. Tracks missing a rekordbox_id are skipped
with a warning (they haven't been synced from Rekordbox yet).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set

log = logging.getLogger(__name__)

_DJLIB_MARKER = "[djlib]"


def _djlib_playlist_name(name: str) -> str:
    return name


def push_playlists(
    library_csv_path: Path,
    dry_run: bool = False,
    only: Optional[List[str]] = None,
) -> Dict[str, int]:
    """Read library.csv and push playlists to Rekordbox master.db.

    Args:
        library_csv_path: Path to library.csv
        dry_run: If True, print what would happen without writing.
        only: If given, only push these playlist names.

    Returns:
        Dict mapping playlist_name → number of tracks pushed.
    """
    import csv

    from pyrekordbox import Rekordbox6Database
    from pyrekordbox.utils import get_rekordbox_pid

    if get_rekordbox_pid():
        raise RuntimeError(
            "Rekordbox is running — close it before pushing playlists "
            "to avoid database corruption."
        )

    with library_csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Build: playlist_name → [rekordbox_id, ...]
    playlist_to_rbids: Dict[str, List[str]] = defaultdict(list)
    skipped_no_rbid = 0

    for row in rows:
        raw = (row.get("playlists") or "").strip()
        if not raw:
            continue
        rb_id = (row.get("rekordbox_id") or "").strip()
        if not rb_id:
            skipped_no_rbid += 1
            continue
        for name in raw.split("|"):
            name = name.strip()
            if name and (only is None or name in only):
                playlist_to_rbids[name].append(rb_id)

    if skipped_no_rbid:
        log.warning(
            "%d tracks have playlists but no rekordbox_id — skipped (run sync-dj-libraries first)",
            skipped_no_rbid,
        )

    if not playlist_to_rbids:
        print("No playlists to push.")
        return {}

    if dry_run:
        print("DRY RUN — no changes written to Rekordbox\n")
        for name, rbids in sorted(playlist_to_rbids.items()):
            print(f"  Playlist '{name}': {len(rbids)} tracks")
        return {name: len(ids) for name, ids in playlist_to_rbids.items()}

    db = Rekordbox6Database()

    # Index existing playlists by name for fast lookup
    existing: Dict[str, object] = {}
    for p in db.get_playlist():
        existing[p.Name] = p

    results: Dict[str, int] = {}

    for playlist_name, rb_ids in sorted(playlist_to_rbids.items()):
        print(f"Pushing playlist '{playlist_name}' ({len(rb_ids)} tracks)…", end=" ", flush=True)

        # Get or create the playlist
        if playlist_name in existing:
            playlist = existing[playlist_name]
            # Remove all existing tracks from this playlist
            for entry in list(playlist.Songs):
                db.session.delete(entry)
            db.session.flush()
        else:
            playlist = db.create_playlist(playlist_name)

        # Add tracks in order
        added = 0
        for track_no, rb_id in enumerate(rb_ids, start=1):
            try:
                db.add_to_playlist(playlist, int(rb_id), track_no=track_no)
                added += 1
            except Exception as exc:
                log.warning("Could not add rekordbox_id=%s to '%s': %s", rb_id, playlist_name, exc)

        results[playlist_name] = added
        print(f"✓ {added} tracks")

    db.commit()
    print(f"\nDone. {len(results)} playlist(s) updated in Rekordbox.")
    return results
