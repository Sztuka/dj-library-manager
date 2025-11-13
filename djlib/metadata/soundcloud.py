from __future__ import annotations
from typing import Dict, List, Optional
import requests, re, time
from functools import lru_cache
from djlib.config import get_soundcloud_client_id

# Licznik prób zapytań do SoundCloud public search (użyteczne dla enrich_status.json)
_SC_REQUESTS = 0

API_SEARCH = "https://api-v2.soundcloud.com/search/tracks"
_DEF_TIMEOUT = 10

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("_", " ")
    s = re.sub(r"\s+", " ", s)
    return s

@lru_cache(maxsize=1000)
def get_soundcloud_genres(artist: str, title: str, version: str = "") -> Optional[List[str]]:
    """Public SoundCloud track search → collect genre + tag_list tokens.

    Returns a sorted list of unique, normalized tokens (lowercase) or None if nothing found.
    Uses only the public /search/tracks endpoint with the provided client_id.
    Rate-limit friendly: small sleep per call; results cached (LRU).
    """
    cid = get_soundcloud_client_id()
    if not cid:
        return None
    query = f"{artist} {title} {version}".strip()
    if not query:
        return None
    global _SC_REQUESTS
    try:
        time.sleep(0.5)  # basic pacing to avoid burst hitting daily quota
        _SC_REQUESTS += 1
        r = requests.get(
            API_SEARCH,
            params={"q": query, "client_id": cid, "limit": 3},
            timeout=_DEF_TIMEOUT,
        )
        if r.status_code != 200:
            return None
        data = r.json() or {}
        coll = (data.get("collection") or [])
        if not coll:
            return None
        # Take best match (first); could improve ranking by duration/title similarity later
        item = coll[0]
        tokens: List[str] = []
        genre = item.get("genre") or ""
        if genre:
            tokens.append(_norm(genre))
        tag_list = item.get("tag_list") or ""
        if tag_list:
            # quoted phrases and standalone tokens
            quoted = re.findall(r'"([^"]+)"', tag_list)
            for qv in quoted:
                tokens.append(_norm(qv))
            remainder = re.sub(r'"[^\"]+"', "", tag_list)
            for part in remainder.split():
                part_n = _norm(part)
                if part_n and len(part_n) > 2:
                    tokens.append(part_n)
        # de-dup preserving order
        seen = set()
        out = [t for t in tokens if not (t in seen or seen.add(t))]
        return sorted(out) if out else None
    except Exception:
        return None

def track_tags(artist: str, title: str) -> Dict[str, List[str]]:
    """Backward-compatible wrapper used by genre_resolver (no version passing yet)."""
    genres = get_soundcloud_genres(artist, title, "") or []
    if not genres:
        return {}
    # first token as primary genre candidate; all tokens as tags
    return {"genre": genres[:1], "tags": genres}

def client_id_health() -> Dict[str, str]:
    """Validate client_id by performing a lightweight public search request.
    Returns dict with status: ok|invalid|missing|error|rate-limit and message.
    """
    cid = get_soundcloud_client_id()
    if not cid:
        return {"status": "missing", "message": "Brak client_id (SOUNDCLOUD_CLIENT_ID)."}
    try:
        r = requests.get(
            API_SEARCH,
            params={"q": "test", "client_id": cid, "limit": 1},
            timeout=5,
        )
        if r.status_code == 200:
            return {"status": "ok", "message": "Client ID działa dla public search."}
        if r.status_code in {401, 403}:
            return {"status": "invalid", "message": f"Status {r.status_code} – ID nieakceptowany w public search."}
        if r.status_code == 429:
            return {"status": "rate-limit", "message": "Osiągnięto limit (429) – spróbuj później."}
        return {"status": "error", "message": f"Nieoczekiwany status {r.status_code}."}
    except Exception as e:
        return {"status": "error", "message": f"Wyjątek: {e}"}

def soundcloud_request_count() -> int:
    """Zwraca liczbę prób zapytań wykonanych do public search w tym przebiegu procesu.
    Używane do logowania w enrich_status.json (attempted_requests)."""
    return _SC_REQUESTS
