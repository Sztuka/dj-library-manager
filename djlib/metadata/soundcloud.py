from __future__ import annotations
from typing import Dict, List
import requests, re
from djlib.config import get_soundcloud_client_id

API_SEARCH = "https://api-v2.soundcloud.com/search/tracks"
API_TRACK   = "https://api-v2.soundcloud.com/tracks/{id}"
API_RESOLVE = "https://api.soundcloud.com/resolve"

_DEF_TIMEOUT = 15

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("_", " ")
    s = re.sub(r"\s+", " ", s)
    return s

def track_tags(artist: str, title: str) -> Dict[str, List[str]]:
    """Fetch SoundCloud genre + tag_list for approximate artist+title.
    Returns {"genre": [...], "tags": [...]} lists (lowercased).
    Empty dict if client id missing or nothing found.
    """
    cid = get_soundcloud_client_id()
    if not cid:
        return {}
    artist = (artist or "").strip()
    title = (title or "").strip()
    q = " ".join([artist, title]).strip()
    if not q:
        return {}
    try:
        r = requests.get(
            API_SEARCH,
            params={"q": q, "client_id": cid, "limit": 3},
            timeout=_DEF_TIMEOUT,
        )
        if r.status_code != 200:
            return {}
        data = r.json() or {}
        coll = (data.get("collection") or [])
        if not coll:
            return {}
        # naive: first item
        item = coll[0]
        tid = item.get("id")
        genre = item.get("genre") or ""
        tag_list = item.get("tag_list") or ""
        tags = []
        if genre:
            tags.append(_norm(genre))
        # tag_list may contain quoted tags and plain words; split respecting quotes
        raw = tag_list.strip()
        if raw:
            # extract quoted tokens first
            quoted = re.findall(r'"([^"]+)"', raw)
            for qv in quoted:
                tags.append(_norm(qv))
            # remove quoted parts, split remainder
            remainder = re.sub(r'"[^"]+"', "", raw)
            for part in remainder.split():
                part = _norm(part)
                if part:
                    tags.append(part)
        # de-dup preserve order
        seen = set()
        tags = [t for t in tags if not (t in seen or seen.add(t))]
        return {"genre": tags[:1], "tags": tags}
    except Exception:
        return {}

def client_id_health() -> Dict[str, str]:
    """Lightweight validation of SoundCloud client_id usefulness.
    Tries a trivial public resolution to see if rate limits / invalid id.
    Returns dict with keys: status: ok|invalid|error and message.
    """
    cid = get_soundcloud_client_id()
    if not cid:
        return {"status": "missing", "message": "Brak client_id (DJLIB_SOUNDCLOUD_CLIENT_ID)."}
    try:
        # use resolve endpoint with a known public profile or track (generic)
        test_url = "https://soundcloud.com/soundcloud"  # stable profile
        r = requests.get(
            API_RESOLVE,
            params={"url": test_url, "client_id": cid},
            timeout=10,
        )
        if r.status_code == 200:
            return {"status": "ok", "message": "Client ID wygląda na działający."}
        if r.status_code in {401, 403}:
            return {"status": "invalid", "message": f"Odrzucono (status {r.status_code}) – ID nieakceptowany lub wygasły."}
        if r.status_code == 429:
            return {"status": "invalid", "message": "Limit zapytań (429) – spróbuj później lub użyj innego client_id."}
        return {"status": "error", "message": f"Nieoczekiwany status {r.status_code}."}
    except Exception as e:
        return {"status": "error", "message": f"Wyjątek: {e}"}
