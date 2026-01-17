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
    """Return a multiplicative factor (0..1] to reduce influence of overly broad tags.

    Only penalize truly problematic broad tags like folk/indie combos.
    Generic parent genres (rock, pop, electronic) keep weight 1.0 - 
    they can still win over false positives, but lose to specific subgenres via boost.
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
    # NOTE: Generic parent genres (rock, pop, electronic, dance) 
    # are NOT downweighted - they keep 1.0 to beat false positives.
    # Specific subgenres win via _specificity_boost() instead.
    return 1.0


# Specific subgenres that should be boosted over their parent genres
_SPECIFIC_GENRE_BOOST = {
    # Rock subgenres
    "rock and roll": 2.0,
    "rockabilly": 2.0,
    "punk rock": 1.8,
    "hard rock": 1.8,
    "classic rock": 1.8,
    "progressive rock": 1.8,
    "glam rock": 1.8,
    # Electronic subgenres
    "tech house": 1.5,
    "deep house": 1.5,
    "progressive house": 1.5,
    "minimal techno": 1.5,
    "acid house": 1.5,
    "electro house": 1.5,
    "melodic techno": 1.5,
    "afro house": 1.5,
    # Pop subgenres
    "synth pop": 1.8,
    "electropop": 1.8,
    "dance pop": 1.5,
    "disco": 1.8,
    "italo disco": 2.0,
    "euro disco": 2.0,
    "nu disco": 1.8,
    # Other specific genres
    "funk": 1.5,
    "soul": 1.5,
    "r&b": 1.5,
    "hip hop": 1.5,
    "reggae": 1.5,
    "ska": 1.8,
    "dub": 1.5,
}


def _specificity_boost(tag: str) -> float:
    """Return boost factor for specific subgenres over generic parents."""
    t = _norm(tag)
    return _SPECIFIC_GENRE_BOOST.get(t, 1.0)


@dataclass
class GenreResolution:
    main: str
    subs: List[str]
    confidence: float
    breakdown: List[Tuple[str, float, Dict[str, float]]]


def resolve(artist: str, title: str, version: str = "", *, duration_s: int | None = None, disable_soundcloud: bool = False, disable_beatport: bool = False) -> GenreResolution | None:
    """Resolve genres using Beatport -> Last.fm -> MB (+ optional SoundCloud) with scoring.

    Version info (remix names) helps SoundCloud queries disambiguate edits.
    Weights (relative): Beatport=10, LFM=6, MB=3, SC=2 (base).
    For remixes (version provided): SC and BP weights increased, LFM/MB decreased.
    Returns main + up to 2 subs.
    """
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist and not title:
        return None

    # Detect if this is a TRUE remix (another artist/producer reworked it)
    # INVERTED LOGIC: Default is NOT remix. Only explicit remix keywords make it a remix.
    # This prevents "(Hear Me Tonight)", "(Remastered)", "(Version 1)" from being treated as remixes.
    is_remix = False
    version_lower = (version or "").strip().lower()
    if version_lower:
        # Explicit remix indicators - ONLY these make it a remix
        remix_keywords = ["remix", "rework", "bootleg", "dub mix", "vip mix", "vip edit"]
        is_remix = any(kw in version_lower for kw in remix_keywords)
        # Edge case: "radio edit" contains "edit" but is NOT a remix
        # Already handled: "edit" alone is not in remix_keywords, only "vip edit"
    
    scores: Dict[str, float] = {}
    parts: List[Tuple[str, float, Dict[str, float]]] = []

    # Beatport (gold standard for EDM - highest weight)
    # Reduced weight for remixes (Beatport often returns original's genre, not remix-specific)
    if not disable_beatport:
        bp_w = 8.0 if is_remix else 10.0
        try:
            from djlib.metadata.beatport import search_track as bp_search
            bp_result = bp_search(artist, title, duration_s)
            if bp_result and bp_result.get("genre"):
                local: Dict[str, float] = {}
                # Beatport returns precise genres like "Techno (Peak Time / Driving)"
                bp_genres = [g.strip() for g in bp_result["genre"].split(",")]
                for t in bp_genres:
                    c = canonical(t)
                    if _is_noise(c):
                        continue
                    f = _downweight_factor(c) * _specificity_boost(c)
                    w = bp_w * f
                    if w <= 0:
                        continue
                    scores[c] = scores.get(c, 0.0) + w
                    local[c] = local.get(c, 0.0) + w
                if local:
                    parts.append(("beatport", bp_w, local))
        except Exception:
            pass  # Beatport unavailable - continue with other sources

    # MusicBrainz
    # Reduced weight for remixes (MB returns data for original track, not remix)
    mb_w = 1.5 if is_remix else 3.0
    rec = mb_client.search_recording(artist, title, duration=duration_s)
    if rec:
        tags = mb_client.get_recording_genres(rec.recording_id, release_group_id=rec.release_group_id, artist_id=rec.artist_id)
        local: Dict[str, float] = {}
        for t in tags:
            c = canonical(t)
            if _is_noise(c):
                continue
            f = _downweight_factor(c) * _specificity_boost(c)
            w = mb_w * f
            if w <= 0:
                continue
            scores[c] = scores.get(c, 0.0) + w
            local[c] = local.get(c, 0.0) + w
        if local:
            parts.append(("musicbrainz", mb_w, local))

    # Last.fm (stronger influence to reflect community tags importance)
    # Zwiększona waga (podniesiona z 4.0 → 6.0) aby Last.fm częściej dominowało w wynikach przy szerokim zestawie tagów.
    # Reduced weight for remixes (LFM returns data for original track, not remix)
    # Further reduced 3.0 → 0.5 to prevent high-playcount indie/pop tags from dominating remix-specific genres
    lfm_w = 0.5 if is_remix else 6.0
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
            f = _downweight_factor(c) * _specificity_boost(c)
            w = base * f
            if w <= 0:
                continue
            scores[c] = scores.get(c, 0.0) + w
            local[c] = local.get(c, 0.0) + w
        if local:
            parts.append(("lastfm", lfm_w, local))

    # SoundCloud (light weight for originals, higher for remixes)
    # SoundCloud is most reliable for remix-specific genre tagging
    if not disable_soundcloud:
        sc_w = 20.0 if is_remix else 2.0  # significantly increased for remixes to override Last.fm and Beatport
        sc = sc_track_tags(artist, title, version)
        if sc.get("tags"):
            local: Dict[str, float] = {}
            for name in sc["tags"]:
                c = canonical(name)
                if _is_noise(c):
                    continue
                f = _downweight_factor(c) * _specificity_boost(c)
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
