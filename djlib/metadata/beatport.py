"""Beatport API client with automatic token refresh via Playwright.

Provides:
- Precise EDM subgenre classification (100+ genres: progressive house, melodic techno, etc.)
- High-quality artwork (1400x1400px)
- Accurate release dates
- BPM and Camelot key from Beatport's curated database

Token Management:
- JWT tokens expire after 1 hour
- Auto-refresh via headless browser (Playwright) when expired
- Credentials stored in system keyring for security
- Token cached in ~/.djlib/beatport_token.json
"""
from __future__ import annotations
from typing import Optional, Dict, List, Tuple
import requests
import time
import json
import base64
from pathlib import Path
from datetime import datetime, timezone

# Rate limiting
_LAST_REQUEST = 0.0
MIN_INTERVAL = 1.0  # Beatport: 1 req/s

API_BASE = "https://api.beatport.com/v4"
CACHE_DIR = Path.home() / ".djlib"
TOKEN_CACHE = CACHE_DIR / "beatport_token.json"


def _decode_jwt(token: str) -> Dict | None:
    """Decode JWT payload without verification (for expiry check)."""
    try:
        # JWT format: header.payload.signature
        parts = token.split(".")
        if len(parts) != 3:
            return None
        
        # Decode payload (base64url)
        payload = parts[1]
        # Add padding if needed
        padding = 4 - (len(payload) % 4)
        if padding != 4:
            payload += "=" * padding
        
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return None


def _is_token_valid(token: str, buffer_seconds: int = 300) -> bool:
    """Check if JWT token is still valid (with 5min buffer)."""
    payload = _decode_jwt(token)
    if not payload:
        return False
    
    exp = payload.get("exp")
    if not exp:
        return False
    
    now = int(datetime.now(timezone.utc).timestamp())
    return (exp - now) > buffer_seconds


def _load_cached_token() -> Optional[str]:
    """Load token from cache file."""
    if not TOKEN_CACHE.exists():
        return None
    
    try:
        with open(TOKEN_CACHE, "r") as f:
            data = json.load(f)
            token = data.get("token", "")
            if token and _is_token_valid(token):
                return token
    except Exception:
        pass
    
    return None


def _save_cached_token(token: str) -> None:
    """Save token to cache file."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    payload = _decode_jwt(token)
    exp_timestamp = payload.get("exp", 0) if payload else 0
    
    data = {
        "token": token,
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": datetime.fromtimestamp(exp_timestamp, tz=timezone.utc).isoformat() if exp_timestamp else None
    }
    
    with open(TOKEN_CACHE, "w") as f:
        json.dump(data, f, indent=2)


def _refresh_token_with_playwright() -> str:
    """Refresh Beatport token using headless browser login.
    
    Requires:
    - Beatport credentials in system keyring (use set_beatport_credentials())
    - Playwright installed (pip install playwright && playwright install chromium)
    
    Returns:
        Fresh JWT token
    
    Raises:
        Exception if login fails or credentials missing
    """
    try:
        from playwright.sync_api import sync_playwright
        import keyring
    except ImportError:
        raise Exception(
            "Playwright not installed. Run: pip install playwright && playwright install chromium"
        )
    
    # Get credentials from keyring
    email = keyring.get_password("djlib_beatport", "email")
    password = keyring.get_password("djlib_beatport", "password")
    
    if not email or not password:
        raise Exception(
            "Beatport credentials not found in keyring. Run: python -m djlib.cli setup-beatport"
        )
    
    print("🔄 Refreshing Beatport token (this takes ~10 seconds)...")
    
    captured_token = None
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        # Intercept API requests to capture Authorization header
        def handle_request(route, request):
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                nonlocal captured_token
                if not captured_token:  # Capture first token
                    captured_token = auth_header.replace("Bearer ", "")
            route.continue_()
        
        page.route("**/api.beatport.com/**", handle_request)
        
        try:
            # Go to login page
            page.goto("https://www.beatport.com/login", timeout=15000)
            page.wait_for_load_state("domcontentloaded")
            
            # Fill login form
            page.fill("input[type='email'], input[name='email']", email)
            page.fill("input[type='password'], input[name='password']", password)
            
            # Submit form
            page.click("button[type='submit']")
            page.wait_for_load_state("networkidle", timeout=10000)
            
            # Trigger an API call to capture token (search for anything)
            page.goto("https://www.beatport.com/search?q=test", timeout=10000)
            page.wait_for_timeout(2000)  # Wait for API call
            
        except Exception as e:
            browser.close()
            raise Exception(f"Beatport login failed: {e}")
        
        browser.close()
    
    if not captured_token:
        raise Exception("Failed to capture Beatport token after login. Check credentials.")
    
    print("✅ Beatport token refreshed successfully")
    return captured_token


def get_valid_token() -> str:
    """Get valid Beatport token - auto-refresh if expired.
    
    Returns:
        Valid JWT token
    
    Raises:
        Exception if refresh fails or credentials missing
    """
    # Try cached token first
    token = _load_cached_token()
    if token:
        return token
    
    # Token expired or missing - refresh
    token = _refresh_token_with_playwright()
    _save_cached_token(token)
    return token


def search_track(artist: str, title: str, duration_s: Optional[int] = None) -> Optional[Dict]:
    """Search Beatport for track metadata.
    
    Args:
        artist: Artist name
        title: Track title
        duration_s: Track duration in seconds (for better matching)
    
    Returns:
        Dict with: genre, release_date, artwork_url, bpm, key_camelot, mix_name
        Or None if not found
    """
    global _LAST_REQUEST
    
    if not artist and not title:
        return None
    
    try:
        token = get_valid_token()
    except Exception as e:
        print(f"\n❌ BEATPORT ERROR: {e}")
        print("   Setup required: python -m djlib.metadata.beatport --setup\n")
        return None
    
    # Rate limiting
    elapsed = time.time() - _LAST_REQUEST
    if elapsed < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - elapsed)
    
    query = f"{artist} {title}".strip()
    
    try:
        _LAST_REQUEST = time.time()
        response = requests.get(
            f"{API_BASE}/catalog/search/",
            params={"q": query, "type": "tracks"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        
        if response.status_code == 401:
            # Token expired during request - invalidate cache
            if TOKEN_CACHE.exists():
                TOKEN_CACHE.unlink()
            return None
        
        if response.status_code != 200:
            return None
        
        data = response.json()
        tracks = data.get("tracks", [])
        
        if not tracks:
            return None
        
        # Find best match
        best_match = tracks[0]
        
        # If duration provided, try to find closer match
        if duration_s and len(tracks) > 1:
            for track in tracks[:5]:  # Check top 5
                track_duration_s = track.get("length_ms", 0) // 1000
                duration_diff = abs(track_duration_s - duration_s)
                best_duration_diff = abs(best_match.get("length_ms", 0) // 1000 - duration_s)
                if duration_diff < best_duration_diff and duration_diff < 30:  # Within 30s
                    best_match = track
        
        # Extract metadata
        genre = best_match.get("genre", {}).get("name", "")
        sub_genre = best_match.get("sub_genre")
        if sub_genre:
            genre = f"{genre}, {sub_genre.get('name', '')}"
        
        release = best_match.get("release", {})
        release_date = release.get("new_release_date", "")
        
        # Artwork URL (1400x1400)
        artwork_url = ""
        if release.get("image"):
            dynamic_uri = release["image"].get("dynamic_uri", "")
            if dynamic_uri:
                artwork_url = dynamic_uri.replace("{w}x{h}", "1400x1400")
        
        # Key in Camelot notation
        key_obj = best_match.get("key", {})
        key_camelot = ""
        if key_obj:
            num = key_obj.get("camelot_number")
            letter = key_obj.get("camelot_letter")
            if num and letter:
                key_camelot = f"{num}{letter}"
        
        return {
            "artist": best_match.get("artists", [{}])[0].get("name", artist),
            "title": best_match.get("name", title),
            "mix_name": best_match.get("mix_name", ""),
            "genre": genre,
            "release_date": release_date,
            "release_name": release.get("name", ""),
            "label": release.get("label", {}).get("name", ""),
            "artwork_url": artwork_url,
            "bpm": best_match.get("bpm"),
            "key_camelot": key_camelot,
            "key_name": key_obj.get("name", ""),
            "duration_ms": best_match.get("length_ms"),
            "catalog_number": best_match.get("catalog_number", ""),
            "isrc": best_match.get("isrc", ""),
        }
    
    except Exception as e:
        print(f"Beatport search error: {e}")
        return None


def token_health() -> Dict[str, str]:
    """Check Beatport token health status.
    
    Returns:
        Dict with status: ok|expired|missing|error and message
    """
    token = _load_cached_token()
    
    if not token:
        # Check if credentials are set
        try:
            import keyring
            email = keyring.get_password("djlib_beatport", "email")
            if not email:
                return {
                    "status": "missing",
                    "message": "Beatport credentials not configured"
                }
        except Exception:
            pass
        
        return {
            "status": "expired",
            "message": "Beatport token expired. Will auto-refresh on first use."
        }
    
    # Check expiry
    payload = _decode_jwt(token)
    if not payload:
        return {"status": "error", "message": "Invalid token format"}
    
    exp = payload.get("exp", 0)
    now = int(datetime.now(timezone.utc).timestamp())
    remaining = exp - now
    
    if remaining <= 0:
        return {"status": "expired", "message": "Token expired. Will auto-refresh."}
    
    if remaining < 600:  # < 10 minutes
        return {
            "status": "ok",
            "message": f"Token expires in {remaining // 60} minutes. Will auto-refresh soon."
        }
    
    return {"status": "ok", "message": f"Token valid for {remaining // 60} minutes."}


def set_beatport_credentials(email: str, password: str) -> None:
    """Store Beatport credentials in system keyring.
    
    Args:
        email: Beatport account email
        password: Beatport account password
    """
    try:
        import keyring
        keyring.set_password("djlib_beatport", "email", email)
        keyring.set_password("djlib_beatport", "password", password)
        print("✅ Beatport credentials saved to system keyring")
    except Exception as e:
        raise Exception(f"Failed to save credentials: {e}")
