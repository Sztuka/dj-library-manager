from __future__ import annotations
from typing import Dict, List, Optional
import requests, re, time, json
from functools import lru_cache
from pathlib import Path
from datetime import datetime, timedelta
from djlib.config import get_soundcloud_client_id

# Licznik prób zapytań do SoundCloud public search (użyteczne dla enrich_status.json)
_SC_REQUESTS = 0

API_SEARCH = "https://api-v2.soundcloud.com/search/tracks"
_DEF_TIMEOUT = 10

# Client ID cache configuration
_CLIENT_ID_CACHE_PATH = Path.home() / ".djlib" / "soundcloud_client_id.json"
_CLIENT_ID_CACHE_DAYS = 30  # SoundCloud client_id typically valid for ~30 days

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("_", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def _extract_primary_remixer(version: str) -> str:
    """Extract main remixer name from version string.
    'Merchant vs Vidojean & Oliver Loenn City Boys Edit' -> 'Merchant'
    'Audien Remix' -> 'Audien'
    """
    if not version:
        return ""
    # Split by common multi-artist separators and take first part
    for sep in [" vs ", " vs. ", " x ", " X ", " & ", " and ", " feat. ", " feat ", " ft. ", " ft "]:
        if sep in version:
            version = version.split(sep)[0].strip()
            break
    # Remove common version keywords to get just the name
    for kw in ["remix", "edit", "mix", "version", "bootleg", "rework", "refix"]:
        version = re.sub(rf"\b{kw}\b", "", version, flags=re.IGNORECASE)
    return version.strip()


def _candidate_queries(artist: str, title: str, version: str, max_queries: int = 2) -> List[str]:
    """Generate optimized query list for SoundCloud search.
    
    Args:
        max_queries: Maximum number of queries to return (default: 2 for performance)
    
    Returns up to max_queries most effective queries:
    1. artist + primary_remixer (best for edits/remixes)
    2. base query (artist + title) as fallback
    """
    base = f"{artist} {title}".strip()
    if not base:
        return []
    queries: List[str] = []
    
    # Extract primary remixer for shorter queries (avoid 403 on long queries)
    primary_remixer = _extract_primary_remixer(version)
    
    # Strategy 1: artist + primary_remixer (short, most effective for remixes)
    # e.g., "Bastille Merchant" finds "bastille - pompeii (merchant edit)"
    if primary_remixer and artist:
        queries.append(f"{artist} {primary_remixer}".strip())
    
    # Strategy 2: base query (finds original or matches title)
    queries.append(base)
    
    # Strategy 3: artist + title + primary_remixer (more specific, if space)
    if primary_remixer and len(queries) < max_queries:
        full_remix = f"{base} {primary_remixer}".strip()
        if full_remix not in queries:
            queries.append(full_remix)
    
    # de-dup preserve order, limit to max_queries
    seen = set()
    return [q for q in queries if q and not (q in seen or seen.add(q))][:max_queries]


@lru_cache(maxsize=1000)
def get_soundcloud_genres(artist: str, title: str, version: str = "") -> Optional[List[str]]:
    """Public SoundCloud search – optimized 2-query strategy for genre/tag extraction.

    Queries (limited to 2 for performance):
      1) artist + primary_remixer (best for finding remixes)
      2) artist + title (fallback)

    For each query we take up to top 3 results, merge tokens and filter noise.
    Noise: generic buzz (new, trending, viral, remix(es) duplicates, year tags).
    Returns unique, normalized tokens sorted (for stable CSV diffs) or None.
    """
    cid = get_valid_client_id()  # Use auto-refresh version
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

    # Words that indicate title/lyric fragments (not genres)
    title_indicator_words = {
        "you", "me", "i", "my", "your", "we", "us", "the", "a", "an", "is", "are", "was", 
        "were", "be", "been", "am", "can", "could", "would", "should", "will", "do", "does",
        "did", "have", "has", "had", "way", "like", "love", "miss", "want", "need", "know",
        "feel", "tell", "say", "see", "look", "time", "full", "version", "song"
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
        # For multi-word phrases: check if it looks like a genre vs title fragment
        if " " in t:
            words = set(t.split())
            # Reject if >50% of words are title/lyric indicators
            title_word_count = len(words & title_indicator_words)
            if title_word_count > len(words) * 0.5:
                return False
            # Accept confirmed genre phrases
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
            time.sleep(0.8)  # Increased delay to avoid rate limiting
            _SC_REQUESTS += 1
            r = requests.get(API_SEARCH, params={"q": q, "client_id": cid, "limit": 5}, timeout=_DEF_TIMEOUT)
            if r.status_code == 403:
                # Rate limit or client_id issue - wait longer and retry once
                time.sleep(2.0)
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


def get_track_year(artist: str, title: str, version: str = "") -> Optional[str]:
    """Get upload year from SoundCloud for a track (especially remixes/edits).
    
    Returns year (e.g., "2024") from created_at field, or None if not found.
    """
    cid = get_valid_client_id()
    if not cid:
        return None
    
    queries = _candidate_queries(artist, title, version)
    if not queries:
        return None
    
    try:
        global _SC_REQUESTS
        # Try first query only (most specific)
        q = queries[0]
        time.sleep(0.8)
        _SC_REQUESTS += 1
        r = requests.get(API_SEARCH, params={"q": q, "client_id": cid, "limit": 5}, timeout=_DEF_TIMEOUT)
        if r.status_code == 403:
            time.sleep(2.0)
            r = requests.get(API_SEARCH, params={"q": q, "client_id": cid, "limit": 5}, timeout=_DEF_TIMEOUT)
        if r.status_code != 200:
            return None
        
        data = r.json() or {}
        items = (data.get("collection") or [])[:3]
        
        # Try to find best matching track
        for item in items:
            created_at = item.get("created_at")
            if created_at:
                # created_at format: "2024-02-04T10:30:00Z"
                try:
                    year = created_at[:4]
                    if year.isdigit() and len(year) == 4:
                        return year
                except Exception:
                    continue
        return None
    except Exception:
        return None


def get_track_artwork_url(artist: str, title: str, version: str = "", client_id: Optional[str] = None) -> Optional[str]:
    """Get artwork URL from SoundCloud for a track.
    
    Returns high-res artwork URL (t500x500) or None if not found.
    """
    cid = client_id or get_valid_client_id()
    if not cid:
        return None
    
    queries = _candidate_queries(artist, title, version)
    if not queries:
        return None
    
    try:
        global _SC_REQUESTS
        # Try first query only (most specific)
        q = queries[0]
        time.sleep(0.8)
        _SC_REQUESTS += 1
        r = requests.get(API_SEARCH, params={"q": q, "client_id": cid, "limit": 5}, timeout=_DEF_TIMEOUT)
        if r.status_code == 403:
            time.sleep(2.0)
            r = requests.get(API_SEARCH, params={"q": q, "client_id": cid, "limit": 5}, timeout=_DEF_TIMEOUT)
        if r.status_code != 200:
            return None
        
        data = r.json() or {}
        items = (data.get("collection") or [])[:3]
        
        # Try to find track with artwork
        for item in items:
            artwork_url = item.get("artwork_url")
            if artwork_url:
                # Replace 'large' (100x100) with 't500x500' for better quality
                # Also try t1080x1080 for even better quality
                high_res_url = artwork_url.replace('-large.', '-t1080x1080.')
                return high_res_url
        return None
    except Exception:
        return None


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


def _load_cached_client_id() -> Optional[str]:
    """Load client_id from cache if valid (not expired)."""
    if not _CLIENT_ID_CACHE_PATH.exists():
        return None
    
    try:
        with open(_CLIENT_ID_CACHE_PATH, 'r') as f:
            data = json.load(f)
        
        client_id = data.get('client_id')
        cached_at = data.get('cached_at')
        
        if not client_id or not cached_at:
            return None
        
        # Check if cache expired (30 days)
        cached_time = datetime.fromisoformat(cached_at)
        if datetime.now() - cached_time > timedelta(days=_CLIENT_ID_CACHE_DAYS):
            return None
        
        return client_id
    except Exception:
        return None


def _save_cached_client_id(client_id: str) -> None:
    """Save client_id to cache with timestamp."""
    _CLIENT_ID_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    data = {
        'client_id': client_id,
        'cached_at': datetime.now().isoformat()
    }
    
    with open(_CLIENT_ID_CACHE_PATH, 'w') as f:
        json.dump(data, f, indent=2)


def _is_client_id_valid(client_id: str) -> bool:
    """Test if client_id works with SoundCloud API."""
    try:
        r = requests.get(
            API_SEARCH,
            params={"q": "test", "client_id": client_id, "limit": 1},
            timeout=5,
        )
        return r.status_code == 200
    except Exception:
        return False


def _refresh_client_id_playwright() -> Optional[str]:
    """Extract fresh client_id from SoundCloud using Playwright.
    
    Strategy:
    1. Open soundcloud.com in headless browser
    2. Intercept network requests to api-v2.soundcloud.com
    3. Extract client_id from query parameters
    4. Return fresh client_id
    
    Returns client_id or None if extraction fails.
    """
    try:
        from playwright.sync_api import sync_playwright
        
        client_id = None
        
        def handle_request(route, request):
            nonlocal client_id
            # Check if this is an API request with client_id
            if 'api-v2.soundcloud.com' in request.url and 'client_id=' in request.url:
                # Extract client_id from URL
                import re
                match = re.search(r'client_id=([a-zA-Z0-9]+)', request.url)
                if match:
                    client_id = match.group(1)
            route.continue_()
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # Intercept all requests
            page.route('**/*', handle_request)
            
            # Visit SoundCloud homepage - this will trigger API calls
            page.goto('https://soundcloud.com/discover', wait_until='networkidle', timeout=30000)
            
            # Wait a bit for API calls to complete
            page.wait_for_timeout(2000)
            
            browser.close()
        
        return client_id if client_id else None
    
    except Exception as e:
        print(f"⚠ SoundCloud auto-refresh failed: {e}")
        return None


def get_valid_client_id() -> Optional[str]:
    """Get valid SoundCloud client_id with automatic refresh.
    
    Priority:
    1. Check environment/config (DJLIB_SOUNDCLOUD_CLIENT_ID)
    2. Load from cache if not expired
    3. Validate cached client_id
    4. Auto-refresh if expired/invalid
    5. Save new client_id to cache
    
    Returns valid client_id or None if all methods fail.
    """
    # First, try environment/config
    env_client_id = get_soundcloud_client_id()
    if env_client_id and _is_client_id_valid(env_client_id):
        return env_client_id
    
    # Try cached client_id
    cached = _load_cached_client_id()
    if cached and _is_client_id_valid(cached):
        return cached
    
    # Cache expired or invalid - auto-refresh
    print("ℹ SoundCloud client_id wygasł lub nieprawidłowy, odświeżanie...")
    fresh_id = _refresh_client_id_playwright()
    
    if fresh_id and _is_client_id_valid(fresh_id):
        _save_cached_client_id(fresh_id)
        print(f"✅ SoundCloud client_id odświeżony i zapisany w cache (~{_CLIENT_ID_CACHE_DAYS} dni)")
        return fresh_id
    
    print("⚠ Nie udało się odświeżyć SoundCloud client_id")
    return None


def client_id_status() -> Dict[str, str]:
    """Check SoundCloud client_id status for health monitoring.
    
    Returns:
        dict with 'status' (ok/expired/missing/error) and 'message'
    """
    env_id = get_soundcloud_client_id()
    cached = _load_cached_client_id()
    
    if env_id:
        if _is_client_id_valid(env_id):
            return {"status": "ok", "message": "Client ID z konfiguracji działa"}
        else:
            return {"status": "expired", "message": "Client ID z konfiguracji wygasł - użyj auto-refresh"}
    
    if cached:
        if _is_client_id_valid(cached):
            cached_time = datetime.fromisoformat(
                json.loads(_CLIENT_ID_CACHE_PATH.read_text()).get('cached_at', '')
            )
            days_old = (datetime.now() - cached_time).days
            return {
                "status": "ok", 
                "message": f"Client ID z cache działa ({days_old}/{_CLIENT_ID_CACHE_DAYS} dni)"
            }
        else:
            return {"status": "expired", "message": "Client ID z cache wygasł - auto-refresh dostępny"}
    
    return {"status": "missing", "message": "Brak client_id - ustaw DJLIB_SOUNDCLOUD_CLIENT_ID lub użyj auto-refresh"}

