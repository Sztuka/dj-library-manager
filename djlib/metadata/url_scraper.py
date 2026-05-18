"""URL metadata scraper — extract track info from SoundCloud, YouTube, Beatport URLs.

Uses two strategies:
1. SoundCloud Resolve API (when client_id available) — structured JSON
2. HTML og:tags fallback — works for any website (SoundCloud, YouTube, Beatport, etc.)

The scraper extracts: artist, title, version, genre, year, artwork URL.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 12
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def scrape_url(url: str) -> Dict[str, str]:
    """Scrape metadata from a URL.  Returns dict with keys:

    - ``artist``, ``title``, ``version`` (parsed from page title)
    - ``genre`` (if available)
    - ``year`` (upload/release year)
    - ``artwork_url`` (og:image)
    - ``source`` (e.g. "soundcloud", "youtube", "beatport", "generic")
    - ``description`` (og:description, truncated)
    - ``url`` (canonical URL)

    All values are strings; missing fields are empty strings.
    Raises ``ValueError`` for invalid URLs, ``requests.RequestException`` on
    network errors.
    """
    url = (url or "").strip()
    if not url:
        raise ValueError("Empty URL")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")

    host = (parsed.hostname or "").lower()

    # Route to specialised scraper
    if "soundcloud.com" in host:
        return _scrape_soundcloud(url)
    if "beatport.com" in host:
        return _scrape_beatport(url)
    if "hypeddit.com" in host:
        return _scrape_hypeddit(url)
    if "youtube.com" in host or "youtu.be" in host:
        return _scrape_generic(url, source="youtube")
    if "1001tracklists.com" in host:
        return _scrape_generic(url, source="1001tracklists")

    return _scrape_generic(url, source="generic")


# ---------------------------------------------------------------------------
# SoundCloud
# ---------------------------------------------------------------------------

def _sc_html_metadata(url: str) -> Dict[str, str]:
    """Extract track metadata from raw SoundCloud page HTML.

    SC embeds server-side JSON in ``window.__sc_hydration`` — no auth needed.
    Returns dict with keys: ``year``, ``genre``, ``tags`` (all strings, may be empty).
    """
    result: Dict[str, str] = {"year": "", "genre": "", "tags": ""}
    try:
        resp = requests.get(url, timeout=_TIMEOUT, headers={"User-Agent": _USER_AGENT})
        if resp.status_code != 200:
            return result
        html = resp.text
        # Upload year from "created_at":"2023-06-07T09:24:54Z"
        m = re.search(r'"created_at"\s*:\s*"(\d{4})-\d{2}-\d{2}', html)
        if m:
            result["year"] = m.group(1)
        else:
            m = re.search(r'datetime="(\d{4})-\d{2}-\d{2}', html)
            if m:
                result["year"] = m.group(1)
        # Genre field (set by uploader)
        m = re.search(r'"genre"\s*:\s*"([^"]{2,60})"', html)
        if m:
            result["genre"] = m.group(1).strip()
        # Tag list (space-separated, often contains genre keywords)
        m = re.search(r'"tag_list"\s*:\s*"([^"]{2,200})"', html)
        if m:
            result["tags"] = m.group(1).strip()
    except Exception as exc:
        logger.debug("SC HTML metadata scrape failed for %s: %s", url, exc)
    return result


def _sc_html_year(url: str) -> str:
    """Extract upload year from raw SoundCloud page HTML. Returns ``"YYYY"`` or ``""``."""
    return _sc_html_metadata(url)["year"]


def _scrape_soundcloud(url: str) -> Dict[str, str]:
    """Scrape SoundCloud track.

    Strategy chain:
    1. oEmbed API — public, no auth needed, returns title + author + thumbnail
    2. Resolve API with auto-refreshed client_id — full structured JSON with year/genre
    3. HTML hydration scrape — extracts upload year from window.__sc_hydration
    4. URL slug parsing — offline fallback, lossy but instant
    """
    result: Optional[Dict[str, str]] = None

    # Strategy 1: oEmbed (fast, reliable for most public tracks — but no year)
    try:
        data = _sc_oembed(url)
        if data:
            result = data
    except Exception as exc:
        logger.debug("SC oEmbed failed: %s", exc)

    # Strategy 2: Resolve API — adds year + genre on top of oEmbed result
    if not result or not result.get("year"):
        try:
            from djlib.metadata.soundcloud import get_valid_client_id

            cid = get_valid_client_id()
            if cid:
                resolve_data = _sc_resolve_api(url, cid)
                if resolve_data:
                    result = resolve_data  # full data including year
        except Exception as exc:
            logger.debug("SC Resolve API failed: %s", exc)

    # Strategy 3: HTML scrape for upload year (no auth needed)
    if result and not result.get("year"):
        result["year"] = _sc_html_year(url)

    # Strategy 4: parse URL slug (lossy but guaranteed)
    if not result:
        result = _sc_parse_url_slug(url)
        if not result.get("year"):
            result["year"] = _sc_html_year(url)

    return result  # type: ignore[return-value]


def _sc_oembed(url: str) -> Optional[Dict[str, str]]:
    """Call SoundCloud oEmbed API — no auth required.

    Returns structured track data or None on failure.
    oEmbed docs: https://developers.soundcloud.com/docs/oembed
    """
    resp = requests.get(
        "https://soundcloud.com/oembed",
        params={"format": "json", "url": url},
        timeout=_TIMEOUT,
        headers={"User-Agent": _USER_AGENT},
    )
    if resp.status_code != 200:
        logger.debug("SC oEmbed returned %d", resp.status_code)
        return None

    d = resp.json()
    oembed_title = d.get("title", "")  # "Strobe by deadmau5"
    author = d.get("author_name", "")
    thumbnail = d.get("thumbnail_url", "") or ""

    if not oembed_title:
        return None

    # oEmbed title format: "Track Title by Author" — parse it
    artist = author
    title = oembed_title
    version = ""

    # Remove " by Author" suffix if present
    if author and oembed_title.lower().endswith(f" by {author.lower()}"):
        title = oembed_title[: -(len(author) + 4)].strip()

    # Parse "Artist - Title (Version)" from the cleaned title
    artist_parsed, title_parsed, version_parsed = _parse_sc_title(title, author)
    if title_parsed:
        artist = artist_parsed
        title = title_parsed
        version = version_parsed

    # Upgrade thumbnail to higher resolution
    if thumbnail:
        thumbnail = thumbnail.replace("-large.", "-t500x500.")

    return {
        "artist": artist,
        "title": title,
        "version": version,
        "genre": "",
        "year": "",
        "artwork_url": thumbnail,
        "source": "soundcloud",
        "description": "",
        "url": url,
    }


def _sc_resolve_api(url: str, client_id: str) -> Optional[Dict[str, str]]:
    """Call SoundCloud Resolve API → structured track JSON."""
    resp = requests.get(
        "https://api-v2.soundcloud.com/resolve",
        params={"url": url, "client_id": client_id},
        timeout=_TIMEOUT,
        headers={"User-Agent": _USER_AGENT},
    )
    if resp.status_code != 200:
        logger.debug("SC Resolve returned %d", resp.status_code)
        return None

    d = resp.json()
    if d.get("kind") != "track":
        logger.debug("SC Resolve returned kind=%s (not track)", d.get("kind"))
        return None

    sc_title = d.get("title", "")
    sc_user = (d.get("user") or {}).get("username", "")
    sc_genre = d.get("genre", "")
    sc_created = d.get("created_at", "")
    sc_artwork = d.get("artwork_url", "") or ""
    sc_description = d.get("description", "") or ""
    sc_permalink = d.get("permalink_url", "") or url

    # Parse "Artist - Title (Version)" from SC title
    artist, title, version = _parse_sc_title(sc_title, sc_user)

    year = sc_created[:4] if sc_created and sc_created[:4].isdigit() else ""

    # Upgrade artwork to higher resolution
    if sc_artwork:
        sc_artwork = sc_artwork.replace("-large.", "-t500x500.")

    return {
        "artist": artist,
        "title": title,
        "version": version,
        "genre": sc_genre,
        "year": year,
        "artwork_url": sc_artwork,
        "source": "soundcloud",
        "description": sc_description[:300],
        "url": sc_permalink,
    }


def _sc_parse_url_slug(url: str) -> Dict[str, str]:
    """Parse SoundCloud URL slug into metadata.

    SoundCloud pages are JS-rendered so og:tags are useless.  The URL slug
    contains the uploader and a slugified track title, e.g.:
    ``/dj-sacha-music/dua-lipa-double-touch-one-kiss-x-woolfie-dj-sacha-mashup``
    """
    parsed = urlparse(url)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        return _empty_result("soundcloud", url=url)

    uploader_slug = parts[0]  # "dj-sacha-music"
    track_slug = parts[1]  # "dua-lipa-double-touch-one-kiss-..."

    # Convert slug to human-readable text
    uploader = _slug_to_name(uploader_slug)
    track_text = _slug_to_name(track_slug)

    # Try to parse "Artist - Title (Version)" from the slug text
    artist, title, version = _parse_sc_title(track_text, uploader)

    return {
        "artist": artist,
        "title": title,
        "version": version,
        "genre": "",
        "year": "",
        "artwork_url": "",
        "source": "soundcloud",
        "description": "",
        "url": url,
    }


def _slug_to_name(slug: str) -> str:
    """Convert a URL slug like ``dj-sacha-music`` → ``Dj Sacha Music``."""
    # Replace hyphens with spaces
    text = slug.replace("-", " ")
    # Title-case but preserve common DJ abbreviations
    words = text.split()
    result = []
    for w in words:
        if w.lower() in ("dj", "mc", "djs", "vs", "ft", "vip"):
            result.append(w.upper() if len(w) <= 2 else w.capitalize())
        elif w.lower() in ("x",):
            result.append(w.lower())
        else:
            result.append(w.capitalize())
    return " ".join(result)


def _parse_sc_title(sc_title: str, sc_user: str) -> tuple[str, str, str]:
    """Parse SoundCloud track title into (artist, title, version).

    SC titles use various formats:
    - "Artist - Title (Remix)"
    - "Title (Artist Remix)"   ← uploader is the "artist"
    - "Artist - Title"
    - "Title"
    """
    version = ""

    # Extract version from parentheses/brackets
    ver_match = re.search(r'[\(\[]([^\)\]]*(?:remix|edit|mashup|bootleg|mix|version|vip|rework|refix|dub|extended|radio|club)[^\)\]]*)[\)\]]', sc_title, re.IGNORECASE)
    if ver_match:
        version = ver_match.group(1).strip()
        # Remove version from title for cleaner parsing
        sc_title_clean = sc_title[:ver_match.start()] + sc_title[ver_match.end():]
    else:
        sc_title_clean = sc_title

    # Try "Artist - Title" split
    parts = re.split(r'\s*[-–—]\s*', sc_title_clean, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        artist = parts[0].strip()
        title = parts[1].strip()
    else:
        # No dash separator — use uploader as artist
        artist = sc_user
        title = sc_title_clean.strip()

    # Clean up trailing/leading whitespace and common noise
    title = re.sub(r'\s+', ' ', title).strip()
    artist = re.sub(r'\s+', ' ', artist).strip()

    # Remove trailing quality markers
    title = re.sub(r'\s*\(?\d{3,4}kbps\)?\s*$', '', title, flags=re.IGNORECASE).strip()
    title = re.sub(r'\s*\[(?:free\s+)?(?:download|dl|buy)\]?\s*$', '', title, flags=re.IGNORECASE).strip()
    title = re.sub(r'\s*\((?:free\s+)?(?:download|dl|buy)\)?\s*$', '', title, flags=re.IGNORECASE).strip()

    return artist, title, version


# ---------------------------------------------------------------------------
# Hypeddit
# ---------------------------------------------------------------------------

def _scrape_hypeddit(url: str) -> Dict[str, str]:
    """Scrape Hypeddit track page.

    Hypeddit stores genre and SoundCloud URL in hidden form inputs that
    og:tags don't expose:
        <input type="hidden" name="genre"         value="Afro House">
        <input type="hidden" name="permalink_url" value="https://soundcloud.com/...">

    Falls back to og:tags for title/artist.
    """
    resp = requests.get(url, timeout=_TIMEOUT, headers={"User-Agent": _USER_AGENT}, allow_redirects=True)
    resp.raise_for_status()
    html = resp.text

    result = _empty_result("hypeddit", url=url)

    # Extract hidden input fields by name
    def _hidden(name: str) -> str:
        m = re.search(
            rf'<input[^>]+name=["\']?{re.escape(name)}["\']?[^>]+value=["\']([^"\']*)["\']',
            html, re.IGNORECASE,
        ) or re.search(
            rf'<input[^>]+value=["\']([^"\']*)["\'][^>]+name=["\']?{re.escape(name)}["\']?',
            html, re.IGNORECASE,
        )
        return m.group(1).strip() if m else ""

    genre = _hidden("genre")
    sc_url = _hidden("permalink_url")

    # og:tags for title/artist
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        og: Dict[str, str] = {}
        for meta in soup.find_all("meta"):
            prop = meta.get("property", "") or meta.get("name", "")
            content = meta.get("content", "")
            if prop and content:
                og[prop] = content
        og_title = og.get("og:title", "")
        if " - " in og_title:
            parts = og_title.split(" - ", 1)
            result["artist"] = parts[0].strip()
            result["title"] = parts[1].strip()
        elif og_title:
            result["title"] = og_title
    except Exception:
        pass

    if genre:
        result["genre"] = genre

    # If SC URL is available and track is live, enrich from SC using same
    # strategy chain as _scrape_soundcloud: oEmbed → Resolve API → HTML scrape
    if sc_url and "soundcloud.com" in sc_url:
        import html as _html
        sc_url_clean = _html.unescape(sc_url)
        result["url"] = sc_url_clean
        try:
            sc_data = _sc_oembed(sc_url_clean)
            if sc_data:
                if not result["artist"]:
                    result["artist"] = sc_data.get("artist", "")
                if not result["title"]:
                    result["title"] = sc_data.get("title", "")
                if sc_data.get("year"):
                    result["year"] = sc_data["year"]
        except Exception:
            pass

        # oEmbed never returns year — try Resolve API (structured JSON with created_at)
        if not result["year"]:
            try:
                from djlib.metadata.soundcloud import get_valid_client_id
                cid = get_valid_client_id()
                if cid:
                    resolve_data = _sc_resolve_api(sc_url_clean, cid)
                    if resolve_data and resolve_data.get("year"):
                        result["year"] = resolve_data["year"]
            except Exception:
                pass

        # Last resort: scrape SC page HTML for created_at in __sc_hydration
        if not result["year"]:
            result["year"] = _sc_html_year(sc_url_clean)

        # Track is private/deleted on SC — Wayback Machine knows when it existed
        if not result["year"]:
            result["year"] = _wayback_year(sc_url_clean)

    return result


# ---------------------------------------------------------------------------
# Wayback Machine year estimation
# ---------------------------------------------------------------------------

def _wayback_year(url: str) -> str:
    """Estimate upload year from the Wayback Machine CDX API.

    When a SoundCloud track is private or deleted the SC API returns 404, but
    the Wayback Machine may have crawled the page while it was still public.
    The earliest archived timestamp gives us a reliable upper-bound for the
    year the track existed.

    Retries up to 3 times with increasing timeouts because CDX can be slow
    or return transient 503s.  Returns ``"YYYY"`` or ``""`` on definitive failure.
    """
    import json as _json
    import subprocess
    import time as _time
    import urllib.parse
    from urllib.parse import urlparse, urlunparse

    # Strip query string — Wayback archives canonical URLs without UTM/si params
    parsed = urlparse(url)
    canonical = urlunparse(parsed._replace(query="", fragment=""))

    params = urllib.parse.urlencode({
        "url": canonical,
        "output": "json",
        "limit": 1,
        "fl": "timestamp,statuscode",
        "from": "2015",
    })
    cdx_url = f"https://web.archive.org/cdx/search/cdx?{params}"

    # curl instead of urllib — handles IPv4/IPv6 fallback on macOS where
    # Python's urllib may stall trying IPv6 when only IPv4 reaches the host.
    timeouts = [15, 20, 25]
    last_exc: Optional[str] = None
    for attempt, max_t in enumerate(timeouts, start=1):
        try:
            proc = subprocess.run(
                ["curl", "-s", "--max-time", str(max_t), "-A", _USER_AGENT, cdx_url],
                capture_output=True, text=True, timeout=max_t + 5,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                rows = _json.loads(proc.stdout)
                if len(rows) < 2:
                    return ""  # no snapshots found — definitive empty
                earliest_ts = rows[1][0]
                status_code = rows[1][1] if len(rows[1]) > 1 else "200"
                if status_code.startswith("4") and status_code != "404":
                    return ""
                year = earliest_ts[:4]
                if year.isdigit():
                    logger.debug(
                        "Wayback year for %s: %s (snapshot %s, status %s, attempt %d)",
                        canonical, year, earliest_ts, status_code, attempt,
                    )
                    return year
            last_exc = f"curl rc={proc.returncode} stdout={proc.stdout[:60]!r}"
        except Exception as exc:
            last_exc = str(exc)

        if attempt < len(timeouts):
            _time.sleep(attempt)  # 1s, 2s between retries

    logger.warning(
        "Wayback CDX niedziałający po %d próbach dla %s (%s) — "
        "wpisz rok ręcznie w kolumnie Year",
        len(timeouts), canonical, last_exc,
    )
    return ""


# ---------------------------------------------------------------------------
# Beatport
# ---------------------------------------------------------------------------

def _scrape_beatport(url: str) -> Dict[str, str]:
    """Scrape Beatport track page — extract from og:tags + JSON-LD."""
    og = _fetch_og_tags(url)
    result = _og_to_result(og, source="beatport")

    # Beatport og:title format: "Track Title (Remix) by Artist on Beatport"
    og_title = og.get("og:title", "")
    bp_match = re.match(
        r'^(.+?)\s+by\s+(.+?)\s+on\s+Beatport\s*$',
        og_title,
        re.IGNORECASE,
    )
    if bp_match:
        title_part = bp_match.group(1).strip()
        artist = bp_match.group(2).strip()

        # Extract version from title
        version = ""
        ver_match = re.search(
            r'[\(\[]([^\)\]]+(?:remix|edit|mix|version|extended|original|radio|club|dub|bootleg)[^\)\]]*)[\)\]]',
            title_part, re.IGNORECASE,
        )
        if ver_match:
            version = ver_match.group(1).strip()
            title_part = (
                title_part[:ver_match.start()] + title_part[ver_match.end():]
            ).strip()

        result["artist"] = artist
        result["title"] = title_part
        result["version"] = version

    return result


# ---------------------------------------------------------------------------
# Generic HTML og:tags scraper
# ---------------------------------------------------------------------------

def _scrape_generic(url: str, source: str = "generic") -> Dict[str, str]:
    """Scrape any page via og:tags / meta tags."""
    og = _fetch_og_tags(url)
    result = _og_to_result(og, source=source)

    # Try to parse "Artist - Title" from og:title
    og_title = og.get("og:title", "")
    if " - " in og_title or " – " in og_title or " — " in og_title:
        parts = re.split(r'\s*[-–—]\s*', og_title, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            result["artist"] = parts[0].strip()
            title_part = parts[1].strip()

            # YouTube: strip " (Official Audio/Video/Lyric)" etc.
            title_part = re.sub(
                r'\s*[\(\[](?:official\s+)?(?:audio|video|lyric(?:s)?|music\s+video|visuali[sz]er|hd|4k)[\)\]]\s*$',
                '', title_part, flags=re.IGNORECASE,
            ).strip()

            # Extract version
            ver_match = re.search(
                r'[\(\[]([^\)\]]+(?:remix|edit|mashup|bootleg|mix|version|vip|rework|extended|radio|club|dub)[^\)\]]*)[\)\]]',
                title_part, re.IGNORECASE,
            )
            if ver_match:
                result["version"] = ver_match.group(1).strip()
                title_part = (
                    title_part[:ver_match.start()]
                    + title_part[ver_match.end():]
                ).strip()

            result["title"] = title_part

    return result


def _fetch_og_tags(url: str) -> Dict[str, str]:
    """Fetch HTML and extract OpenGraph + standard meta tags."""
    resp = requests.get(
        url,
        timeout=_TIMEOUT,
        headers={"User-Agent": _USER_AGENT},
        allow_redirects=True,
    )
    resp.raise_for_status()

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("beautifulsoup4 not installed — using regex fallback")
        return _parse_og_regex(resp.text)

    soup = BeautifulSoup(resp.text, "html.parser")
    og: Dict[str, str] = {}

    for meta in soup.find_all("meta"):
        prop = meta.get("property", "") or meta.get("name", "")
        content = meta.get("content", "")
        if prop and content:
            og[prop] = content

    # Fallback: <title> tag
    if "og:title" not in og:
        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            og["og:title"] = title_tag.string.strip()

    # JSON-LD (Beatport, some SoundCloud pages)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            import json
            ld = json.loads(script.string or "")
            if isinstance(ld, dict):
                if ld.get("@type") in ("MusicRecording", "MusicComposition", "AudioObject"):
                    og.setdefault("ld:name", ld.get("name", ""))
                    og.setdefault("ld:artist", ld.get("byArtist", {}).get("name", "") if isinstance(ld.get("byArtist"), dict) else "")
                    og.setdefault("ld:genre", ld.get("genre", ""))
                    og.setdefault("ld:datePublished", ld.get("datePublished", ""))
        except Exception:
            pass

    return og


def _parse_og_regex(html: str) -> Dict[str, str]:
    """Regex fallback for og:tag extraction (no bs4)."""
    og: Dict[str, str] = {}
    for m in re.finditer(
        r'<meta\s+(?:property|name)=["\']([^"\']+)["\']\s+content=["\']([^"\']*)["\']',
        html, re.IGNORECASE,
    ):
        og[m.group(1)] = m.group(2)
    # Reverse order (content first)
    for m in re.finditer(
        r'<meta\s+content=["\']([^"\']*?)["\']\s+(?:property|name)=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    ):
        og[m.group(2)] = m.group(1)
    # <title>
    tm = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
    if tm and "og:title" not in og:
        og["og:title"] = tm.group(1).strip()
    return og


def _og_to_result(og: Dict[str, str], source: str) -> Dict[str, str]:
    """Convert og:tags dict to a standardised result dict."""
    title = og.get("og:title", "")
    description = og.get("og:description", "")
    image = og.get("og:image", "")
    url = og.get("og:url", "")

    # Try ld:json data first
    artist = og.get("ld:artist", "")
    if not artist:
        # Some SC pages put artist in og:description or music:musician
        artist = og.get("music:musician:name", "")

    year = ""
    date = og.get("ld:datePublished", "") or og.get("music:release_date", "")
    if date and date[:4].isdigit():
        year = date[:4]

    genre = og.get("ld:genre", "") or og.get("music:genre", "")

    return {
        "artist": artist,
        "title": title,
        "version": "",
        "genre": genre,
        "year": year,
        "artwork_url": image,
        "source": source,
        "description": description[:300] if description else "",
        "url": url or "",
    }


def _empty_result(source: str, url: str = "") -> Dict[str, str]:
    """Return an empty result dict."""
    return {
        "artist": "",
        "title": "",
        "version": "",
        "genre": "",
        "year": "",
        "artwork_url": "",
        "source": source,
        "description": "",
        "url": url,
    }
