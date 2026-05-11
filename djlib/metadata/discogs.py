"""Discogs API client — release year lookup for tracks.

Uses the public Discogs database search API:
    https://api.discogs.com/database/search?q=...&type=release

Anonymous: 25 req/min. With a personal token: 60 req/min. Rate limiting is
caller-side (a Semaphore in djlib.review.server) — this module just makes
single requests.

Returns the earliest plausible release year for an artist+title pair, with
preference for results whose artist matches the queried artist exactly.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import requests

from djlib.config import get_discogs_token

log = logging.getLogger(__name__)

_API = "https://api.discogs.com/database/search"
_USER_AGENT = "djlib-manager/0.1 +https://github.com/Sztuka/dj-library-manager"
_TIMEOUT = 10
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def get_release_year(artist: str, title: str) -> Optional[str]:
    """Look up the earliest release year for ``artist - title`` on Discogs.

    Returns ``"YYYY"`` or ``None`` if no plausible match is found.
    """
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist or not title:
        return None

    params = {
        "q": f"{artist} {title}",
        "type": "release",
        "per_page": 25,
    }
    token = get_discogs_token()
    if token:
        params["token"] = token

    try:
        r = requests.get(
            _API, params=params,
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT,
        )
        if r.status_code != 200:
            log.debug("Discogs HTTP %s for %s - %s", r.status_code, artist, title)
            return None
        data = r.json()
    except Exception as exc:
        log.debug("Discogs request failed for %s - %s: %s", artist, title, exc)
        return None

    results = data.get("results") or []
    if not results:
        return None

    norm_artist = _normalize(artist)
    norm_title = _normalize(title)

    # Score: prefer exact "Artist - Title" matches, deprioritize compilations
    exact_matches = []
    loose_matches = []
    for res in results:
        result_title = res.get("title") or ""
        norm_result = _normalize(result_title)
        is_exact = norm_artist in norm_result and norm_title in norm_result
        is_compilation = (
            "Compilation" in (res.get("format") or [])
            or "various" in result_title.lower()
        )
        year = str(res.get("year") or "").strip()
        if not _YEAR_RE.fullmatch(year):
            continue
        entry = (year, is_compilation, result_title)
        if is_exact:
            exact_matches.append(entry)
        else:
            loose_matches.append(entry)

    pool = exact_matches or loose_matches
    if not pool:
        return None

    # Prefer non-compilation, then earliest year
    pool.sort(key=lambda x: (x[1], int(x[0])))
    return pool[0][0]
