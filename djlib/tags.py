from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Tuple
import re
import unicodedata
from mutagen import File as MutFile  # type: ignore
from djlib.filename import split_title_and_version

def _first_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        if not v:
            return ""
        v = v[0]
    return str(v)


def _normalize_token(text: str) -> str:
    """Lowercase alnum slug without diacritics for safe comparisons."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", ascii_only.lower())


def _strip_artist_prefix(title: str, artist: str) -> str:
    """Drop leading "artist - " from title when tags duplicate artist."""
    if not title or not artist:
        return title
    if " - " not in title:
        return title
    prefix, rest = title.split(" - ", 1)
    if _normalize_token(prefix) == _normalize_token(artist):
        return rest.strip()
    return title

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

_CAM_PAT = re.compile(r"^\s*(\d{1,2})([ABabdDmM])\s*$")

def _to_camelot(key_raw: str) -> str:
    if not key_raw:
        return ""
    s = key_raw.strip()
    m = _CAM_PAT.match(s)
    if m:
        n, ab = m.groups()
        n = int(n)
        if 1 <= n <= 12:
            # Convert d->B (major), m->A (minor), and normalize case
            if ab.lower() == 'd':
                ab = 'B'
            elif ab.lower() == 'm':
                ab = 'A'
            else:
                ab = ab.upper()
            return f"{n}{ab}"

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
    f = MutFile(str(path), easy=True)  # type: ignore[no-any-return]
    tags: Dict[str, Any] = getattr(f, "tags", {}) or {}

    artist = _first_str(tags.get("artist")).strip()
    title = _first_str(tags.get("title")).strip()
    genre = _first_str(tags.get("genre")).strip()
    comment = _first_str(tags.get("comment")).strip()
    bpm = _first_str(tags.get("bpm")).strip()
    
    # Read year from date or year tag
    # Easy mode doesn't expose date/year for FLAC, need raw access
    year = ""
    album = ""
    try:
        raw = MutFile(str(path))
        if raw and getattr(raw, "tags", None):
            raw_tags = raw.tags
            
            # For ID3 tags (MP3), read TDRC frame directly
            if hasattr(raw_tags, '__getitem__') and 'TDRC' in raw_tags:
                tdrc = raw_tags['TDRC']
                if tdrc and hasattr(tdrc, 'text') and tdrc.text:
                    year_text = str(tdrc.text[0])
                    year = year_text.split("-")[0].split("/")[0][:4]
                    if year.isdigit() and len(year) == 4:
                        pass  # year is set
                    else:
                        year = ""
            
            # Fallback: Try various date/year tag names (Vorbis comments for FLAC)
            if not year:
                year_candidates = [
                    _first_str(raw_tags.get("originaldate")),  # Original release date (priority)
                    _first_str(raw_tags.get("year")),
                    _first_str(raw_tags.get("date")),
                    _first_str(raw_tags.get("releasedate")),
                ]
                for candidate in year_candidates:
                    candidate = (candidate or "").strip()
                    if candidate:
                        # Extract year from formats: "1958", "1958-03-15", "1958/03/15"
                        year = candidate.split("-")[0].split("/")[0][:4]
                        if year.isdigit() and len(year) == 4:
                            break
            
            # Read album from raw tags (ID3 TALB frame or Vorbis album tag)
            if hasattr(raw_tags, '__getitem__') and 'TALB' in raw_tags:
                talb = raw_tags['TALB']
                if talb and hasattr(talb, 'text') and talb.text:
                    album = str(talb.text[0]).strip()
            
            if not album:
                album_candidates = [
                    _first_str(raw_tags.get("album")),
                ]
                for candidate in album_candidates:
                    candidate = (candidate or "").strip()
                    if candidate:
                        album = candidate
                        break
    except Exception:
        pass

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

    # Fallback: try raw container frames (e.g., ID3 TKEY) if Easy tags didn't expose the key
    if not key_camelot:
        try:
            raw = MutFile(str(path))
            if raw and getattr(raw, "tags", None):
                # ID3 TKEY
                try:
                    tkey = raw.tags.get("TKEY")  # type: ignore[call-arg]
                    if tkey:
                        # Some mutagen frames hold .text list
                        vals = getattr(tkey, "text", None)
                        if vals:
                            cam = _to_camelot(_first_str(vals))
                            if cam:
                                key_camelot = cam
                except Exception:
                    pass
                # MP4 iTunes custom key (----:com.apple.iTunes:initialkey)
                try:
                    if getattr(raw, "tags", None):
                        for k in ("----:com.apple.iTunes:initialkey", "----:com.apple.iTunes:KEY"):
                            if k in raw.tags:  # type: ignore[operator]
                                v = _first_str(raw.tags.get(k))  # type: ignore[call-arg]
                                cam = _to_camelot(v)
                                if cam:
                                    key_camelot = cam
                                    break
                except Exception:
                    pass
        except Exception:
            pass

    title, version_info = split_title_and_version(title)
    title = _strip_artist_prefix(title, artist)

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
        "year": year,
        "album": album,
    }

_AIFF_EXTS = {".aiff", ".aif"}


def _write_tags_id3_frames(path: Path, updates: Dict[str, str]) -> None:
    """Write tags to AIFF (and any ID3-only format) using raw ID3 Frame objects.

    mutagen's easy=True mode doesn't work reliably for AIFF — it exposes raw
    ID3 tags which require Frame instances, not plain strings.
    """
    from mutagen.id3 import (
        TALB, TBPM, TCON, TDRC, TIT1, TIT2, TKEY, TPE1,
        ID3NoHeaderError,
    )
    try:
        from mutagen.aiff import AIFF
        audio = AIFF(str(path))
    except Exception as exc:
        raise ValueError(f"Cannot open AIFF: {path}: {exc}") from exc

    if audio.tags is None:
        audio.add_tags()

    frame_map = {
        "artist":   lambda v: TPE1(encoding=3, text=[v]),
        "title":    lambda v: TIT2(encoding=3, text=[v]),
        "genre":    lambda v: TCON(encoding=3, text=[v]),
        "year":     lambda v: TDRC(encoding=3, text=[v]),
        "album":    lambda v: TALB(encoding=3, text=[v]),
        "grouping": lambda v: TIT1(encoding=3, text=[v]),
        "bpm":      lambda v: TBPM(encoding=3, text=[v]),
        "key_camelot": lambda v: TKEY(encoding=3, text=[v]),
    }

    for our_key, make_frame in frame_map.items():
        if our_key not in updates:
            continue
        val = str(updates[our_key] or "")
        frame_id = make_frame("x").FrameID  # get e.g. "TPE1"
        if val:
            audio.tags[frame_id] = make_frame(val)
        else:
            audio.tags.delall(frame_id)

    audio.save()


def write_tags(path: Path, updates: Dict[str, str]) -> None:
    """Write metadata tags to an audio file.

    Supported keys: artist, title, bpm, key_camelot, genre, year, album, grouping.
    AIFF files use raw ID3 Frame objects (EasyID3 is MP3-only in mutagen).
    """
    if path.suffix.lower() in _AIFF_EXTS:
        _write_tags_id3_frames(path, updates)
        return

    f = MutFile(str(path), easy=True)
    if f is None:
        raise ValueError(f"Nie można otworzyć pliku audio: {path}")

    if f.tags is None:
        f.add_tags()

    tags = f.tags

    mapping = {
        "artist": "artist",
        "title": "title",
        "bpm": "bpm",
        "genre": "genre",
        "comment": "comment",
        "year": "date",
        "album": "album",
        "grouping": "grouping",
    }

    for our_key, mutagen_key in mapping.items():
        if our_key in updates:
            val = updates[our_key]
            if val or our_key in ["album", "year"]:
                tags[mutagen_key] = val

    if "key_camelot" in updates:
        key_val = updates["key_camelot"]
        if key_val:
            try:
                tags["key"] = key_val
            except Exception:
                pass

    f.save()

    if "key_camelot" in updates and updates["key_camelot"]:
        key_val = updates["key_camelot"]
        try:
            raw = MutFile(str(path))
            if raw and hasattr(raw, 'tags') and raw.tags:
                from mutagen.id3 import TKEY as ID3TKEY
                try:
                    raw.tags.add(ID3TKEY(encoding=3, text=key_val))
                    raw.save()
                except Exception:
                    pass
        except Exception:
            pass
