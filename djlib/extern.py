from __future__ import annotations
from pathlib import Path
from typing import Dict, Tuple, List
import time
import json
import base64
import os

from djlib.config import LOGS_DIR, get_lastfm_api_key, get_spotify_credentials

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

# --- Spotify ---

# Spotify Web API terms limit how long you may cache metadata.
# Use a conservative 24h TTL to stay within typical guidance.
SPOTIFY_TTL = 24 * 3600
TOKEN_CACHE = CACHE_DIR / "spotify_token.json"


def _spotify_token() -> str | None:
    now = time.time()
    if TOKEN_CACHE.exists():
        try:
            data = json.loads(TOKEN_CACHE.read_text())
            if data.get("expires_at", 0) > now + 30:
                return data.get("access_token")
        except Exception:
            pass
    cid, sec = get_spotify_credentials()
    if not cid or not sec:
        return None
    import requests
    try:
        _ext_throttle()
        resp = requests.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            headers={
                "Authorization": "Basic " + base64.b64encode(f"{cid}:{sec}".encode()).decode(),
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        tok = data.get("access_token")
        expires_in = int(data.get("expires_in", 3600))
        TOKEN_CACHE.write_text(json.dumps({"access_token": tok, "expires_at": now + expires_in}))
        return tok
    except Exception:
        return None


def spotify_artist_genres(artist: str, title: str) -> List[str]:
    """Use track search to find artist, then return artist genres. Requires client credentials.
    Returns a list of lowercased genres. Cached.
    """
    tok = _spotify_token()
    if not tok:
        return []
    import requests
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist and not title:
        return []
    key = f"spotify_genres:{artist}|{title}"
    cached = cache_get(key, SPOTIFY_TTL)
    if cached is not None:
        return cached.get("genres", []) if isinstance(cached, dict) else []
    try:
        q = " ".join([artist, title]).strip()
        _ext_throttle()
        resp = requests.get(
            "https://api.spotify.com/v1/search",
            params={"q": q, "type": "track", "limit": 1},
            headers={"Authorization": f"Bearer {tok}"},
            timeout=15,
        )
        if resp.status_code != 200:
            cache_set(key, {"genres": []})
            return []
        items = ((resp.json().get("tracks") or {}).get("items") or [])
        if not items:
            cache_set(key, {"genres": []})
            return []
        track = items[0]
        artists = track.get("artists") or []
        genres: List[str] = []
        for a in artists[:2]:  # first 1-2 artists
            aid = a.get("id")
            if not aid:
                continue
            _ext_throttle()
            r2 = requests.get(
                f"https://api.spotify.com/v1/artists/{aid}",
                headers={"Authorization": f"Bearer {tok}"},
                timeout=15,
            )
            if r2.status_code == 200:
                genres.extend([g.lower() for g in r2.json().get("genres", []) or []])
        # de-dup
        seen = set()
        genres = [g for g in genres if not (g in seen or seen.add(g))]
        cache_set(key, {"genres": genres})
        return genres
    except Exception:
        cache_set(key, {"genres": []})
        return []

# --- Discogs removed ---

