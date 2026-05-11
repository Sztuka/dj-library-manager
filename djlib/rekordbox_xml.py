"""Generate a Rekordbox-importable XML file from gig-prep track data.

Rekordbox can import the resulting file via File → Import → rekordbox xml.
The XML points at local MacBook paths so Rekordbox finds the copied files
without touching the NAS collection.

Pioneer's DJ_PLAYLISTS XML schema (v1.0.0):

    <DJ_PLAYLISTS>
      <COLLECTION>
        <TRACK TrackID="…" Name="…" Location="file://localhost/…">
          <POSITION_MARK Name="…" Type="0" Start="1.234" Num="0"
                         Red="40" Green="226" Blue="20"/>
        </TRACK>
      </COLLECTION>
      <PLAYLISTS>
        <NODE Type="0" Name="ROOT">
          <NODE Type="1" Name="<gig_id>" Entries="N">
            <TRACK Key="1"/>
          </NODE>
        </NODE>
      </PLAYLISTS>
    </DJ_PLAYLISTS>

POSITION_MARK fields:
  Type  0=cue/hot-cue, 4=loop
  Num   -1=memory cue, 0-7=hot cue slot
  Start position in seconds (float)
  End   loop end in seconds (only for Type=4)
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from djlib.cues.schema import deserialize_rb_cues


# ── Camelot → Rekordbox key notation ─────────────────────────────────────────

_CAMELOT_TO_RB: Dict[str, str] = {
    "1A": "Abm", "1B": "B",
    "2A": "Ebm", "2B": "F#",
    "3A": "Bbm", "3B": "Db",
    "4A": "Fm",  "4B": "Ab",
    "5A": "Cm",  "5B": "Eb",
    "6A": "Gm",  "6B": "Bb",
    "7A": "Dm",  "7B": "F",
    "8A": "Am",  "8B": "C",
    "9A": "Em",  "9B": "G",
    "10A": "Bm", "10B": "D",
    "11A": "F#m","11B": "A",
    "12A": "Dbm","12B": "E",
}


# ── File extension → Rekordbox Kind label ─────────────────────────────────────

_EXT_TO_KIND: Dict[str, str] = {
    ".mp3":  "MP3 File",
    ".flac": "FLAC File",
    ".aif":  "AIFF File",
    ".aiff": "AIFF File",
    ".wav":  "WAV File",
    ".m4a":  "M4A File",
    ".ogg":  "OGG File",
    ".mp4":  "MP4 File",
}


# ── Hot-cue default colors (packed 0xRRGGBB) when color=0 ────────────────────

_HOTCUE_DEFAULT_COLORS = [
    (237, 20,  47),   # slot 0 — red
    (40,  226, 20),   # slot 1 — green
    (109, 177, 232),  # slot 2 — blue
    (255, 165, 0),    # slot 3 — orange
    (164, 69,  255),  # slot 4 — purple
    (255, 255, 0),    # slot 5 — yellow
    (0,   255, 255),  # slot 6 — cyan
    (255, 20,  147),  # slot 7 — pink
]


def _rgb(color_int: int, hotcue_slot: int) -> tuple[int, int, int]:
    """Unpack a Rekordbox color int to (R, G, B).

    The stored color is a 24-bit packed int (0xRRGGBB). When 0 (no color
    set), fall back to the per-slot default so the XML is colourful and
    readable in Rekordbox.
    """
    if color_int:
        r = (color_int >> 16) & 0xFF
        g = (color_int >> 8) & 0xFF
        b = color_int & 0xFF
        return (r, g, b)
    if 0 <= hotcue_slot < len(_HOTCUE_DEFAULT_COLORS):
        return _HOTCUE_DEFAULT_COLORS[hotcue_slot]
    return (255, 255, 255)


def _path_to_location(path: str) -> str:
    """Convert an absolute filesystem path to a rekordbox.xml Location URL.

    Rekordbox expects:  file://localhost/Users/dj/Gigs/friday/audio/tid.mp3
    Spaces and non-ASCII characters must be percent-encoded.
    """
    encoded = quote(path, safe="/")
    return f"file://localhost{encoded}"


def _file_kind(path: str) -> str:
    ext = Path(path).suffix.lower()
    return _EXT_TO_KIND.get(ext, "Unknown")


def _position_marks(cue_json: str) -> List[ET.Element]:
    """Convert cue_points_rb JSON to a list of POSITION_MARK elements."""
    cues = deserialize_rb_cues(cue_json)
    elements: List[ET.Element] = []

    for cue in cues:
        kind    = int(cue.get("kind", 0))
        pos_ms  = int(cue.get("pos_ms", 0))
        out_ms  = int(cue.get("out_ms", 0))
        label   = str(cue.get("label", "") or "")
        color   = int(cue.get("color", 0) or 0)

        # kind=0 → memory cue (Num=-1), kind=1-8 → hot cue (Num=0-7)
        is_hotcue = kind >= 1
        num       = kind - 1 if is_hotcue else -1

        is_loop   = bool(cue.get("active_loop")) or out_ms > 0
        cue_type  = "4" if is_loop else "0"

        start_s = pos_ms / 1000.0

        attribs: Dict[str, str] = {
            "Name":  label,
            "Type":  cue_type,
            "Start": f"{start_s:.3f}",
            "Num":   str(num),
        }

        if is_loop and out_ms > 0:
            attribs["End"] = f"{out_ms / 1000.0:.3f}"

        if is_hotcue:
            r, g, b = _rgb(color, num)
            attribs["Red"]   = str(r)
            attribs["Green"] = str(g)
            attribs["Blue"]  = str(b)

        elements.append(ET.Element("POSITION_MARK", attribs))

    return elements


# ── Main entry point ──────────────────────────────────────────────────────────


def write_rekordbox_xml(
    tracks: List[Dict[str, Any]],
    gig_id: str,
    output_path: Path,
) -> None:
    """Write a rekordbox.xml file for the given gig tracks.

    Args:
        tracks: list of dicts with keys:
            track_id, artist, title, bpm, key, rating, play_count,
            duration_seconds, added_date, local_path, cue_points_rb
        gig_id: used as the playlist node name
        output_path: destination path (e.g. ~/Gigs/friday/rekordbox.xml)
    """
    root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
    ET.SubElement(root, "PRODUCT", {
        "Name":    "rekordbox",
        "Version": "6.0.0",
        "Company": "Pioneer DJ",
    })

    collection = ET.SubElement(root, "COLLECTION", {"Entries": str(len(tracks))})

    playlist_keys: List[str] = []

    for idx, track in enumerate(tracks, start=1):
        track_id   = str(idx)  # sequential int key within this XML
        local_path = str(track.get("local_path", ""))
        bpm_raw    = track.get("bpm", "") or ""
        key_raw    = str(track.get("key", "") or "")
        rating_raw = track.get("rating", "") or ""
        dur_raw    = track.get("duration_seconds", "") or ""

        try:
            avg_bpm = f"{float(bpm_raw):.2f}"
        except (TypeError, ValueError):
            avg_bpm = "0.00"

        try:
            total_time = str(int(float(dur_raw)))
        except (TypeError, ValueError):
            total_time = "0"

        try:
            # Rekordbox rating: 0-255 where 51*star = value
            rb_rating = str(min(255, int(float(rating_raw)) * 51))
        except (TypeError, ValueError):
            rb_rating = "0"

        try:
            size = str(Path(local_path).stat().st_size) if local_path else "0"
        except OSError:
            size = "0"

        tonality = _CAMELOT_TO_RB.get(key_raw.strip().upper(), "")

        attribs: Dict[str, str] = {
            "TrackID":     track_id,
            "Name":        str(track.get("title", "") or ""),
            "Artist":      str(track.get("artist", "") or ""),
            "Album":       "",
            "Genre":       str(track.get("genre", "") or ""),
            "Kind":        _file_kind(local_path),
            "Size":        size,
            "TotalTime":   total_time,
            "BitRate":     "",
            "SampleRate":  "",
            "DateAdded":   str(track.get("added_date", "") or ""),
            "Remixer":     "",
            "Mix":         "",
            "Label":       "",
            "Grouping":    "",
            "Comments":    "",
            "PlayCount":   str(track.get("play_count", "0") or "0"),
            "Rating":      rb_rating,
            "Location":    _path_to_location(local_path),
            "Colour":      "0",
            "AverageBpm":  avg_bpm,
        }
        if tonality:
            attribs["Tonality"] = tonality

        track_el = ET.SubElement(collection, "TRACK", attribs)

        for pm in _position_marks(str(track.get("cue_points_rb", "") or "")):
            track_el.append(pm)

        playlist_keys.append(track_id)

    # Playlist node
    playlists = ET.SubElement(root, "PLAYLISTS")
    root_node = ET.SubElement(playlists, "NODE", {
        "Type":  "0",
        "Name":  "ROOT",
        "Count": "1",
    })
    gig_node = ET.SubElement(root_node, "NODE", {
        "Name":    gig_id,
        "Type":    "1",
        "KeyType": "0",
        "Entries": str(len(playlist_keys)),
    })
    for key in playlist_keys:
        ET.SubElement(gig_node, "TRACK", {"Key": key})

    # Write with XML declaration
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    with output_path.open("wb") as f:
        tree.write(f, encoding="utf-8", xml_declaration=True)
