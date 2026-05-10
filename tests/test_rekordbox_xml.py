"""Tests for djlib.rekordbox_xml — rekordbox.xml generator."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from djlib.rekordbox_xml import (
    _path_to_location,
    _position_marks,
    _rgb,
    write_rekordbox_xml,
)


# ── _path_to_location ─────────────────────────────────────────────────────────


def test_path_to_location_simple():
    loc = _path_to_location("/Users/dj/Gigs/friday/audio/tid.mp3")
    assert loc == "file://localhost/Users/dj/Gigs/friday/audio/tid.mp3"


def test_path_to_location_spaces():
    loc = _path_to_location("/Users/dj/Music Unsorted/track name.mp3")
    assert "%20" in loc
    assert "file://localhost" in loc


def test_path_to_location_unicode():
    loc = _path_to_location("/Users/dj/Música/Beyoncé.mp3")
    assert "file://localhost" in loc
    assert "Bey" in loc  # encoded but parseable


# ── _rgb ──────────────────────────────────────────────────────────────────────


def test_rgb_packed_int():
    # 0xFF8000 = orange (255, 128, 0)
    r, g, b = _rgb(0xFF8000, 0)
    assert (r, g, b) == (255, 128, 0)


def test_rgb_zero_uses_slot_default():
    r, g, b = _rgb(0, 0)
    assert (r, g, b) == (237, 20, 47)  # slot 0 default = red


def test_rgb_zero_slot_out_of_range():
    r, g, b = _rgb(0, 99)
    assert (r, g, b) == (255, 255, 255)  # fallback white


# ── _position_marks ───────────────────────────────────────────────────────────


def _cues_json(cues: list) -> str:
    return json.dumps({"v": 1, "cues": cues})


def test_position_marks_memory_cue():
    cue = {"kind": 0, "pos_ms": 1234, "label": "Intro", "color": 0, "out_ms": 0}
    marks = _position_marks(_cues_json([cue]))
    assert len(marks) == 1
    m = marks[0]
    assert m.attrib["Num"] == "-1"
    assert m.attrib["Type"] == "0"
    assert m.attrib["Start"] == "1.234"
    assert m.attrib["Name"] == "Intro"
    # Memory cues have no Red/Green/Blue
    assert "Red" not in m.attrib


def test_position_marks_hot_cue():
    cue = {"kind": 1, "pos_ms": 5000, "label": "Drop", "color": 0xFF0000, "out_ms": 0}
    marks = _position_marks(_cues_json([cue]))
    assert len(marks) == 1
    m = marks[0]
    assert m.attrib["Num"] == "0"  # kind=1 → slot 0
    assert m.attrib["Red"] == "255"
    assert m.attrib["Green"] == "0"
    assert m.attrib["Blue"] == "0"


def test_position_marks_loop():
    cue = {"kind": 2, "pos_ms": 8000, "out_ms": 12000, "label": "", "color": 0,
           "active_loop": True}
    marks = _position_marks(_cues_json([cue]))
    m = marks[0]
    assert m.attrib["Type"] == "4"
    assert m.attrib["End"] == "12.000"
    assert m.attrib["Start"] == "8.000"


def test_position_marks_empty_json():
    assert _position_marks("") == []


def test_position_marks_multiple():
    cues = [
        {"kind": 0, "pos_ms": 0,    "label": "Start", "color": 0, "out_ms": 0},
        {"kind": 1, "pos_ms": 3000, "label": "A",     "color": 0, "out_ms": 0},
        {"kind": 2, "pos_ms": 6000, "label": "B",     "color": 0, "out_ms": 0},
    ]
    marks = _position_marks(_cues_json(cues))
    assert len(marks) == 3


# ── write_rekordbox_xml ───────────────────────────────────────────────────────


def _make_track(tmp_path: Path, tid: str, title: str, artist: str) -> dict:
    audio = tmp_path / f"{tid}.mp3"
    audio.write_bytes(b"fake audio" * 100)
    return {
        "track_id":        tid,
        "title":           title,
        "artist":          artist,
        "bpm":             "128",
        "key":             "5A",
        "rating":          "4",
        "play_count":      "12",
        "duration_seconds":"360",
        "added_date":      "2026-05-11",
        "genre":           "Tech House",
        "local_path":      str(audio),
        "cue_points_rb":   "",
    }


def test_write_rekordbox_xml_valid_xml(tmp_path):
    tracks = [_make_track(tmp_path, "t1", "Track One", "Artist A")]
    out = tmp_path / "rekordbox.xml"
    write_rekordbox_xml(tracks, "friday", out)
    assert out.exists()
    ET.parse(out)  # must be valid XML


def test_write_rekordbox_xml_structure(tmp_path):
    tracks = [
        _make_track(tmp_path, "t1", "Track One", "Artist A"),
        _make_track(tmp_path, "t2", "Track Two", "Artist B"),
    ]
    out = tmp_path / "rekordbox.xml"
    write_rekordbox_xml(tracks, "friday", out)

    tree = ET.parse(out)
    root = tree.getroot()
    assert root.tag == "DJ_PLAYLISTS"

    collection = root.find("COLLECTION")
    assert collection is not None
    assert collection.attrib["Entries"] == "2"
    track_els = collection.findall("TRACK")
    assert len(track_els) == 2


def test_write_rekordbox_xml_location_format(tmp_path):
    tracks = [_make_track(tmp_path, "t1", "T", "A")]
    out = tmp_path / "rekordbox.xml"
    write_rekordbox_xml(tracks, "friday", out)

    tree = ET.parse(out)
    track_el = tree.getroot().find(".//TRACK[@TrackID='1']")
    loc = track_el.attrib["Location"]
    assert loc.startswith("file://localhost/")
    assert loc.endswith(".mp3")


def test_write_rekordbox_xml_key_mapping(tmp_path):
    tracks = [_make_track(tmp_path, "t1", "T", "A")]
    tracks[0]["key"] = "8A"  # A minor
    out = tmp_path / "rekordbox.xml"
    write_rekordbox_xml(tracks, "friday", out)

    tree = ET.parse(out)
    track_el = tree.getroot().find(".//TRACK[@TrackID='1']")
    assert track_el.attrib.get("Tonality") == "Am"


def test_write_rekordbox_xml_unknown_key_omits_tonality(tmp_path):
    tracks = [_make_track(tmp_path, "t1", "T", "A")]
    tracks[0]["key"] = "???"
    out = tmp_path / "rekordbox.xml"
    write_rekordbox_xml(tracks, "friday", out)

    tree = ET.parse(out)
    track_el = tree.getroot().find(".//TRACK[@TrackID='1']")
    assert "Tonality" not in track_el.attrib


def test_write_rekordbox_xml_playlist_node(tmp_path):
    tracks = [_make_track(tmp_path, "t1", "T", "A")]
    out = tmp_path / "rekordbox.xml"
    write_rekordbox_xml(tracks, "friday-2026-05-15", out)

    tree = ET.parse(out)
    gig_node = tree.getroot().find(".//NODE[@Name='friday-2026-05-15']")
    assert gig_node is not None
    assert gig_node.attrib["Entries"] == "1"
    assert gig_node.find("TRACK") is not None


def test_write_rekordbox_xml_with_cue_points(tmp_path):
    tracks = [_make_track(tmp_path, "t1", "T", "A")]
    tracks[0]["cue_points_rb"] = json.dumps({"v": 1, "cues": [
        {"kind": 1, "pos_ms": 1000, "label": "Kick", "color": 0xFF0000, "out_ms": 0},
        {"kind": 0, "pos_ms": 5000, "label": "Break", "color": 0, "out_ms": 0},
    ]})
    out = tmp_path / "rekordbox.xml"
    write_rekordbox_xml(tracks, "friday", out)

    tree = ET.parse(out)
    track_el = tree.getroot().find(".//TRACK[@TrackID='1']")
    marks = track_el.findall("POSITION_MARK")
    assert len(marks) == 2


def test_write_rekordbox_xml_empty_tracks(tmp_path):
    out = tmp_path / "rekordbox.xml"
    write_rekordbox_xml([], "friday", out)
    tree = ET.parse(out)
    assert tree.getroot().find("COLLECTION").attrib["Entries"] == "0"


def test_write_rekordbox_xml_rating_scale(tmp_path):
    tracks = [_make_track(tmp_path, "t1", "T", "A")]
    tracks[0]["rating"] = "5"
    out = tmp_path / "rekordbox.xml"
    write_rekordbox_xml(tracks, "friday", out)

    tree = ET.parse(out)
    track_el = tree.getroot().find(".//TRACK[@TrackID='1']")
    # 5 stars → 255
    assert track_el.attrib["Rating"] == "255"


def test_write_rekordbox_xml_xml_declaration(tmp_path):
    tracks = [_make_track(tmp_path, "t1", "T", "A")]
    out = tmp_path / "rekordbox.xml"
    write_rekordbox_xml(tracks, "friday", out)
    content = out.read_bytes()
    assert content.startswith(b"<?xml")


# ── Integration: rekordbox.xml written by run_gig_prep_copy ──────────────────


def test_gig_prep_copy_writes_rekordbox_xml(tmp_path):
    """run_gig_prep_copy must produce rekordbox.xml after COMMIT."""
    import csv as csv_mod
    from djlib.gig import GigDir, ResolveResult, run_gig_prep_copy

    src = tmp_path / "src" / "a.mp3"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"audio" * 1000)

    csv_path = tmp_path / "data" / "library.csv"
    csv_path.parent.mkdir(parents=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv_mod.DictWriter(f, fieldnames=[
            "track_id", "live_location", "live_path", "old_full_path",
            "artist", "title", "bpm", "key", "cue_points_rb",
        ])
        w.writeheader()
        w.writerow({
            "track_id": "t1", "live_location": "nas", "live_path": "",
            "old_full_path": str(src), "artist": "DJ Test", "title": "Test Track",
            "bpm": "128", "key": "8A", "cue_points_rb": "",
        })

    resolved = [ResolveResult(str(src), "t1", {"track_id": "t1"}, "exact")]
    gig_dir = GigDir(gig_id="xml-test", root=tmp_path / "Gigs")

    run_gig_prep_copy("xml-test", resolved, csv_path, gig_dir)

    xml_path = gig_dir.path / "rekordbox.xml"
    assert xml_path.exists(), "rekordbox.xml not written"
    ET.parse(xml_path)  # valid XML
