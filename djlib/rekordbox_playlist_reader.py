"""Read-only fetch of playlist membership from Rekordbox master6.db.

Used by the Review UI /api/playlists/diff endpoint to compare djlib
playlist tags (library.csv `playlists` field) against Rekordbox state.

Playlist folders (ParentID != 0) are traversed but not exposed as
separate playlists — tracks are attributed to their leaf playlist name.
Only real playlists (Attribute == 0) are returned; smart playlists
(Attribute == 1) are excluded.

Connection pattern: new session per call, closed in finally — matches
rekordbox_reader.py. Safe in WAL mode even when Rekordbox is open.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Set

log = logging.getLogger(__name__)

# Rekordbox playlist Attribute values
_ATTR_PLAYLIST = 0   # regular playlist
_ATTR_SMART = 1      # smart playlist (auto-generated)
_ATTR_FOLDER = 2     # folder (contains other playlists, no tracks directly)


def _default_db_path() -> Optional[Path]:
    candidates = [
        Path.home() / "Library/Pioneer/rekordbox/master.db",           # RB 7
        Path.home() / "Library/Application Support/Pioneer/rekordbox/master6.db",  # RB 6
        Path.home() / "Library/Application Support/Pioneer/rekordboxAgent/Storage/master6.db",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def fetch_rb_playlists(
    db_path: Optional[Path] = None,
) -> Dict[str, List[Dict]]:
    """Return all regular Rekordbox playlists with their tracks.

    Returns:
        {playlist_name: [{"rb_id": str, "artist": str, "title": str}, ...]}

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
    from pyrekordbox.db6 import DjmdPlaylist, DjmdSongPlaylist, DjmdContent, DjmdArtist

    result: Dict[str, List[Dict]] = {}
    db = Rekordbox6Database(path=db_path)
    try:
        # Build artist id → name map (single query, cheaper than per-track join)
        artist_map: Dict[int, str] = {}
        for artist in db.session.query(DjmdArtist).all():
            aid = getattr(artist, "ID", None)
            name = getattr(artist, "Name", "") or ""
            if aid is not None:
                artist_map[int(aid)] = name

        # Fetch all non-smart, non-deleted playlists
        playlists = (
            db.session.query(DjmdPlaylist)
            .filter(DjmdPlaylist.Attribute == _ATTR_PLAYLIST)
            .all()
        )

        for pl in playlists:
            pl_name = str(getattr(pl, "Name", "") or "").strip()
            if not pl_name:
                continue

            # Fetch tracks for this playlist
            song_entries = (
                db.session.query(DjmdSongPlaylist, DjmdContent)
                .join(DjmdContent, DjmdSongPlaylist.ContentID == DjmdContent.ID)
                .filter(DjmdSongPlaylist.PlaylistID == pl.ID)
                .order_by(DjmdSongPlaylist.TrackNo)
                .all()
            )

            tracks = []
            for sp, content in song_entries:
                rb_id = str(getattr(content, "ID", "") or "")
                title = str(getattr(content, "Title", "") or "")
                artist_id = getattr(content, "ArtistID", None)
                artist = artist_map.get(int(artist_id), "") if artist_id else ""
                # Fall back to SrcArtistName (populated for some track types)
                if not artist:
                    artist = str(getattr(content, "SrcArtistName", "") or "")
                tracks.append({"rb_id": rb_id, "artist": artist, "title": title})

            # Rekordbox allows duplicate playlist names in different folders.
            # Merge tracks instead of overwriting to avoid silent data loss.
            if pl_name in result:
                log.debug("Duplicate playlist name %r — merging %d tracks", pl_name, len(tracks))
                result[pl_name].extend(tracks)
            else:
                result[pl_name] = tracks

    finally:
        try:
            db.session.close()
        except Exception:
            pass

    return result
