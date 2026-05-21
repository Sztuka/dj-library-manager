"""Collaboration format normalizer.

Normalizes collaboration separators in artist strings:
  "Calvin Harris ft. Dua Lipa"    → "Calvin Harris feat. Dua Lipa"
  "Calvin Harris x Dua Lipa"      → "Calvin Harris feat. Dua Lipa"
  "Calvin Harris featuring Dua Lipa" → "Calvin Harris feat. Dua Lipa"
  "Calvin Harris & Dua Lipa"      → "Calvin Harris feat. Dua Lipa"  (if not a band)
  "Bill Haley & His Comets"       → unchanged  (known band name)

`&` disambiguation:
  1. Check `collab_overrides` in artist_aliases.yml first (manual override, instant).
  2. MusicBrainz artist search: if score >= 90, treat as band name (no change).
  3. Otherwise: treat as collaboration, normalize.

Results are protected from DJ-software overwrite via `artist_normalized: yes` flag.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# Canonical format written after normalization (can be overridden via config)
DEFAULT_CANONICAL_SEP = "feat."

# Patterns that unambiguously indicate a collaboration
_FEAT_RE = re.compile(
    r"\s+(feat\.|featuring|ft\.|ft(?=\s))\s+",
    re.IGNORECASE,
)

# Lowercase 'x' surrounded by spaces — separator, not part of a name.
# Case-sensitive: "Malcolm X Foundation" has uppercase X and must NOT match.
# "Martin Garrix x Tiësto" → collaboration
# "DJ X" → NOT matched (no trailing space after X within artist token)
_X_SEP_RE = re.compile(r"\s+x\s+")

# Ampersand with optional surrounding whitespace
_AMP_RE = re.compile(r"\s*&\s*")

# Collab separators used to strip trailing collab info when checking band names.
# "Bill Haley & His Comets feat. DJ Snake" → check "Bill Haley & His Comets" (strip feat. part)
_COLLAB_SPLIT_RE = re.compile(
    r"\s+(?:feat\.|featuring|ft\.|ft(?=\s)|x)\s+",
    re.IGNORECASE,
)

# MusicBrainz throttle: 1 req/sec
_MB_LAST_REQUEST_TS: float = 0.0
_MB_THROTTLE_SECS: float = 1.1


def _mb_search_artist(name: str) -> int:
    """Return MusicBrainz artist search score (0-100) for exact name match."""
    global _MB_LAST_REQUEST_TS
    elapsed = time.monotonic() - _MB_LAST_REQUEST_TS
    if elapsed < _MB_THROTTLE_SECS:
        time.sleep(_MB_THROTTLE_SECS - elapsed)

    try:
        import requests
        try:
            import requests_cache
            session = requests_cache.CachedSession(
                "djlib_http_cache",
                expire_after=86400 * 30,  # 30 days for MB artist lookups
            )
        except ImportError:
            session = requests.Session()

        resp = session.get(
            "https://musicbrainz.org/ws/2/artist/",
            params={"query": f'artist:"{name}"', "fmt": "json", "limit": 1},
            headers={"User-Agent": "djlib/1.0 (peter@sztuckiszewcow.com)"},
            timeout=10,
        )
        _MB_LAST_REQUEST_TS = time.monotonic()
        if resp.status_code != 200:
            log.warning("MB artist search HTTP %d for %r", resp.status_code, name)
            return 0
        artists = resp.json().get("artists", [])
        if not artists:
            return 0
        return int(artists[0].get("score", 0))
    except Exception as exc:
        log.warning("MB artist search failed for %r: %s", name, exc)
        return 0


def _is_band_name(name: str, overrides: Dict[str, str], use_mb: bool = True) -> bool:
    """Return True if name is a known band (should not be split/normalized).

    Checks collab_overrides first (instant), then MusicBrainz (network).
    """
    key = name.strip()
    override = overrides.get(key)
    if override == "band":
        return True
    if override == "collab":
        return False
    if not use_mb:
        return False
    score = _mb_search_artist(key)
    return score >= 90


def normalize_collab_artist(
    raw: str,
    overrides: Optional[Dict[str, str]] = None,
    canonical_sep: str = DEFAULT_CANONICAL_SEP,
    use_mb: bool = True,
) -> Tuple[str, bool]:
    """Return (normalized, changed) for a single artist string.

    Normalizes feat./ft./featuring/x separators to canonical_sep.
    For '&': checks overrides/MB before normalizing.
    Does nothing to '& the X' style band names.
    """
    if overrides is None:
        overrides = {}

    result = raw

    # 1. Unambiguous: feat./ft./featuring → canonical_sep
    result = _FEAT_RE.sub(f" {canonical_sep} ", result)

    # 2. Unambiguous: lowercase 'x' separator → canonical_sep
    result = _X_SEP_RE.sub(f" {canonical_sep} ", result)

    # 3. Ambiguous '&' — check band name before normalizing.
    # Only take the immediate segment after '&' (before any further collab separator)
    # so "Bill Haley & His Comets feat. DJ Snake" → candidate "Bill Haley & His Comets"
    # (not "Bill Haley & His Comets feat. DJ Snake" which wouldn't match the override).
    def _replace_amp(m: re.Match) -> str:
        before = result[: m.start()].strip()
        after_full = result[m.end() :].strip()
        after_segment = _COLLAB_SPLIT_RE.split(after_full, maxsplit=1)[0].strip()
        candidate = f"{before} & {after_segment}"
        if _is_band_name(candidate, overrides, use_mb=use_mb):
            return m.group(0)  # keep original
        return f" {canonical_sep} "

    if "&" in result:
        result = _AMP_RE.sub(_replace_amp, result)

    # Collapse multiple spaces
    result = re.sub(r"  +", " ", result).strip()
    return result, result != raw


def collect_normalization_candidates(
    rows: List[Dict],
    overrides: Optional[Dict[str, str]] = None,
    canonical_sep: str = DEFAULT_CANONICAL_SEP,
    use_mb: bool = True,
) -> List[Dict]:
    """Return list of {row, original, normalized} for rows that would change.

    Deduplicates by artist string so each unique value is MB-queried once.
    """
    if overrides is None:
        overrides = {}

    seen: Dict[str, str] = {}  # original → normalized
    results = []

    for row in rows:
        raw = (row.get("artist") or "").strip()
        if not raw:
            continue
        if raw in seen:
            normalized = seen[raw]
        else:
            normalized, _ = normalize_collab_artist(
                raw, overrides=overrides, canonical_sep=canonical_sep, use_mb=use_mb
            )
            seen[raw] = normalized

        if normalized != raw:
            results.append({"row": row, "original": raw, "normalized": normalized})

    return results
