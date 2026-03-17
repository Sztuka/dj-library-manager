"""Automatic bucket assignment logic (DEPRECATED).

⚠️  WARNING: This module is DEPRECATED as of November 2025.

Bucket-based path assignment has been replaced with simple logistics (LIBRARY/REJECT/ARCHIVE).
Genre/BPM heuristics should be used for genre classification only, not folder placement.

For new code, use:
- `djlib.genre_canonical` for genre resolution
- `djlib.logistics` for destination paths

See djlib/legacy/README.md for migration guide.
"""

from __future__ import annotations

from typing import Dict, Tuple, Optional, List
import warnings
from djlib.genre_canonical import CanonicalGenreResolver

# Issue deprecation warning
warnings.warn(
    "djlib.placement is deprecated. Use djlib.genre_canonical for genre classification.",
    DeprecationWarning,
    stacklevel=2
)


# Canonical genre resolver instance
resolver = CanonicalGenreResolver()

# Define canonical club genres (keys from genres.yml)
CLUB_GENRE_KEYS = {
    "HOUSE", "TECH_HOUSE", "MELODIC_TECHNO", "TECHNO", "HARD_TECHNO",
    "AFRO_HOUSE", "ELECTRO_SWING", "TRANCE", "PSYTRANCE", "DNB",
    "DUBSTEP", "BREAKBEAT", "UK_GARAGE", "DEEP_HOUSE",
    "PROGRESSIVE_HOUSE", "DISCO_HOUSE", "ELECTRO_HOUSE", "TRAP",
    "EURODANCE", "INDIE_DANCE",
}

# Vibe map using canonical keys
VIBE_MAP = [
    ({"RNB"}, "OPEN FORMAT/RNB"),
    ({"HIP_HOP"}, "OPEN FORMAT/HIP-HOP"),
    ({"LATIN_POP", "REGGAETON", "KUDURO"}, "OPEN FORMAT/LATIN REGGAETON"),
    ({"ROCK_N_ROLL"}, "OPEN FORMAT/ROCKNROLL"),
    ({"ROCK", "INDIE_ROCK", "PUNK", "POST_PUNK"}, "OPEN FORMAT/ROCK CLASSICS"),
    ({"FUNK"}, "OPEN FORMAT/FUNK"),
    ({"SOUL", "BLUES"}, "OPEN FORMAT/SOUL"),
    ({"DISCO"}, "OPEN FORMAT/DISCO"),
    ({"POP", "SYNTHPOP", "INDIE_POP"}, "OPEN FORMAT/POP"),
    ({"JAZZ"}, "OPEN FORMAT/JAZZ"),
    ({"NEW_WAVE", "SYNTHWAVE"}, "OPEN FORMAT/NEW WAVE"),
    ({"REGGAE", "DANCEHALL", "SKA"}, "OPEN FORMAT/REGGAE"),
    ({"AFROBEATS"}, "OPEN FORMAT/AFROBEATS"),
    ({"BALKAN"}, "OPEN FORMAT/BALKAN"),
    ({"AMBIENT", "IDM", "TRIP_HOP"}, "OPEN FORMAT/AMBIENT"),
]


REMIX_TOKENS = {"remix","edit","extended","club","rework","vip","bootleg","refix","mix"}

def _norm(s: str) -> str:
    return (s or "").strip().lower()

def _has_any(text: str, tokens: set[str]) -> bool:
    t = _norm(text)
    return any(tok in t for tok in tokens)



def _is_clubish_version(title: str, version_info: str) -> bool:
    if not version_info:
        # fallback: inspect parentheses inside title if version isn't provided separately
        import re as _re
        m = _re.findall(r"\(([^)]+)\)", title or "")
        if m:
            version_info = ", ".join(m)
    return _has_any(title, REMIX_TOKENS) or _has_any(version_info, REMIX_TOKENS)

def _parse_bpm(bpm: str) -> Optional[float]:
    try:
        return float(str(bpm).replace(",","."))
    except Exception:
        return None


def decide_bucket(row: Dict[str,str]) -> Tuple[Optional[str], float, str]:
    """
    Returns: (target_subfolder | None, confidence(0..1), reason)
    """
    artist = row.get("artist_canonical") or row.get("artist") or ""
    title  = row.get("title_canonical")  or row.get("title")  or ""
    version = row.get("version_info","") or row.get("version_suggest","") or ""
    raw_genre = row.get("genre","")
    era    = (row.get("era") or "").strip()
    bpmv   = _parse_bpm(row.get("bpm","")) or 0.0
    keyc   = (row.get("key_camelot") or "").strip().upper()

    # Resolve genre using canonical resolver
    resolved = resolver.resolve(raw_genre)
    genre_key = resolved[0] if resolved else None
    genre_label = resolved[1] if resolved else (raw_genre or "")

    # 1) CLUB: canonical genre, club version, or BPM
    is_club_genre = genre_key in CLUB_GENRE_KEYS
    title_mixed = row.get("title") or title
    is_club_version = _is_clubish_version(title_mixed, version)
    if is_club_genre or is_club_version or (bpmv >= 122 and genre_key in {"HOUSE", "TECH_HOUSE", "TRANCE", "DNB"}):
        # Map to specific club bucket
        if genre_key == "TECH_HOUSE":       return ("CLUB/TECH HOUSE", 0.95, "genre=tech house")
        if genre_key == "MELODIC_TECHNO":   return ("CLUB/MELODIC TECHNO", 0.95, "genre=melodic techno")
        if genre_key == "TECHNO":           return ("CLUB/TECHNO", 0.9, "genre=techno")
        if genre_key == "DNB":              return ("CLUB/DNB", 0.95, "genre=dnb")
        if genre_key == "TRANCE":           return ("CLUB/TRANCE", 0.9, "genre=trance")
        if genre_key == "AFRO_HOUSE":       return ("CLUB/AFRO HOUSE", 0.9, "genre=afro house")
        if genre_key == "ELECTRO_SWING":    return ("CLUB/ELECTRO SWING", 0.9, "genre=electro swing")
        if genre_key == "HOUSE" or is_club_version or bpmv >= 122:
            return ("CLUB/HOUSE", 0.8, f"fallback clubish (bpm={bpmv:.0f}, remix={is_club_version})")

    # 2) OPEN FORMAT / decade
    if era in {"70s","80s","90s","2000s","2010s"}:
        return (f"OPEN FORMAT/{era}", 0.9, f"era={era}")

    # 3) OPEN FORMAT / vibe (canonical keys)
    for keys, bucket in VIBE_MAP:
        if genre_key in keys:
            return (bucket, 0.75, f"vibe via genre={genre_label or 'n/a'}")

    # default - POP is new generic pop bucket
    if genre_key:
        return ("OPEN FORMAT/POP", 0.6, f"default pop (genre={genre_label})")
    return (None, 0.0, "undecided")
