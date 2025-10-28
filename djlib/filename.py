from __future__ import annotations
import re
from pathlib import Path

_ILLEGAL = r'[\/\\\:\*\?"<>\|]'

def build_final_filename(artist: str, title: str, version_info: str, key_camelot: str, bpm: str, ext: str) -> str:
    vi = (version_info or "").strip() or "Original Mix"
    k = (key_camelot or "").strip() or "??"
    b = (bpm or "").strip() or "??"
    a = (artist or "Unknown Artist").strip()
    t = (title or "Unknown Title").strip()

    name = f"{a} - {t} ({vi}) [{k} {b}]{ext}"
    # wyczyść nielegalne znaki w nazwie
    return re.sub(_ILLEGAL, "-", name)

def extension_for(path: Path) -> str:
    return path.suffix or ".mp3"
