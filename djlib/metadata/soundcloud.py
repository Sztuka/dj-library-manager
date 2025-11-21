from __future__ import annotations
from typing import Dict, List, Optional
import requests, re, time
from functools import lru_cache
from djlib.config import get_soundcloud_client_id

# Licznik prób zapytań do SoundCloud public search (użyteczne dla enrich_status.json)
_SC_REQUESTS = 0

API_SEARCH = "https://api-v2.soundcloud.com/search/tracks"
_DEF_TIMEOUT = 10

_REMIX_KEYWORDS = (
    "remix", "bootleg", "rework", "refix", "flip", "vip", "mashup", "re-edit", "re edit"
)
_GENERIC_VERSION_WORDS = {
    "extended", "radio", "original", "mix", "edit", "version", "club", "dub", "instrumental",
    "clean", "dirty", "album", "single", "main mix"
}

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("_", " ")
    s = re.sub(r"\s+", " ", s)
    return s

def _split_version_segments(text: str) -> List[str]:
    if not text:
        return []
    normalized = re.sub(r"[()\[\]{}]+", ",", text)
    parts = [seg.strip() for seg in re.split(r"[,/|]+", normalized) if seg.strip()]
    return parts


def _focus_version_tokens(title: str, version: str) -> List[str]:
    segments = []
    segments.extend(_split_version_segments(version))
    # also collect from parentheses in title if version missing
    if title:
        segments.extend(_split_version_segments(title))
    tokens: List[str] = []
    seen = set()
    for seg in segments:
        lower = seg.lower()
        if not any(kw in lower for kw in _REMIX_KEYWORDS):
            continue
        # drop if it's only a generic descriptor without remixer name
        if all(word in _GENERIC_VERSION_WORDS for word in lower.split()):
            continue
        if lower not in seen:
            tokens.append(seg)
            seen.add(lower)
    return tokens


def _candidate_queries(artist: str, title: str, version: str) -> List[str]:
    base = f"{artist} {title}".strip()
    if not base:
        return []
    queries: List[str] = []
    for tok in _focus_version_tokens(title, version):
        queries.append(f"{base} {tok}".strip())
    queries.append(base)
    if "remix" not in base.lower():
        queries.append(f"{base} remix")
    # de-dup preserve order
    seen = set()
    return [q for q in queries if q and not (q in seen or seen.add(q))]


@lru_cache(maxsize=1000)
def get_soundcloud_genres(artist: str, title: str, version: str = "") -> Optional[List[str]]:
    """Public SoundCloud search – multi-query strategy collecting genre + tag_list tokens.

    Queries (stop early if we already have strong tokens like 'afro house'):
      1) artist + title (+ version if given)
      2) artist + title + 'remix'
      3) artist + title + 'extended edit'

    For each query we take up to top 3 results, merge tokens and filter noise.
    Noise: generic buzz (new, trending, viral, remix(es) duplicates, year tags).
    Returns unique, normalized tokens sorted (for stable CSV diffs) or None.
    """
    cid = get_soundcloud_client_id()
    if not cid:
        return None
    queries = _candidate_queries(artist, title, version)
    if not queries:
        return None

    collected: List[str] = []
    global _SC_REQUESTS

    # Build stopword set from artist/title to drop self-referential tokens
    at_words = set(_norm((artist or "") + " " + (title or "")).split())
    # Common non-genre words to ignore
    common_noise = {"edit", "extended", "original", "mix", "remix", "vip", "club", "radio", "version"}

    # Acceptable single-word genre-like tokens (others are dropped if single words)
    allow_single = {
        "house", "techno", "trance", "electronic", "edm", "garage", "dubstep", "amapiano",
        "breaks", "breakbeat", "disco", "funk", "soul", "hiphop", "hip-hop", "hip",
        "drill", "afro", "dancehall", "reggaeton", "dnb", "drumstep", "jungle",
    }

    def _keep_token(t: str) -> bool:
        if not t:
            return False
        if t in at_words:
            return False
        if t in common_noise:
            return False
        # remove plain years
        if re.fullmatch(r"20[0-3][0-9]", t):
            return False
        # keep multi-word phrases (e.g., 'afro house', 'tech house')
        if " " in t:
            return True
        # keep only certain singletons
        if t in allow_single:
            return True
        # drop very short or person-name-like singles
        if len(t) <= 4:
            return False
        return False

    def _extract_from_item(item: Dict[str, str]) -> List[str]:
        toks: List[str] = []
        genre = item.get("genre") or ""
        if genre:
            normg = _norm(genre)
            if _keep_token(normg):
                toks.append(normg)
        tag_list = item.get("tag_list") or ""
        if tag_list:
            quoted = re.findall(r'"([^\"]+)"', tag_list)
            for qv in quoted:
                nv = _norm(qv)
                if _keep_token(nv):
                    toks.append(nv)
            remainder = re.sub(r'"[^\"]+"', "", tag_list)
            for part in remainder.split():
                pn = _norm(part)
                if _keep_token(pn):
                    toks.append(pn)
        # Basic item-level filtering of noise
        noise = {"new", "trending", "viral", "remixes", "remix", "extended", "mix", "summer", "new music"}
        out = []
        for t in toks:
            if t.isdigit():
                continue
            # remove explicit year tags
            if re.match(r"20[0-3][0-9]", t):
                continue
            if any(word in t for word in noise):
                # keep composite genres like 'afro house' despite containing filtered words
                if t not in noise and not t.endswith(" mix"):
                    out.append(t)
                continue
            out.append(t)
        return out

    try:
        for q in queries:
            time.sleep(0.4)
            _SC_REQUESTS += 1
            r = requests.get(API_SEARCH, params={"q": q, "client_id": cid, "limit": 5}, timeout=_DEF_TIMEOUT)
            if r.status_code != 200:
                continue
            data = r.json() or {}
            coll = (data.get("collection") or [])[:3]
            for item in coll:
                collected.extend(_extract_from_item(item))
            # Early exit if we already captured strong afro/house tokens
            if any(t in collected for t in ["afro house", "afro tech", "tech house", "house"]):
                break
    # de-dup preserve order
        seen = set()
        uniq = [t for t in collected if not (t in seen or seen.add(t))]
        return sorted(uniq) if uniq else None
    except Exception:
        return None

def track_tags(artist: str, title: str, version: str = "") -> Dict[str, List[str]]:
    """Wrapper used by genre_resolver.
    Accepts optional version/remix tokens to improve search precision."""
    genres = get_soundcloud_genres(artist, title, version) or []
    if not genres:
        return {}
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
