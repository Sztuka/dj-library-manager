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
    if "youtube.com" in host or "youtu.be" in host:
        return _scrape_generic(url, source="youtube")
    if "1001tracklists.com" in host:
        return _scrape_generic(url, source="1001tracklists")

    return _scrape_generic(url, source="generic")


# ---------------------------------------------------------------------------
# SoundCloud
# ---------------------------------------------------------------------------

def _scrape_soundcloud(url: str) -> Dict[str, str]:
    """Scrape SoundCloud track.

    Strategy chain (stops at first success):
    1. oEmbed API — public, no auth needed, returns title + author + thumbnail
    2. Resolve API with auto-refreshed client_id — full structured JSON
    3. URL slug parsing — offline fallback, lossy but instant
    """
    # Strategy 1: oEmbed (fast, reliable for most public tracks)
    try:
        data = _sc_oembed(url)
        if data:
            return data
    except Exception as exc:
        logger.debug("SC oEmbed failed: %s", exc)

    # Strategy 2: Resolve API with auto-refreshed client_id
    try:
        from djlib.metadata.soundcloud import get_valid_client_id

        cid = get_valid_client_id()
        if cid:
            data = _sc_resolve_api(url, cid)
            if data:
                return data
    except Exception as exc:
        logger.debug("SC Resolve API failed: %s", exc)

    # Strategy 3: parse URL slug (lossy but guaranteed)
    return _sc_parse_url_slug(url)


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
