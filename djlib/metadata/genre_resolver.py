from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Tuple

# Ensure requests-cache side effects
import djlib.metadata  # noqa: F401

from . import mb_client
from . import lastfm
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
    # project-specific: do not use 'folk indie' at all
    "folk indie",
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


def _downweight_factor(tag: str) -> float:
    """Return a multiplicative factor (0..1] to reduce influence of broad tags.

    Goal: limit over-dominance of generic tags like folk/indie/alternative.
    Keep them present if truly dominant, but with smaller weight.
    """
    t = _norm(tag)
    if not t:
        return 1.0
    if t == "folk":
        return 0.30
    # Indie is OK; only penalize the folk+indie combo (any order)
    if t in {"indie folk"}:  # 'folk indie' is fully ignored by _NOISE_TERMS
        return 0.40
    if t in {"alternative", "alternative rock"}:
        return 0.60
    return 1.0


@dataclass
class GenreResolution:
    main: str
    subs: List[str]
    confidence: float
    breakdown: List[Tuple[str, float, Dict[str, float]]]


def resolve(artist: str, title: str, version: str = "", *, duration_s: int | None = None, disable_soundcloud: bool = False) -> GenreResolution | None:
    """Resolve genres using MB -> Last.fm (+ optional SoundCloud) with scoring.

    Version info (remix names) helps SoundCloud queries disambiguate edits.
    Weights (relative): MB=3, LFM=6, SC=2. Returns main + up to 2 subs.
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
            f = _downweight_factor(c)
            w = mb_w * f
            if w <= 0:
                continue
            scores[c] = scores.get(c, 0.0) + w
            local[c] = local.get(c, 0.0) + w
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
            base = (math.log(max(cnt, 1)) if cnt > 0 else 0.0) * lfm_w
            c = canonical(name)
            if _is_noise(c):
                continue
            f = _downweight_factor(c)
            w = base * f
            if w <= 0:
                continue
            scores[c] = scores.get(c, 0.0) + w
            local[c] = local.get(c, 0.0) + w
        if local:
            parts.append(("lastfm", lfm_w, local))

    # SoundCloud (light weight)
    if not disable_soundcloud:
        sc_w = 2.0  # moderate weight: between MB and Last.fm
        sc = sc_track_tags(artist, title, version)
        if sc.get("tags"):
            local: Dict[str, float] = {}
            for name in sc["tags"]:
                c = canonical(name)
                if _is_noise(c):
                    continue
                f = _downweight_factor(c)
                w = sc_w * f
                if w <= 0:
                    continue
                scores[c] = scores.get(c, 0.0) + w
                local[c] = local.get(c, 0.0) + w
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
