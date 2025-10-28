from __future__ import annotations
from typing import Dict, Tuple, Optional, List
import re

CLUB_GENRES = {
    "house","tech house","tech-house","techhouse",
    "techno","melodic techno","melodic-techno","melodictechno",
    "dnb","drum and bass","drum & bass","drum n bass",
    "trance","afro house","afro-house","afrohouse",
    "electroswing","electro swing",
}

VIBE_MAP = [
    ({"rnb","r&b"},                                             "OPEN FORMAT/RNB"),
    ({"hip hop","hip-hop","rap","trap"},                       "OPEN FORMAT/HIP-HOP"),
    ({"latin","reggaeton","reggaetón","bachata","salsa"},       "OPEN FORMAT/LATIN REGGAETON"),
    ({"rock and roll","rock'n'roll","rocknroll","rockabilly"},  "OPEN FORMAT/ROCKNROLL"),
    ({"rock","classic rock","hard rock"},                       "OPEN FORMAT/ROCK CLASSICS"),
    ({"funk","soul","motown","boogie","northern soul"},         "OPEN FORMAT/FUNK SOUL"),
    ({"pop","dance","eurodance","edm","disco"},                 "OPEN FORMAT/PARTY DANCE"),
]


REMIX_TOKENS = {"remix","edit","extended","club","rework","vip","bootleg","refix","mix"}

def _norm(s: str) -> str:
    return (s or "").strip().lower()

def _has_any(text: str, tokens: set[str]) -> bool:
    t = _norm(text)
    return any(tok in t for tok in tokens)

def _clean_genre(g: str) -> str:
    s = _norm(g).replace("_"," ").replace("-"," ").replace("  "," ")
    # ujednolicenia podstawowe
    s = s.replace("drum and bass","dnb").replace("drum & bass","dnb").replace("drum n bass","dnb")
    s = s.replace("electro swing","electroswing")
    s = s.replace("afro-house","afro house")
    s = s.replace("tech-house","tech house")
    s = s.replace("melodic techno","melodic techno")
    return s

def _is_clubish_version(title: str, version_info: str) -> bool:
    return _has_any(title, REMIX_TOKENS) or _has_any(version_info, REMIX_TOKENS)

def _parse_bpm(bpm: str) -> Optional[float]:
    try:
        return float(str(bpm).replace(",","."))
    except Exception:
        return None

def decide_bucket(row: Dict[str,str]) -> Tuple[Optional[str], float, str]:
    """
    Zwraca: (target_subfolder | None, confidence(0..1), reason)
    """
    artist = row.get("artist_canonical") or row.get("artist") or ""
    title  = row.get("title_canonical")  or row.get("title")  or ""
    version= row.get("version_info","")
    genre  = _clean_genre(row.get("genre",""))
    era    = (row.get("era") or "").strip()
    bpmv   = _parse_bpm(row.get("bpm","")) or 0.0
    keyc   = (row.get("key_camelot") or "").strip().upper()

    # 1) CLUB: gatunek/wersja klubowa/BPM
    is_club_genre = any(g in genre for g in CLUB_GENRES)
    is_club_version = _is_clubish_version(title, version)
    if is_club_genre or is_club_version or (bpmv >= 122 and any(x in genre for x in {"house","tech","trance","dnb"})):
        # mapowanie do konkretnego kubła
        if "tech house" in genre:       return ("CLUB/TECH HOUSE", 0.95, "genre=tech house")
        if "melodic techno" in genre:   return ("CLUB/MELODIC TECHNO", 0.95, "genre=melodic techno")
        if "techno" in genre:           return ("CLUB/TECHNO", 0.9, "genre=techno")
        if "dnb" in genre:              return ("CLUB/DNB", 0.95, "genre=dnb")
        if "trance" in genre:           return ("CLUB/TRANCE", 0.9, "genre=trance")
        if "afro house" in genre:       return ("CLUB/AFRO HOUSE", 0.9, "genre=afro house")
        if "electroswing" in genre:     return ("CLUB/ELECTRO SWING", 0.9, "genre=electroswing")
        if "house" in genre or is_club_version or bpmv >= 122:
            return ("CLUB/HOUSE", 0.8, f"fallback clubish (bpm={bpmv:.0f}, remix={is_club_version})")

    # 2) OPEN FORMAT / dekada
    if era in {"70s","80s","90s","2000s","2010s"}:
        return (f"OPEN FORMAT/{era}", 0.9, f"era={era}")

    # 3) OPEN FORMAT / vibe
    for keys, bucket in VIBE_MAP:
        if any(k in genre for k in keys):
            return (bucket, 0.75, f"vibe via genre={genre or 'n/a'}")

    # default
    if genre:
        return ("OPEN FORMAT/PARTY DANCE", 0.6, f"default party (genre={genre})")
    return (None, 0.0, "undecided")
