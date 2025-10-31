from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Tuple

# Ensure requests-cache side effects
import djlib.metadata  # noqa: F401

from . import mb_client
from . import lastfm
from ..extern import spotify_artist_genres


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


@dataclass
class GenreResolution:
    main: str
    subs: List[str]
    confidence: float
    breakdown: List[Tuple[str, float, Dict[str, float]]]


def resolve(artist: str, title: str, *, duration_s: int | None = None) -> GenreResolution | None:
    """Resolve genres using MB -> Last.fm -> Spotify with scoring.

    Weights: MB=3, LFM=2, SP=1. Returns main + up to 2 subs.
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
            scores[c] = scores.get(c, 0.0) + mb_w
            local[c] = local.get(c, 0.0) + mb_w
        if local:
            parts.append(("musicbrainz", mb_w, local))

    # Last.fm
    lfm_w = 2.0
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
            scores[c] = scores.get(c, 0.0) + sp_w
            local[c] = local.get(c, 0.0) + sp_w
        if local:
            parts.append(("spotify", sp_w, local))

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
