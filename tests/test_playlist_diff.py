"""Tests for playlist diff logic (server-side, no Rekordbox DB needed)."""
from __future__ import annotations

import pytest
from djlib.review.server import _build_playlist_diff


def _lib_row(track_id, rb_id, playlists="", artist="", title=""):
    return {
        "track_id": track_id,
        "rekordbox_id": rb_id,
        "playlists": playlists,
        "artist": artist or f"Artist {track_id}",
        "title": title or f"Title {track_id}",
    }


def _rb_track(rb_id, artist="", title=""):
    return {"rb_id": rb_id, "artist": artist or f"RB Artist {rb_id}", "title": title or f"RB Title {rb_id}"}


# ── State: "both" ─────────────────────────────────────────────────────────────

def test_both_state_when_in_rb_and_djlib():
    rb_pls = {"PornoStar": [_rb_track("100")]}
    lib = [_lib_row("t1", "100", playlists="PornoStar")]
    diff = _build_playlist_diff(rb_pls, lib)
    entry = diff["playlists"]["PornoStar"][0]
    assert entry["state"] == "both"
    assert entry["track_id"] == "t1"
    assert entry["rekordbox_id"] == "100"


# ── State: "rb_only" ──────────────────────────────────────────────────────────

def test_rb_only_when_in_rb_but_not_tagged_in_djlib():
    rb_pls = {"PornoStar": [_rb_track("100")]}
    lib = [_lib_row("t1", "100", playlists="")]  # in library but not tagged
    diff = _build_playlist_diff(rb_pls, lib)
    entry = diff["playlists"]["PornoStar"][0]
    assert entry["state"] == "rb_only"
    assert entry["track_id"] == "t1"


def test_rb_only_when_tagged_in_different_playlist():
    rb_pls = {"PornoStar": [_rb_track("100")]}
    lib = [_lib_row("t1", "100", playlists="OtherPlaylist")]
    diff = _build_playlist_diff(rb_pls, lib)
    entry = diff["playlists"]["PornoStar"][0]
    assert entry["state"] == "rb_only"


# ── State: "rb_only_unknown" ──────────────────────────────────────────────────

def test_rb_only_unknown_when_not_in_library():
    rb_pls = {"PornoStar": [_rb_track("999", artist="Unknown", title="Unknown Track")]}
    lib = []
    diff = _build_playlist_diff(rb_pls, lib)
    entry = diff["playlists"]["PornoStar"][0]
    assert entry["state"] == "rb_only_unknown"
    assert entry["track_id"] is None
    assert entry["artist"] == "Unknown"
    assert entry["title"] == "Unknown Track"


# ── State: "djlib_only" ───────────────────────────────────────────────────────

def test_djlib_only_when_tagged_but_not_in_rb():
    rb_pls = {}  # no RB playlists
    lib = [_lib_row("t1", "100", playlists="PornoStar")]
    diff = _build_playlist_diff(rb_pls, lib)
    entries = diff["playlists"].get("PornoStar", [])
    assert len(entries) == 1
    assert entries[0]["state"] == "djlib_only"
    assert entries[0]["track_id"] == "t1"


def test_djlib_only_track_in_rb_playlist_different_name():
    rb_pls = {"HouseSet": [_rb_track("100")]}
    lib = [_lib_row("t1", "100", playlists="PornoStar")]
    diff = _build_playlist_diff(rb_pls, lib)
    # Track is in HouseSet in RB (rb_only state) and PornoStar in djlib (djlib_only)
    house_entry = diff["playlists"]["HouseSet"][0]
    assert house_entry["state"] == "rb_only"
    porno_entries = diff["playlists"].get("PornoStar", [])
    assert any(e["state"] == "djlib_only" for e in porno_entries)


# ── rb_only_playlists ─────────────────────────────────────────────────────────

def test_rb_only_playlists_when_no_djlib_tag():
    rb_pls = {"RBOnly": [_rb_track("100")], "Both": [_rb_track("200")]}
    lib = [_lib_row("t2", "200", playlists="Both")]
    diff = _build_playlist_diff(rb_pls, lib)
    assert "RBOnly" in diff["rb_only_playlists"]
    assert "Both" not in diff["rb_only_playlists"]


def test_rb_only_playlists_empty_when_all_tagged():
    rb_pls = {"PornoStar": [_rb_track("100")]}
    lib = [_lib_row("t1", "100", playlists="PornoStar")]
    diff = _build_playlist_diff(rb_pls, lib)
    assert diff["rb_only_playlists"] == []


# ── Multi-playlist tracks ─────────────────────────────────────────────────────

def test_track_in_multiple_rb_playlists():
    rb_pls = {
        "Playlist A": [_rb_track("100")],
        "Playlist B": [_rb_track("100")],
    }
    lib = [_lib_row("t1", "100", playlists="Playlist A")]
    diff = _build_playlist_diff(rb_pls, lib)
    a_state = diff["playlists"]["Playlist A"][0]["state"]
    b_state = diff["playlists"]["Playlist B"][0]["state"]
    assert a_state == "both"
    assert b_state == "rb_only"


def test_pipe_separated_playlists_parsed():
    rb_pls = {"A": [_rb_track("100")], "B": [_rb_track("100")]}
    lib = [_lib_row("t1", "100", playlists="A|B")]
    diff = _build_playlist_diff(rb_pls, lib)
    assert diff["playlists"]["A"][0]["state"] == "both"
    assert diff["playlists"]["B"][0]["state"] == "both"


# ── Artist/title fallback ─────────────────────────────────────────────────────

def test_artist_title_from_library_preferred_over_rb():
    rb_pls = {"PornoStar": [_rb_track("100", artist="RB Artist", title="RB Title")]}
    lib = [_lib_row("t1", "100", playlists="PornoStar", artist="DJ Artist", title="Good Track")]
    diff = _build_playlist_diff(rb_pls, lib)
    entry = diff["playlists"]["PornoStar"][0]
    assert entry["artist"] == "DJ Artist"
    assert entry["title"] == "Good Track"


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_empty_inputs():
    diff = _build_playlist_diff({}, [])
    assert diff["playlists"] == {}
    assert diff["rb_only_playlists"] == []


def test_track_without_rekordbox_id_in_library_skipped():
    rb_pls = {"PornoStar": [_rb_track("100")]}
    lib = [
        _lib_row("t1", "", playlists="PornoStar"),  # no rb_id
        _lib_row("t2", "100", playlists="PornoStar"),
    ]
    diff = _build_playlist_diff(rb_pls, lib)
    entries = diff["playlists"]["PornoStar"]
    # Only t2 matches via rb_id
    assert len([e for e in entries if e["state"] == "both"]) == 1


def test_track_without_rb_id_appears_as_djlib_only():
    rb_pls = {}
    lib = [_lib_row("t1", "", playlists="PornoStar")]
    diff = _build_playlist_diff(rb_pls, lib)
    entries = diff["playlists"].get("PornoStar", [])
    assert len(entries) == 1
    assert entries[0]["state"] == "djlib_only"
    assert entries[0]["rekordbox_id"] is None


def test_duplicate_lib_rows_no_duplicate_djlib_only():
    rb_pls = {}
    lib = [
        _lib_row("t1", "100", playlists="PornoStar"),
        _lib_row("t1", "100", playlists="PornoStar"),  # duplicate row
    ]
    diff = _build_playlist_diff(rb_pls, lib)
    entries = diff["playlists"].get("PornoStar", [])
    assert len(entries) == 1  # deduplicated
