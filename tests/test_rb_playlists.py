"""Tests for fetch_playlists_for_tracks (rekordbox_reader)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from djlib.rekordbox_reader import fetch_playlists_for_tracks


def _make_content(cid: int, folder: str, filename: str) -> MagicMock:
    c = MagicMock()
    c.ID = cid
    c.FolderPath = folder
    c.FileNameL = filename
    return c


def _make_song_playlist(content_id: int, playlist_id: int) -> MagicMock:
    sp = MagicMock()
    sp.ContentID = content_id
    sp.PlaylistID = playlist_id
    return sp


def _make_playlist(pid: int, name: str) -> MagicMock:
    pl = MagicMock()
    pl.ID = pid
    pl.Name = name
    return pl


def _mock_db(contents, song_pls, playlists):
    """Return a mock Rekordbox6Database with the given model objects."""
    db = MagicMock()
    session = db.session

    def query_side_effect(model):
        q = MagicMock()
        q.filter.return_value = q
        q.all.return_value = []

        model_name = getattr(model, "__name__", str(model))
        if "Content" in model_name:
            q.all.return_value = contents
            q.filter.return_value = q
        elif "SongPlaylist" in model_name:
            q.all.return_value = song_pls
        elif model_name == "DjmdPlaylist":
            q.all.return_value = playlists
        return q

    session.query.side_effect = query_side_effect
    return db


# ── No DB available ───────────────────────────────────────────────────────────

def test_returns_empty_when_no_db():
    result = fetch_playlists_for_tracks(
        [{"track_id": "tid-1", "rekordbox_id": "42", "file_path": ""}],
        db_path=Path("/nonexistent/master6.db"),
    )
    assert result == {}


def test_returns_empty_for_empty_entries():
    result = fetch_playlists_for_tracks([], db_path=Path("/nonexistent.db"))
    assert result == {}


# ── Lookup by rekordbox_id ────────────────────────────────────────────────────

def test_lookup_by_rekordbox_id():
    content = _make_content(42, "/Music/", "track.aiff")
    sp = _make_song_playlist(42, 10)
    pl = _make_playlist(10, "PornoStar")

    db = _mock_db([content], [sp], [pl])

    with patch("pyrekordbox.Rekordbox6Database", return_value=db):
        with patch("djlib.rekordbox_reader._default_db_path", return_value=Path("/fake.db")):
            result = fetch_playlists_for_tracks(
                [{"track_id": "tid-1", "rekordbox_id": "42", "file_path": ""}]
            )

    assert result == {"tid-1": ["PornoStar"]}


def test_multiple_playlists_for_one_track():
    content = _make_content(42, "/Music/", "track.aiff")
    sp1 = _make_song_playlist(42, 10)
    sp2 = _make_song_playlist(42, 11)
    pl1 = _make_playlist(10, "PornoStar")
    pl2 = _make_playlist(11, "UltimateSet")

    db = _mock_db([content], [sp1, sp2], [pl1, pl2])

    with patch("pyrekordbox.Rekordbox6Database", return_value=db):
        with patch("djlib.rekordbox_reader._default_db_path", return_value=Path("/fake.db")):
            result = fetch_playlists_for_tracks(
                [{"track_id": "tid-1", "rekordbox_id": "42", "file_path": ""}]
            )

    assert set(result["tid-1"]) == {"PornoStar", "UltimateSet"}


def test_track_not_in_any_playlist_not_in_result():
    content = _make_content(42, "/Music/", "track.aiff")

    db = _mock_db([content], [], [])  # no SongPlaylist entries

    with patch("pyrekordbox.Rekordbox6Database", return_value=db):
        with patch("djlib.rekordbox_reader._default_db_path", return_value=Path("/fake.db")):
            result = fetch_playlists_for_tracks(
                [{"track_id": "tid-1", "rekordbox_id": "42", "file_path": ""}]
            )

    assert result == {}


def test_track_not_in_rekordbox_returns_empty():
    db = _mock_db([], [], [])  # content query returns nothing

    with patch("pyrekordbox.Rekordbox6Database", return_value=db):
        with patch("djlib.rekordbox_reader._default_db_path", return_value=Path("/fake.db")):
            result = fetch_playlists_for_tracks(
                [{"track_id": "tid-1", "rekordbox_id": "99", "file_path": ""}]
            )

    assert result == {}


# ── Fallback: filename lookup ─────────────────────────────────────────────────

def test_lookup_by_filename_fallback():
    content = _make_content(55, "/Unsorted/", "Artist - Title.aiff")
    sp = _make_song_playlist(55, 20)
    pl = _make_playlist(20, "Incoming")

    db = _mock_db([content], [sp], [pl])

    with patch("pyrekordbox.Rekordbox6Database", return_value=db):
        with patch("djlib.rekordbox_reader._default_db_path", return_value=Path("/fake.db")):
            result = fetch_playlists_for_tracks(
                [{"track_id": "tid-2", "rekordbox_id": "", "file_path": "/Unsorted/Artist - Title.aiff"}]
            )

    assert result == {"tid-2": ["Incoming"]}


# ── pyrekordbox not installed ─────────────────────────────────────────────────

def test_graceful_when_pyrekordbox_missing():
    with patch.dict("sys.modules", {"pyrekordbox": None, "pyrekordbox.db6": None}):
        # Should not raise, just return {}
        result = fetch_playlists_for_tracks(
            [{"track_id": "tid-1", "rekordbox_id": "42", "file_path": ""}],
            db_path=Path("/fake.db"),
        )
    assert result == {}
