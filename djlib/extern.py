from __future__ import annotations
from pathlib import Path
from typing import Dict
import time
import json

from djlib.config import LOGS_DIR, get_lastfm_api_key

CACHE_DIR = LOGS_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# --- Simple file cache with TTL ---

def _cache_path(name: str) -> Path:
    safe = name.replace("/", "_")
    return CACHE_DIR / f"{safe}.json"


def cache_get(name: str, ttl_seconds: int) -> dict | None:
    p = _cache_path(name)
    if not p.exists():
        return None
    try:
        st = p.stat()
        if (time.time() - st.st_mtime) > ttl_seconds:
            return None
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def cache_set(name: str, data: dict) -> None:
    p = _cache_path(name)
    try:
        with p.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass

# --- Global external HTTP throttle (conservative: 1 request/sec) ---
_EXT_LAST_TS: float = 0.0

def _ext_throttle(min_interval: float = 1.05) -> None:
    global _EXT_LAST_TS
    now = time.time()
    wait = _EXT_LAST_TS + min_interval - now
    if wait > 0:
        time.sleep(wait)
    _EXT_LAST_TS = time.time()

# --- Last.fm ---

LASTFM_TTL = 7 * 24 * 3600  # 7 days


def lastfm_toptags(artist: str, title: str) -> Dict[str, int]:
    """Fetch Last.fm top tags for track (fallback: artist). Returns tag->count.
    Requires LASTFM API key when available; if missing, returns empty.
    """
    api_key = get_lastfm_api_key()
    if not api_key:
        return {}
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist and not title:
        return {}

    # Try track.getTopTags first
    import requests
    key = f"lastfm_tracktags:{artist}|{title}"
    cached = cache_get(key, LASTFM_TTL)
    if cached is not None:
        return cached or {}
    try:
        params = {
            "method": "track.gettoptags",
            "artist": artist,
            "track": title,
            "api_key": api_key,
            "format": "json",
        }
        _ext_throttle()
        r = requests.get("https://ws.audioscrobbler.com/2.0/", params=params, timeout=15, allow_redirects=True)
        tags: Dict[str, int] = {}
        if r.status_code == 200:
            data = r.json()
            for it in (data.get("toptags") or {}).get("tag", []) or []:
                name = (it.get("name") or "").strip().lower()
                try:
                    count = int(it.get("count", 0))
                except Exception:
                    count = 0
                if name:
                    tags[name] = tags.get(name, 0) + count
        # Fallback: artist.getTopTags
        if not tags and artist:
            params = {
                "method": "artist.gettoptags",
                "artist": artist,
                "api_key": api_key,
                "format": "json",
            }
            _ext_throttle()
            r = requests.get("https://ws.audioscrobbler.com/2.0/", params=params, timeout=15, allow_redirects=True)
            if r.status_code == 200:
                data = r.json()
                for it in (data.get("toptags") or {}).get("tag", []) or []:
                    name = (it.get("name") or "").strip().lower()
                    try:
                        count = int(it.get("count", 0))
                    except Exception:
                        count = 0
                    if name:
                        tags[name] = tags.get(name, 0) + count
        cache_set(key, tags)
        return tags
    except Exception:
        cache_set(key, {})
        return {}

# --- Discogs removed ---

