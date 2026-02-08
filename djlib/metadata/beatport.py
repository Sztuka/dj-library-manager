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
import re
import requests
import time
import json
import base64
from pathlib import Path
from datetime import datetime, timezone

# Rate limiting
_LAST_REQUEST = 0.0
MIN_INTERVAL = 1.0  # Beatport: 1 req/s

# Version matching constants
_MIN_REMIXER_NAME_LEN = 4  # Minimum length for remixer name to be meaningful
_MIN_WORD_LEN = 4  # Minimum word length to be "significant"
_MIN_LONG_WORD_LEN = 6  # Length for single-word remixer name matching
_REMIX_SUFFIX_PATTERN = re.compile(
    r'\s*(extended\s*)?(club\s*)?(radio\s*)?(original\s*)?(remix|mix|edit|dub|version)\s*$',
    re.IGNORECASE
)

# Token refresh lock (prevents concurrent refresh attempts)
_REFRESHING = False
_MEMORY_TOKEN: Optional[str] = None  # In-memory cache for current process

API_BASE = "https://api.beatport.com/v4"
CACHE_DIR = Path.home() / ".djlib"
TOKEN_CACHE = CACHE_DIR / "beatport_token.json"

# In-process cache to avoid repeated disk reads / refresh checks
_TOKEN_IN_MEMORY: Optional[str] = None

# Refresh cooldown (avoid repeated Playwright logins across rows/runs)
REFRESH_COOLDOWN_SECONDS = 10 * 60


def _now_ts() -> float:
    return time.time()


def _load_cache_doc() -> Dict:
    if not TOKEN_CACHE.exists():
        return {}
    try:
        with open(TOKEN_CACHE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache_doc(data: Dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(TOKEN_CACHE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def _get_last_refresh_attempt_ts() -> Optional[float]:
    doc = _load_cache_doc()
    ts = doc.get("last_refresh_attempt_ts")
    try:
        return float(ts) if ts is not None else None
    except Exception:
        return None


def _get_last_refresh_failed() -> bool:
    """Check if last refresh attempt failed."""
    doc = _load_cache_doc()
    return bool(doc.get("last_refresh_failed", False))


def _set_last_refresh_attempt_ts(ts: float, failed: bool = False) -> None:
    doc = _load_cache_doc()
    doc["last_refresh_attempt_ts"] = ts
    doc["last_refresh_failed"] = failed
    _save_cache_doc(doc)


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
    """Load token from cache file or memory."""
    global _MEMORY_TOKEN
    
    # Check in-memory cache first (fastest)
    if _MEMORY_TOKEN and _is_token_valid(_MEMORY_TOKEN):
        return _MEMORY_TOKEN
    
    # Check file cache
    if not TOKEN_CACHE.exists():
        return None
    
    try:
        with open(TOKEN_CACHE, "r") as f:
            data = json.load(f)
            token = data.get("token", "")
            if token and _is_token_valid(token):
                # Cache in memory for future calls
                _MEMORY_TOKEN = token
                return token
    except Exception:
        pass
        pass
    
    return None


def _save_cached_token(token: str) -> None:
    """Save token to cache file and memory."""
    global _MEMORY_TOKEN
    
    # Save to memory immediately
    _MEMORY_TOKEN = token
    
    payload = _decode_jwt(token)
    exp_timestamp = payload.get("exp", 0) if payload else 0

    doc = _load_cache_doc()
    doc.update(
        {
            "token": token,
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": datetime.fromtimestamp(exp_timestamp, tz=timezone.utc).isoformat() if exp_timestamp else None,
            "last_refresh_attempt_ts": _now_ts(),
        }
    )
    _save_cache_doc(doc)


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
    username = keyring.get_password("djlib_beatport", "username")
    password = keyring.get_password("djlib_beatport", "password")
    
    if not username or not password:
        raise Exception(
            "Beatport credentials not found in keyring. Run: python -m djlib.cli setup-beatport"
        )
    
    print("🔄 Refreshing Beatport token (this takes ~15 seconds)...")
    
    captured_token = None
    api_calls_seen = 0
    
    # Debug mode: set headless=False to see what's happening
    import os
    debug_mode = os.environ.get("BEATPORT_DEBUG", "").lower() in ("1", "true", "yes")
    
    def _is_fresh_token(token: str, min_remaining_seconds: int = 300) -> bool:
        """Check if token has enough time remaining to be useful."""
        try:
            payload = _decode_jwt(token)
            exp = payload.get("exp", 0)
            now = int(datetime.now(timezone.utc).timestamp())
            remaining = exp - now
            return remaining > min_remaining_seconds
        except Exception:
            return False
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not debug_mode)
        # Create fresh context without any stored state
        context = browser.new_context()
        page = context.new_page()
        
        # Clear all storage on beatport.com before we start
        page.goto("https://www.beatport.com", wait_until="domcontentloaded")
        page.evaluate("localStorage.clear(); sessionStorage.clear();")
        context.clear_cookies()
        page.wait_for_timeout(500)
        
        # Intercept API requests AND responses to capture fresh token
        def handle_request(route, request):
            nonlocal captured_token, api_calls_seen
            
            # Log API calls for debugging
            if "api.beatport.com" in request.url:
                api_calls_seen += 1
                auth_header = request.headers.get("authorization", "")
                
                if auth_header.startswith("Bearer "):
                    token = auth_header.replace("Bearer ", "")
                    # Only capture if it's fresh (has > 5 min remaining)
                    if not captured_token and _is_fresh_token(token, min_remaining_seconds=300):
                        captured_token = token
                        print(f"✓ Fresh token captured from API call #{api_calls_seen}")
                    elif debug_mode and not _is_fresh_token(token, min_remaining_seconds=300):
                        payload = _decode_jwt(token)
                        exp = payload.get("exp", 0)
                        now = int(datetime.now(timezone.utc).timestamp())
                        print(f"🔍 Skipping stale token (expires in {exp - now}s)")
            
            route.continue_()
        
        page.route("**/*", handle_request)
        
        try:
            # Go to login page (Beatport uses separate account subdomain)
            page.goto("https://account.beatport.com/", timeout=15000)
            page.wait_for_load_state("domcontentloaded")
            
            # Handle cookie consent if present
            try:
                page.click("button:has-text('I Accept'), button:has-text('Accept'), button:has-text('Essential Only')", timeout=3000)
                page.wait_for_timeout(500)
            except Exception:
                pass  # No cookie banner or already dismissed
            
            # Click "Login" button to open the login modal/form
            try:
                page.click("button:has-text('Login'), a:has-text('Login')", timeout=5000)
                page.wait_for_timeout(2000)  # Wait for modal to appear
            except Exception:
                pass  # Maybe already on login form
            
            # Now wait for login form fields to appear
            page.wait_for_selector("input[type='text'], input[type='email'], input[name='username']", timeout=5000)
            
            # Fill login form (Beatport uses username, not email)
            # Try multiple selectors for username field
            username_filled = False
            for selector in ["input[name='username']", "input[type='email']", "input[type='text']:not([name='search-field']):not([name='vendor-search-handler'])"]:
                try:
                    elem = page.query_selector(selector)
                    if elem and elem.is_visible():
                        page.fill(selector, username)
                        username_filled = True
                        print(f"✓ Filled username using: {selector}")
                        break
                except Exception:
                    continue
            
            if not username_filled:
                raise Exception("Could not find username input field")
            
            page.fill("input[type='password']", password)
            print("✓ Filled password")
            
            # Submit form - try multiple methods
            try:
                # Method 1: Click submit button
                page.click("button[type='submit'], button:has-text('Log in'), button:has-text('Sign in'), button:has-text('Submit')", timeout=3000)
            except Exception:
                try:
                    # Method 2: Press Enter on password field
                    page.press("input[type='password']", "Enter")
                except Exception:
                    raise Exception("Could not submit login form")
            
            print("✓ Submitted login form")
            
            # Wait for navigation after login with retries
            # Beatport login can be slow and may need multiple checks
            login_successful = False
            for attempt in range(5):  # Try up to 5 times
                page.wait_for_timeout(3000)  # Wait 3 seconds between checks
                
                current_url = page.url
                if debug_mode:
                    print(f"🔍 Debug: Current URL = {current_url}")
                
                # Check if we're redirected away from account.beatport.com (to main site)
                if "account.beatport.com" not in current_url or "/login" not in current_url:
                    login_successful = True
                    print(f"✓ Login successful (attempt {attempt + 1})")
                    break
                
                # Check for error messages
                if debug_mode:
                    try:
                        error_text = page.text_content("body")
                        if error_text and ("incorrect" in error_text.lower() or "invalid" in error_text.lower()):
                            print("🔍 Debug: Found error text on page")
                    except Exception:
                        pass
                
                # Still on login page - wait a bit more
                if attempt < 4:  # Don't print on last attempt
                    print(f"⏳ Still on login page, waiting... (attempt {attempt + 1}/5)")
            
            if not login_successful:
                # Take screenshot for debugging if in debug mode
                if debug_mode:
                    screenshot_path = "/tmp/beatport_login_failed.png"
                    page.screenshot(path=screenshot_path)
                    print(f"🔍 Debug: Screenshot saved to {screenshot_path}")
                raise Exception("Login failed - still on login page after 5 attempts. Check credentials.")
            
            # IMPORTANT: Reset captured_token after login success
            # Any token captured before this point was from a stale/cached session
            captured_token = None
            api_calls_seen = 0
            
            # First, go to main beatport.com and wait for frontend to update the token
            # The login sets a cookie, but localStorage token needs JS to run
            page.goto("https://www.beatport.com/", timeout=15000)
            page.wait_for_timeout(3000)  # Wait longer for JS to update localStorage
            
            # Try to get token from localStorage first (more reliable than intercepting)
            def _extract_token_from_storage():
                """Try to extract JWT from Beatport's localStorage."""
                try:
                    # Beatport stores auth data in __bp_store__ (Redux/Zustand persist)
                    bp_store = page.evaluate("localStorage.getItem('__bp_store__')")
                    if bp_store:
                        import json
                        store_data = json.loads(bp_store)
                        # Navigate through possible token locations
                        for key in ['state', 'persist:root', 'auth', 'user']:
                            if isinstance(store_data.get(key), dict):
                                for subkey in ['accessToken', 'access_token', 'token', 'jwt']:
                                    token = store_data[key].get(subkey)
                                    if token and token.startswith('eyJ'):
                                        return token
                        # Try top-level
                        for key in ['accessToken', 'access_token', 'token']:
                            token = store_data.get(key)
                            if token and isinstance(token, str) and token.startswith('eyJ'):
                                return token
                except Exception:
                    pass
                
                # Try direct localStorage keys
                for key in ['access_token', 'accessToken', 'bp_token', 'jwt', 'token']:
                    try:
                        token = page.evaluate(f"localStorage.getItem('{key}')")
                        if token and token.startswith('eyJ'):
                            return token
                    except Exception:
                        pass
                return None
            
            # Try localStorage first
            ls_token = _extract_token_from_storage()
            if ls_token and _is_fresh_token(ls_token, min_remaining_seconds=300):
                captured_token = ls_token
                print("✓ Fresh token captured from localStorage")
            
            # If no token from localStorage, trigger API calls to intercept
            if not captured_token:
                # Now trigger an API call to capture FRESH token
                try:
                    page.goto("https://www.beatport.com/search?q=test", timeout=15000)
                    page.wait_for_timeout(4000)  # Wait for API call
                except Exception as e:
                    # If we already captured token, this timeout is acceptable
                    if not captured_token:
                        raise Exception(f"Failed to trigger API call after login: {e}")
                    print(f"⚠ Warning: Search timeout, but token already captured")
            
            # Still no token? Try one more search with reload
            if not captured_token:
                print("⏳ No fresh token yet, trying page reload...")
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                
                # Check localStorage again after reload
                ls_token = _extract_token_from_storage()
                if ls_token and _is_fresh_token(ls_token, min_remaining_seconds=300):
                    captured_token = ls_token
                    print("✓ Fresh token captured from localStorage after reload")
            
        except Exception as e:
            browser.close()
            raise Exception(f"Beatport login failed: {e}")
        
        browser.close()
    
    if not captured_token:
        raise Exception("Failed to capture fresh Beatport token after login. Token may be stale.")
    
    print("✅ Beatport token refreshed successfully")
    return captured_token


def get_valid_token(max_retries: int = 2, *, force_refresh: bool = False) -> str:
    """Get valid Beatport token - auto-refresh if expired.
    
    Args:
        max_retries: Maximum number of refresh attempts (default: 2)
        force_refresh: Skip cooldown check (e.g. when user explicitly requests refresh)
    
    Returns:
        Valid JWT token
    
    Raises:
        Exception if refresh fails or credentials missing
        
    Cooldown logic:
        - Cooldown only applies if LAST refresh attempt FAILED
        - If token just expired naturally, try to refresh immediately
        - This prevents retry loops when Beatport servers are down, but allows
          normal token refresh when token simply expires
    """
    global _REFRESHING
    global _TOKEN_IN_MEMORY
    global _MEMORY_TOKEN

    # Fast path: in-process token already loaded AND still valid
    # Skip if force_refresh requested (e.g., after 401)
    if not force_refresh and _TOKEN_IN_MEMORY and _is_token_valid(_TOKEN_IN_MEMORY):
        return _TOKEN_IN_MEMORY
    
    # Also check _MEMORY_TOKEN (used by _load_cached_token)
    if not force_refresh and _MEMORY_TOKEN and _is_token_valid(_MEMORY_TOKEN):
        _TOKEN_IN_MEMORY = _MEMORY_TOKEN
        return _MEMORY_TOKEN
    
    # Clear in-memory caches if forcing refresh or token expired
    if force_refresh or (_TOKEN_IN_MEMORY and not _is_token_valid(_TOKEN_IN_MEMORY)):
        _TOKEN_IN_MEMORY = None
        _MEMORY_TOKEN = None
    
    # Try cached token first
    token = _load_cached_token()
    if token:
        _TOKEN_IN_MEMORY = token
        return token
    
    # No valid cached token. Apply cooldown ONLY if last refresh FAILED.
    # If token just expired normally, allow immediate refresh.
    if not force_refresh and _get_last_refresh_failed():
        last_ts = _get_last_refresh_attempt_ts()
        if last_ts is not None and (_now_ts() - last_ts) < REFRESH_COOLDOWN_SECONDS:
            remaining = int(REFRESH_COOLDOWN_SECONDS - (_now_ts() - last_ts))
            raise Exception(
                f"Beatport token refresh cooldown active ({remaining}s left). "
                "Last refresh attempt failed; wait or use --force-beatport-refresh to retry."
            )
    
    # If another process is already refreshing, wait and retry
    if _REFRESHING:
        print("⏳ Waiting for token refresh to complete...")
        import time
        for _ in range(20):  # Wait up to 40 seconds
            time.sleep(2)
            token = _load_cached_token()
            if token:
                return token
        raise Exception("Token refresh timeout - another process failed to refresh")
    
    # Set lock
    _REFRESHING = True
    
    try:
        # Token expired or missing - refresh with retry limit
        last_error = None
        for attempt in range(max_retries):
            try:
                token = _refresh_token_with_playwright()
                _save_cached_token(token)
                _TOKEN_IN_MEMORY = token
                # Clear failed flag on success
                _set_last_refresh_attempt_ts(_now_ts(), failed=False)
                return token
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    print(f"⚠ Token refresh failed (attempt {attempt + 1}/{max_retries}): {e}")
                    import time
                    time.sleep(2)  # Brief pause before retry
                else:
                    print(f"❌ Token refresh failed after {max_retries} attempts")
        
        # All attempts failed - set cooldown
        _set_last_refresh_attempt_ts(_now_ts(), failed=True)
        raise Exception(f"Failed to get valid Beatport token: {last_error}")
    finally:
        # Always release lock
        _REFRESHING = False


def search_track(artist: str, title: str, duration_s: Optional[int] = None, version: Optional[str] = None) -> Optional[Dict]:
    """Search Beatport for track metadata.
    
    Args:
        artist: Artist name
        title: Track title
        duration_s: Track duration in seconds (for better matching)
        version: Version/mix name (e.g., "Bob Sinclar Extended Mix") for remix matching
    
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
    
    # Build search query - include remixer name from version if present
    query = f"{artist} {title}".strip()
    if version:
        # Extract remixer name from versions like "Bob Sinclar Extended Mix"
        # by stripping standard mix type suffixes
        remixer_part = _REMIX_SUFFIX_PATTERN.sub('', version.strip()).strip()
        # Only add if it's a meaningful name (not already in query)
        if remixer_part and len(remixer_part) >= _MIN_REMIXER_NAME_LEN and remixer_part.lower() not in query.lower():
            query = f"{remixer_part} {query}"
    
    try:
        _LAST_REQUEST = time.time()
        response = requests.get(
            f"{API_BASE}/catalog/search/",
            params={"q": query, "type": "tracks"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        
        if response.status_code == 401:
            # Token expired during request - remove cache but don't auto-refresh
            # This prevents infinite loops
            if TOKEN_CACHE.exists():
                TOKEN_CACHE.unlink()

            # Try one forced refresh (bypass cooldown) and retry once.
            try:
                token = get_valid_token(force_refresh=True)
                response = requests.get(
                    f"{API_BASE}/catalog/search/",
                    params={"q": query, "type": "tracks"},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10,
                )
            except Exception:
                print("⚠ Beatport token expired. Re-run command to refresh.")
                return None
        
        if response.status_code != 200:
            return None
        
        data = response.json()
        tracks = data.get("tracks", [])
        
        if not tracks:
            return None
        
        # Normalize string for comparison
        def _normalize(s: str) -> str:
            s = (s or "").lower().strip()
            s = re.sub(r"['\"\-\.]", "", s)  # Remove quotes, apostrophes, hyphens, dots
            s = re.sub(r"\s+", " ", s)  # Normalize whitespace
            return s
        
        def _version_matches(track_mix_name: str, search_version: str) -> int:
            """Score how well track's mix_name matches the searched version.
            
            Returns:
                3 = exact match
                2 = one contains the other (same remix/mix type)
                1 = significant word overlap
                0 = no match
            """
            if not search_version:
                return 0
            mix_norm = _normalize(track_mix_name)
            ver_norm = _normalize(search_version)
            if not mix_norm or not ver_norm:
                return 0
            
            # Exact match - highest score
            if mix_norm == ver_norm:
                return 3
            
            # Check for remix/mix type mismatch ("mix" vs "remix" are different!)
            ver_has_remix = "remix" in ver_norm
            mix_has_remix = "remix" in mix_norm
            remix_type_matches = (ver_has_remix == mix_has_remix)
            
            # If remix types match, check containment
            if remix_type_matches and (mix_norm in ver_norm or ver_norm in mix_norm):
                return 2
            
            # Word overlap matching for remixer attribution
            ver_words = set(ver_norm.split())
            mix_words = set(mix_norm.split())
            common = ver_words & mix_words
            significant_common = [w for w in common if len(w) >= _MIN_WORD_LEN]
            
            # Remix type mismatch requires stronger word evidence
            if not remix_type_matches:
                return 1 if len(significant_common) >= 3 else 0
            
            # Standard word overlap matching
            if len(significant_common) >= 2:
                return 1
            # Single long remixer name match (e.g., "Sinclar" in both)
            if significant_common and any(len(w) >= _MIN_LONG_WORD_LEN for w in significant_common):
                return 1
            return 0
        
        artist_norm = _normalize(artist)
        
        # Find best match WITH artist verification and version matching
        best_match = None
        best_version_score = 0  # Track version match quality (0-3)
        
        for track in tracks[:15]:  # Check top 15 (more for remix matching)
            track_artists = track.get("artists", [])
            track_artist_names = [a.get("name", "") for a in track_artists]
            track_mix_name = track.get("mix_name", "")
            
            # Check if any artist matches
            artist_match = False
            for ta in track_artist_names:
                ta_norm = _normalize(ta)
                # Fuzzy match: one contains the other
                if artist_norm in ta_norm or ta_norm in artist_norm:
                    artist_match = True
                    break
                # Also check individual words for multi-word artists
                artist_words = set(artist_norm.split())
                ta_words = set(ta_norm.split())
                if artist_words and ta_words and len(artist_words & ta_words) >= 1:
                    # At least one significant word matches
                    if any(len(w) > 3 for w in (artist_words & ta_words)):
                        artist_match = True
                        break
            
            if not artist_match:
                continue
            
            # Artist matches - check duration if provided
            if duration_s:
                track_duration_s = track.get("length_ms", 0) // 1000
                duration_diff = abs(track_duration_s - duration_s)
                if duration_diff > 60:  # More than 60s difference - skip
                    continue
            
            # Check if version matches (remix/mix name matching) - returns score 0-3
            this_version_score = _version_matches(track_mix_name, version) if version else 0
            
            # Selection logic:
            # 1. Prefer tracks with higher version match score
            # 2. Among same version score, prefer better duration match
            if best_match is None:
                best_match = track
                best_version_score = this_version_score
            elif this_version_score > best_version_score:
                # This track has better version match - prefer it
                best_match = track
                best_version_score = this_version_score
            elif this_version_score < best_version_score:
                # Previous has better version match - keep previous
                pass
            elif duration_s:
                # Same version score - compare durations
                track_duration_s = track.get("length_ms", 0) // 1000
                best_duration_s = best_match.get("length_ms", 0) // 1000
                if abs(track_duration_s - duration_s) < abs(best_duration_s - duration_s):
                    best_match = track
                    best_version_score = this_version_score
        
        if best_match is None:
            # No matching artist found in Beatport results
            return None
        
        # If we're searching for a remix but found only original, reject match
        # This prevents returning "Hip-Hop" for an Afro House remix
        if version and best_version_score == 0:
            return None
        
        # Extract metadata
        genre = best_match.get("genre", {}).get("name", "")
        sub_genre = best_match.get("sub_genre")
        if sub_genre:
            genre = f"{genre}, {sub_genre.get('name', '')}"
        
        release = best_match.get("release", {})
        # Check both track-level and release-level date fields
        # Date is typically at track level, not release level
        release_date = (
            best_match.get("new_release_date") or  # Primary field
            best_match.get("publish_date") or  # Alternative
            best_match.get("date", {}).get("published") or  # Nested structure
            release.get("new_release_date") or  # Fallback: release level
            ""
        )
        
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
            username = keyring.get_password("djlib_beatport", "username")
            if not username:
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


def set_beatport_credentials(username: str, password: str) -> None:
    """Store Beatport credentials in system keyring.
    
    Args:
        username: Beatport account username
        password: Beatport account password
    """
    try:
        import keyring
        keyring.set_password("djlib_beatport", "username", username)
        keyring.set_password("djlib_beatport", "password", password)
        print("✅ Beatport credentials saved to system keyring")
    except Exception as e:
        raise Exception(f"Failed to save credentials: {e}")
