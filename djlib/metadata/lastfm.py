from __future__ import annotations
from typing import List, Tuple
import urllib.parse
import requests

# Ensure requests-cache is installed globally
import djlib.metadata  # noqa: F401  # ensure requests-cache is installed
from djlib.config import get_lastfm_api_key


API_ROOT = "https://ws.audioscrobbler.com/2.0/"


def _normalize_tag(tag: str) -> str:
    t = (tag or "").strip().lower()
    # common cleanups
    t = t.replace("_", " ").replace("-", " ")
    t = " ".join(t.split())
    return t


def _call(method: str, params: dict) -> dict:
    key = get_lastfm_api_key()
    if not key:
        return {}
    base = {"method": method, "api_key": key, "format": "json"}
    base.update(params)
    resp = requests.get(API_ROOT, params=base, timeout=15)
    if resp.status_code != 200:
        return {}
    return resp.json() or {}


def top_tags(artist: str, title: str, *, min_count: int = 10, max_tags: int = 20) -> List[Tuple[str, int]]:
    """Return list of (tag, count) sorted by count desc. Track first, then artist fallback."""
    artist = (artist or "").strip()
    title = (title or "").strip()
    out: List[Tuple[str, int]] = []
    if not artist and not title:
        return out

    # Try track.getTopTags
    if artist and title:
        data = _call("track.getTopTags", {"artist": artist, "track": title})
        tags = ((data.get("toptags") or {}).get("tag") or [])
        for t in tags:
            name = _normalize_tag(t.get("name", ""))
            try:
                cnt = int(t.get("count", 0))
            except Exception:
                cnt = 0
            if name and cnt >= min_count:
                out.append((name, cnt))

    # Fallback to artist.getTopTags if empty
    if not out and artist:
        data = _call("artist.getTopTags", {"artist": artist})
        tags = ((data.get("toptags") or {}).get("tag") or [])
        for t in tags:
            name = _normalize_tag(t.get("name", ""))
            try:
                cnt = int(t.get("count", 0))
            except Exception:
                cnt = 0
            if name and cnt >= min_count:
                out.append((name, cnt))

    # sort and trim
    out.sort(key=lambda x: x[1], reverse=True)
    return out[:max_tags]


def track_info(artist: str, title: str) -> dict:
    """Return basic track info from Last.fm: playcount, listeners, duration.

    Returns empty dict if API key missing or not found.
    """
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist or not title:
        return {}

    data = _call("track.getInfo", {"artist": artist, "track": title})
    if not data:
        return {}
    tr = data.get("track") or {}
    out: dict = {}
    try:
        out["playcount"] = int(tr.get("playcount", 0))
    except Exception:
        pass
    try:
        out["listeners"] = int(tr.get("listeners", 0))
    except Exception:
        pass
    try:
        # duration in ms
        dur_ms = int(tr.get("duration", 0))
        out["duration_ms"] = dur_ms
    except Exception:
        pass
    return out
