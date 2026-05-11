"""Read-only fetch of track fields from Rekordbox master6.db for gig-merge.

Phase 3 gig-merge uses this to get the post-gig state of tracks:
cue points edited, ratings changed, play counts incremented during the gig.

Only reads — never calls db.session.commit() or modifies any data.
Safe to run while Rekordbox is closed (does not require Rekordbox to be running).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Fields fetched from Rekordbox and returned per track.
# These are the mutable fields a DJ changes during/after a gig.
_MUTABLE_FIELDS = ("cue_points_rb", "rating", "play_count", "last_played")


def _default_db_path() -> Optional[Path]:
    """Return the default Rekordbox 6/7 master6.db path on macOS, or None."""
    candidates = [
        Path.home() / "Library/Application Support/Pioneer/rekordbox/master6.db",
        Path.home() / "Library/Application Support/Pioneer/rekordboxAgent/Storage/master6.db",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _content_to_dict(content: Any) -> Dict[str, str]:
    """Extract mutable fields from a DjmdContent row."""
    from djlib.cues.schema import serialize_rb_cues

    bpm_raw = getattr(content, "BPM", None)
    bpm_str = f"{bpm_raw / 100.0:.2f}" if bpm_raw else ""

    return {
        "cue_points_rb": serialize_rb_cues(getattr(content, "Cues", None)),
        "rating":        str(getattr(content, "Rating", "") or ""),
        "play_count":    str(getattr(content, "DJPlayCount", "") or ""),
        "last_played":   str(getattr(content, "LastPlayed", "") or ""),
        "bpm":           bpm_str,
    }


def fetch_gig_tracks(
    gig_tracks: List[Dict[str, str]],
    db_path: Optional[Path] = None,
) -> Dict[str, Dict[str, str]]:
    """Fetch post-gig field values from Rekordbox master6.db.

    Args:
        gig_tracks: list of dicts, each must have "track_id" and "rekordbox_id".
                    Entries without rekordbox_id are silently skipped.
        db_path:    path to master6.db; defaults to the macOS system location.

    Returns:
        {track_id: {field: value}} for every track with a recognised rekordbox_id.
        Tracks not found in Rekordbox are omitted from the result (not an error —
        the DJ may have removed them from the collection).

    Raises:
        FileNotFoundError: if db_path is None and the default location doesn't exist.
        ImportError: if pyrekordbox is not installed.
    """
    if db_path is None:
        db_path = _default_db_path()
        if db_path is None:
            raise FileNotFoundError(
                "Rekordbox master6.db not found at the default macOS location. "
                "Pass db_path explicitly."
            )

    from pyrekordbox import Rekordbox6Database

    # Build lookup: rekordbox_id (str) → track_id
    rb_id_to_tid: Dict[str, str] = {}
    for entry in gig_tracks:
        tid = str(entry.get("track_id", "") or "")
        rb_id = str(entry.get("rekordbox_id", "") or "")
        if tid and rb_id:
            rb_id_to_tid[rb_id] = tid
        elif tid and not rb_id:
            log.debug("track_id=%s has no rekordbox_id — skipping", tid)

    if not rb_id_to_tid:
        return {}

    result: Dict[str, Dict[str, str]] = {}
    db = Rekordbox6Database(path=db_path)
    try:
        for content in db.get_content():
            rb_id = str(getattr(content, "ID", "") or "")
            tid = rb_id_to_tid.get(rb_id)
            if tid is None:
                continue
            try:
                result[tid] = _content_to_dict(content)
            except Exception as exc:
                log.warning("Could not read fields for rekordbox_id=%s: %s", rb_id, exc)
    finally:
        try:
            db.session.close()
        except Exception:
            pass

    found = len(result)
    total = len(rb_id_to_tid)
    if found < total:
        missing = total - found
        log.warning(
            "%d of %d gig tracks not found in Rekordbox DB "
            "(removed from collection, or ID mismatch)",
            missing, total,
        )

    return result
