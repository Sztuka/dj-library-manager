from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Tuple

# Ensure requests-cache side effects
import djlib.metadata  # noqa: F401

from . import mb_client
from . import lastfm
from ..extern import spotify_artist_genres
from .soundcloud import track_tags as sc_track_tags


def _norm(tag: str) -> str:
    t = (tag or "").strip().lower()
    t = t.replace("_", " ").replace("-", " ")
    t = " ".join(t.split())
    return t


ALIASES = {
    "edm": "electronic",
    "tech-house": "tech house",
    "techno house": "tech house",
    "d n b": "drum and bass",
    "d&b": "drum and bass",
}


def canonical(tag: str) -> str:
    t = _norm(tag)
    return ALIASES.get(t, t)


# Non-genre noise patterns occasionally present in MB/LFM tags
_NOISE_TERMS = {
    "offizielle charts",  # german charts label
    "offizielle",
    "charts",
    "chart",
    "ph temp checken",
    "favourite", "favorite", "favorites",
    "seen live",
    "plattentests.de",
    "germany", "deutschland",
    # newly filtered buzz / generic popularity tokens and year/season fluff
    "viral", "trending", "new", "new music", "summer mix", "summer", "remixes", "mix",
}

import re as _re

def _is_noise(tag: str) -> bool:
    t = _norm(tag)
    if not t:
        return True
    if t in _NOISE_TERMS:
        return True
    # domain-like tokens
    if "." in t and not t.replace(".", "").isalpha():
        return True
    # '1–4 wochen' / '1-4 wochen' etc.
    if _re.search(r"\b\d+\s*[–-]?\s*\d*\s*wochen\b", t):
        return True
    # very short or purely numeric
    if len(t) <= 2 or t.isdigit():
        return True
    # Year-only tags (2023, 2024, etc.) or tokens ending with year markers
    if _re.fullmatch(r"20[0-3][0-9]", t):
        return True
    if _re.search(r"20[0-3][0-9]", t) and len(t.split()) == 1:
        return True
    return False


@dataclass
class GenreResolution:
    main: str
    subs: List[str]
    confidence: float
    breakdown: List[Tuple[str, float, Dict[str, float]]]


def resolve(artist: str, title: str, *, duration_s: int | None = None, disable_soundcloud: bool = False) -> GenreResolution | None:
    """Resolve genres using MB -> Last.fm -> Spotify with scoring.

    Weights (relative): MB=3, LFM=6, SP=1. Returns main + up to 2 subs.
    """
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist and not title:
        return None

    scores: Dict[str, float] = {}
    parts: List[Tuple[str, float, Dict[str, float]]] = []

    # MusicBrainz
    mb_w = 3.0
    rec = mb_client.search_recording(artist, title, duration=duration_s)
    if rec:
        tags = mb_client.get_recording_genres(rec.recording_id, release_group_id=rec.release_group_id, artist_id=rec.artist_id)
        local: Dict[str, float] = {}
        for t in tags:
            c = canonical(t)
            if _is_noise(c):
                continue
            scores[c] = scores.get(c, 0.0) + mb_w
            local[c] = local.get(c, 0.0) + mb_w
        if local:
            parts.append(("musicbrainz", mb_w, local))

    # Last.fm (stronger influence to reflect community tags importance)
    # Zwiększona waga (podniesiona z 4.0 → 6.0) aby Last.fm częściej dominowało w wynikach przy szerokim zestawie tagów.
    lfm_w = 6.0
    tags_lfm = lastfm.top_tags(artist, title)
    if tags_lfm:
        local: Dict[str, float] = {}
        # weight by log(count), scale with lfm_w
        import math
        for name, cnt in tags_lfm:
            w = (math.log(max(cnt, 1)) if cnt > 0 else 0.0) * lfm_w
            if w <= 0:
                continue
            c = canonical(name)
            if _is_noise(c):
                continue
            scores[c] = scores.get(c, 0.0) + w
            local[c] = local.get(c, 0.0) + w
        if local:
            parts.append(("lastfm", lfm_w, local))

    # Spotify
    sp_w = 1.0
    tags_sp = spotify_artist_genres(artist, title)
    if tags_sp:
        local: Dict[str, float] = {}
        for name in tags_sp:
            c = canonical(name)
            if _is_noise(c):
                continue
            scores[c] = scores.get(c, 0.0) + sp_w
            local[c] = local.get(c, 0.0) + sp_w
        if local:
            parts.append(("spotify", sp_w, local))

    # SoundCloud (light weight)
    if not disable_soundcloud:
        sc_w = 2.0  # moderate weight: between MB and Last.fm, above Spotify
        sc = sc_track_tags(artist, title)
        if sc.get("tags"):
            local: Dict[str, float] = {}
            for name in sc["tags"]:
                c = canonical(name)
                if _is_noise(c):
                    continue
                scores[c] = scores.get(c, 0.0) + sc_w
                local[c] = local.get(c, 0.0) + sc_w
            if local:
                parts.append(("soundcloud", sc_w, local))

    if not scores:
        return None

    # rank and choose main + up to 2 subs
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    main = ranked[0][0]
    subs = [k for k, _ in ranked[1:3]]

    # crude confidence: main share of total weight (0..1)
    total_w = sum(scores.values()) or 1.0
    conf = ranked[0][1] / total_w
    return GenreResolution(main=main, subs=subs, confidence=conf, breakdown=parts)
