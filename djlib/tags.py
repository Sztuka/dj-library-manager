from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Tuple
import re
import mutagen  # używamy tylko mutagen.File(...)

def _first_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        if not v:
            return ""
        v = v[0]
    return str(v)

def _extract_version_from_title(title: str) -> Tuple[str, str]:
    title = title.strip()
    m = re.search(r"\(([^)]+)\)\s*$", title)
    if not m:
        return title, ""
    version = m.group(1).strip()
    clean = title[: m.start()].strip()
    return clean, version

_CAM_MAJOR = {
    "C": "8B", "G": "9B", "D": "10B", "A": "11B", "E": "12B",
    "B": "1B", "F#": "2B", "C#": "3B", "G#": "4B", "D#": "5B",
    "A#": "6B", "F": "7B",
}
_CAM_MINOR = {
    "A": "8A", "E": "9A", "B": "10A", "F#": "11A", "C#": "12A",
    "G#": "1A", "D#": "2A", "A#": "3A", "F": "4A", "C": "5A",
    "G": "6A", "D": "7A",
}
_FLAT_TO_SHARP = {"DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#"}

_CAM_PAT = re.compile(r"^\s*(\d{1,2})([ABab])\s*$")

def _to_camelot(key_raw: str) -> str:
    if not key_raw:
        return ""
    s = key_raw.strip()
    m = _CAM_PAT.match(s)
    if m:
        n, ab = m.groups()
        n = int(n)
        if 1 <= n <= 12:
            return f"{n}{ab.upper()}"

    s = s.upper().replace("MAJOR", "").replace("MINOR", "M").replace(" MIN", "M").strip()
    s = s.replace("MOLL", "M").replace("DUR", "").strip()
    s = s.replace("MIN", "M").replace("MAJ", "")
    s = s.replace("H", "B")   # niemieckie H -> B
    s = s.replace("♭", "B").replace("♯", "#")
    s = re.sub(r"([A-G])B", lambda m: _FLAT_TO_SHARP.get(m.group(0).upper(), m.group(0)), s)

    minor = s.endswith("M")
    base = s[:-1] if minor else s
    base = base.strip()
    if not base:
        return ""
    return _CAM_MINOR.get(base, "") if minor else _CAM_MAJOR.get(base, "")

def read_tags(path: Path) -> Dict[str, str]:
    """
    Zwraca:
    artist, title, version_info, bpm, key_camelot, energy_hint, genre, comment
    """
    f = mutagen.File(str(path), easy=True)  # type: ignore[no-any-return]
    tags: Dict[str, Any] = getattr(f, "tags", {}) or {}

    artist = _first_str(tags.get("artist")).strip()
    title = _first_str(tags.get("title")).strip()
    genre = _first_str(tags.get("genre")).strip()
    comment = _first_str(tags.get("comment")).strip()
    bpm = _first_str(tags.get("bpm")).strip()

    key_candidates = [
        _first_str(tags.get("initialkey")),
        _first_str(tags.get("key")),
        _first_str(tags.get("tkey")),
    ]
    key_camelot = ""
    for k in key_candidates:
        cam = _to_camelot(k)
        if cam:
            key_camelot = cam
            break

    version_info = ""
    if title:
        title, version_info = _extract_version_from_title(title)

    energy_hint = (_first_str(tags.get("energy")) or _first_str(tags.get("grouping"))).strip()

    return {
        "artist": artist,
        "title": title,
        "version_info": version_info,
        "bpm": bpm,
        "key_camelot": key_camelot,
        "energy_hint": energy_hint,
        "genre": genre,
        "comment": comment,
    }
