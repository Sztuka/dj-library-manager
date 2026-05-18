"""Read-only fetch of track fields from Rekordbox master6.db.

Used by:
- gig-merge (Phase 3): fetch post-gig cue points, ratings, play counts.
- cmd_apply: fetch playlist membership for tracks already in Rekordbox
  so their djlib playlists field is populated on first apply.

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
# NOTE: last_played is intentionally omitted — DjmdContent has no LastPlayed column;
# play history lives in DjmdSongHistory and would require a join + aggregation.
_MUTABLE_FIELDS = ("cue_points_rb", "rating", "play_count", "bpm")


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
    from pyrekordbox.db6 import DjmdContent

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

    # Convert rekordbox_id strings to ints for SQL IN filter
    rb_int_ids = []
    for rb_id in rb_id_to_tid:
        try:
            rb_int_ids.append(int(rb_id))
        except ValueError:
            log.warning("rekordbox_id %r is not an integer — skipping", rb_id)

    result: Dict[str, Dict[str, str]] = {}
    db = Rekordbox6Database(path=db_path)
    try:
        # Targeted query: only fetch the N tracks we need, not the whole collection
        rows = (
            db.session.query(DjmdContent)
            .filter(DjmdContent.ID.in_(rb_int_ids))
            .all()
        )
        for content in rows:
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


def fetch_playlists_for_tracks(
    track_entries: List[Dict[str, str]],
    db_path: Optional[Path] = None,
) -> Dict[str, List[str]]:
    """Return Rekordbox playlist names per track, keyed by track_id.

    Looks up by rekordbox_id first (fast, exact). Falls back to filename
    matching for tracks not yet imported into djlib's sync (no rekordbox_id).

    Skips silently when pyrekordbox is not installed, the DB is absent, or
    a track is not found in the Rekordbox collection.

    Returns:
        {track_id: ["Playlist A", "Playlist B", ...]}
        Only tracks with at least one playlist membership appear in the result.
    """
    if not track_entries:
        return {}

    if db_path is None:
        db_path = _default_db_path()
        if db_path is None:
            log.debug("Rekordbox DB not found — skipping playlist lookup")
            return {}

    try:
        from pyrekordbox import Rekordbox6Database
        from pyrekordbox.db6 import DjmdContent, DjmdPlaylist, DjmdSongPlaylist
    except ImportError:
        log.debug("pyrekordbox not installed — skipping RB playlist lookup")
        return {}

    # Build two lookup maps: by rekordbox_id (preferred) and by filename (fallback).
    rb_id_to_tid: Dict[str, str] = {}
    filename_to_tid: Dict[str, str] = {}   # filename → track_id (for fallback)

    for entry in track_entries:
        tid = str(entry.get("track_id", "") or "")
        if not tid:
            continue
        rb_id = str(entry.get("rekordbox_id", "") or "")
        if rb_id:
            rb_id_to_tid[rb_id] = tid
        else:
            fp = str(entry.get("file_path", "") or "")
            if fp:
                filename_to_tid[Path(fp).name] = tid

    result: Dict[str, List[str]] = {}

    try:
        db = Rekordbox6Database(path=db_path)
        try:
            content_id_to_tid: Dict[int, str] = {}

            # 1. Primary lookup: rekordbox_id → DjmdContent.ID
            if rb_id_to_tid:
                rb_int_ids = []
                for rb_id in rb_id_to_tid:
                    try:
                        rb_int_ids.append(int(rb_id))
                    except ValueError:
                        log.debug("rekordbox_id %r is not an integer — skipping", rb_id)
                if rb_int_ids:
                    rows = (
                        db.session.query(DjmdContent)
                        .filter(DjmdContent.ID.in_(rb_int_ids))
                        .all()
                    )
                    for content in rows:
                        cid = int(getattr(content, "ID", 0) or 0)
                        tid = rb_id_to_tid.get(str(cid))
                        if tid:
                            content_id_to_tid[cid] = tid

            # 2. Fallback: filename match for tracks without rekordbox_id.
            #    Filters by FileNameL (cheap) then checks folder if ambiguous.
            if filename_to_tid:
                already_mapped = set(content_id_to_tid.values())
                remaining = {fn: tid for fn, tid in filename_to_tid.items()
                             if tid not in already_mapped}
                if remaining:
                    rows = (
                        db.session.query(DjmdContent)
                        .filter(DjmdContent.FileNameL.in_(list(remaining.keys())))
                        .all()
                    )
                    for content in rows:
                        fname = str(getattr(content, "FileNameL", "") or "")
                        tid = remaining.get(fname)
                        if tid:
                            cid = int(getattr(content, "ID", 0) or 0)
                            content_id_to_tid[cid] = tid

            if not content_id_to_tid:
                return {}

            # 3. Fetch playlist memberships for all found content IDs.
            content_ids = list(content_id_to_tid.keys())
            song_pls = (
                db.session.query(DjmdSongPlaylist)
                .filter(DjmdSongPlaylist.ContentID.in_(content_ids))
                .all()
            )
            if not song_pls:
                return {}

            # 4. Resolve playlist names.
            pl_ids = {int(sp.PlaylistID) for sp in song_pls if sp.PlaylistID}
            playlists = (
                db.session.query(DjmdPlaylist)
                .filter(DjmdPlaylist.ID.in_(pl_ids))
                .all()
            )
            pl_name_by_id: Dict[int, str] = {
                int(getattr(pl, "ID", 0)): str(getattr(pl, "Name", "") or "")
                for pl in playlists
            }

            for sp in song_pls:
                cid = int(sp.ContentID or 0)
                pl_id = int(sp.PlaylistID or 0)
                tid = content_id_to_tid.get(cid)
                name = pl_name_by_id.get(pl_id, "").strip()
                if tid and name:
                    result.setdefault(tid, []).append(name)

        finally:
            try:
                db.session.close()
            except Exception:
                pass

    except Exception as exc:
        log.warning("Could not fetch Rekordbox playlist memberships: %s", exc)

    return result
