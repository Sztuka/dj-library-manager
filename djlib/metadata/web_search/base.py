"""Base classes and shared logic for web search backends."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_log = logging.getLogger(__name__)

# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class SearchResult:
    """Single web search result."""

    title: str
    url: str
    snippet: str
    source: str = ""  # e.g. "beatport", "soundcloud", "discogs", "generic"

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class TrackSearchResults:
    """Aggregated search results for a track across multiple sites."""

    artist: str
    title: str
    version: str
    results: List[SearchResult] = field(default_factory=list)
    backend: str = ""
    search_time_ms: int = 0
    queries_made: int = 0

    def to_prompt_context(self, max_results: int = 6) -> str:
        """Format results as context text for LLM prompt injection."""
        if not self.results:
            return "(No web search results found)"

        lines = []
        for r in self.results[:max_results]:
            source_tag = f"[{r.source.upper()}]" if r.source else ""
            lines.append(f"{source_tag} {r.title}")
            lines.append(f"  URL: {r.url}")
            if r.snippet:
                # Truncate long snippets
                snip = r.snippet[:300].strip()
                lines.append(f"  {snip}")
            lines.append("")

        return "\n".join(lines).strip()


# ── Abstract backend ─────────────────────────────────────────────────────────


class SearchBackend(ABC):
    """Abstract base class for web search backends.

    Subclasses implement ``_search()`` which performs the actual HTTP call.
    The base class provides rate limiting, error handling, and query building.
    """

    name: str = "base"
    requires_api_key: bool = False
    requires_docker: bool = False

    def __init__(
        self,
        delay: float = 1.0,
        max_results_per_query: int = 3,
        timeout: float = 10.0,
    ) -> None:
        self.delay = delay
        self.max_results_per_query = max_results_per_query
        self.timeout = timeout
        self._last_request_time: float = 0.0

    def _throttle(self) -> None:
        """Enforce minimum delay between requests."""
        if self.delay <= 0:
            return
        elapsed = time.time() - self._last_request_time
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request_time = time.time()

    @abstractmethod
    def _search(self, query: str, max_results: int) -> List[SearchResult]:
        """Execute a single search query. Implemented by each backend."""
        ...

    def search(self, query: str, max_results: int = 3) -> List[SearchResult]:
        """Search with rate limiting and error handling."""
        self._throttle()
        try:
            results = self._search(query, max_results)
            return results
        except Exception as e:
            _log.warning("%s search failed for '%s': %s", self.name, query, e)
            return []

    def is_available(self) -> bool:
        """Check if this backend is ready to use (API key set, Docker running, etc.)."""
        return True


# ── Query building ───────────────────────────────────────────────────────────


def _normalize_query(text: str) -> str:
    """Normalize text for search queries: strip accents, remove noise.

    Handles apostrophes (Stayin' Alive → Stayin Alive), dots in abbreviations
    (P.I.M.P. → PIMP), and accented characters (Rosalía → Rosalia).
    """
    # NFD decompose, strip combining marks
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Strip apostrophes / curly quotes (Stayin' Alive, Movin' too fast)
    ascii_text = re.sub(r"[''ʼ`]", "", ascii_text)
    # Strip dots between single letters (P.I.M.P. → PIMP) but keep "feat."
    ascii_text = re.sub(r"(?<=[A-Za-z])\.(?=[A-Za-z]\.?)", "", ascii_text)
    # Remove common noise tokens
    noise = re.compile(
        r"\b(feat\.?|ft\.?|featuring|original mix|extended mix|radio edit)\b",
        re.IGNORECASE,
    )
    ascii_text = noise.sub("", ascii_text)
    # Remove stray/trailing dots (but not decimals like "3.5")
    ascii_text = re.sub(r"\.(?!\d)", " ", ascii_text)
    # Collapse whitespace
    return re.sub(r"\s+", " ", ascii_text).strip()


def _build_search_queries(
    artist: str,
    title: str,
    version: str = "",
) -> List[Dict[str, str]]:
    """Build ranked search queries for a track.

    Returns list of {query, site, purpose} dicts, ordered by expected quality.

    Strategy ("hybrid detective"):
      - Tier 1: ``site:beatport.com`` — gold standard for EDM subgenres.
      - Tier 2: Broad query with DJ keywords — lets the search engine
        organically surface DJCity, 1001Tracklists, Hypeddit, SoundCloud,
        Wikipedia, YouTube compilations, etc.  No ``site:`` restriction.
      - Tier 3 (remixes only): Remixer identity query — "who is this
        producer?"  Finds DJMag profiles, Wikipedia, interviews.

    This replaces the old per-site (SC/Discogs/generic) tiers which were
    noisier and required more API calls.  Empirical testing showed broad
    queries catch all relevant sources organically.
    """
    artist_clean = _normalize_query(artist)
    title_clean = _normalize_query(title)
    version_clean = _normalize_query(version)

    # Detect remix
    is_remix = bool(
        re.search(
            r"\b(remix|bootleg|rework|refix|flip|mashup|edit)\b",
            version,
            re.IGNORECASE,
        )
    )
    # Extract remixer name if available (e.g. "Vintage Culture Remix" → "Vintage Culture")
    remixer = ""
    if is_remix and version_clean:
        remixer = re.sub(
            r"\b(remix|bootleg|rework|refix|flip|mashup|edit|extended|club|dub|vip)\b",
            "",
            version_clean,
            flags=re.IGNORECASE,
        ).strip()

    queries = []

    # === Tier 1: Beatport (targeted — gold standard for EDM) ===
    if artist_clean and title_clean:
        bp_q = f"{artist_clean} {title_clean}"
        if version_clean:
            bp_q += f" {version_clean}"
        queries.append({
            "query": f"{bp_q} site:beatport.com",
            "site": "beatport",
            "purpose": "EDM genre from Beatport catalog",
        })

    # === Tier 2: Broad detective — DJ keywords, no site: restriction ===
    # Lets the search engine organically find DJCity, 1001Tracklists,
    # Hypeddit, SoundCloud, Wikipedia, YouTube compilations, Discogs, etc.
    if artist_clean and title_clean:
        detective_q = f"{artist_clean} {title_clean}"
        if version_clean:
            detective_q += f" {version_clean}"
        # Only add "remix" keyword for actual remixes — for originals it
        # pollutes results with unrelated remix versions (#6)
        keywords = "genre DJ" if not is_remix else "genre remix DJ"
        queries.append({
            "query": f"{detective_q} {keywords}",
            "site": "detective",
            "purpose": "Broad — DJCity, 1001TL, Hypeddit, Wikipedia, SC, YT",
        })

    # === Tier 3 (remixes only): Remixer identity research ===
    # "Who is this producer?" — finds DJMag profiles, Wikipedia, interviews
    # with genre/style mentions.  Critical for remixes where the remixer's
    # style determines the genre, not the original artist.
    if is_remix and remixer:
        queries.append({
            "query": f"{remixer} DJ producer genre style",
            "site": "remixer",
            "purpose": f"Remixer identity: {remixer}'s DJ style/genre",
        })

    # === Edge case: only filename, no parsed artist/title ===
    if not queries and (artist_clean or title_clean):
        fallback = artist_clean or title_clean
        queries.append({
            "query": f"{fallback} music genre",
            "site": "generic",
            "purpose": "Fallback genre search",
        })

    return queries


# ── High-level search function ───────────────────────────────────────────────


def _detect_source(url: str) -> str:
    """Detect the music source from a result URL."""
    url_lower = url.lower()
    for tag in (
        "beatport", "soundcloud", "discogs", "hypeddit", "mixcloud",
        "1001tracklists", "djcity", "traxsource", "junodownload",
        "wikipedia", "allmusic", "musicbrainz", "last.fm",
        "youtube", "spotify", "apple.music", "genius",
        "djmag", "residentadvisor", "ra.co",
    ):
        if tag in url_lower:
            return tag
    return "other"


def _is_beatport_specific_match(
    results: List[SearchResult],
    title: str,
) -> bool:
    """Check if Beatport results contain a specific track page (not just artist/chart pages).

    Generic pages (artist profile, charts, homepage) have no genre signal.
    Only a specific track/release page (with the track title in URL or result
    title) is worth stopping early for.

    Examples of GOOD (specific) results:
      - "Fisher - Losing It (Original Mix) [Catch & Release]"
      - URL: beatport.com/track/losing-it-original-mix/12345

    Examples of BAD (generic) results:
      - "Kendrick Lamar Music & Downloads on Beatport"
      - "Beatport | DJ & Electronic Dance Music, Tracks & Mixes"
      - URL: beatport.com/artist/kendrick-lamar/12345
    """
    if not results or not title:
        return False

    # Normalize title for fuzzy matching
    title_words = set(
        re.sub(r"[^\w\s]", "", title.lower()).split()
    )
    # Remove very short/common words
    title_words = {w for w in title_words if len(w) > 2}
    if not title_words:
        return False

    for r in results:
        # Check URLs for track-specific paths
        url_lower = r.url.lower()
        if "/track/" in url_lower or "/release/" in url_lower:
            return True

        # Check if result title contains most of the track title words
        result_title_lower = re.sub(r"[^\w\s]", "", r.title.lower())
        result_words = set(result_title_lower.split())
        overlap = title_words & result_words
        if len(overlap) >= len(title_words) * 0.6:
            # Also reject if it's clearly an artist page or generic page
            generic_patterns = (
                "music & downloads on beatport",
                "music download :: beatport",
                "beatport | dj &",
                "top 100",
                "best sellers",
            )
            if not any(p in r.title.lower() for p in generic_patterns):
                return True

    return False


def search_track_genre(
    backend: SearchBackend,
    artist: str = "",
    title: str = "",
    version: str = "",
    max_queries: int = 3,
    max_results_per_query: int = 3,
    stop_after_beatport: bool = True,
) -> TrackSearchResults:
    """Search for a track's genre info using the given backend.

    Executes ranked queries:
      1. ``site:beatport.com`` — targeted EDM gold standard.
      2. Broad "detective" query — catches DJCity, 1001Tracklists, Hypeddit,
         SoundCloud, Wikipedia, YouTube compilations organically.
      3. Remixer identity query (remixes only) — who is this producer?

    Stops early if Beatport returns results (configurable).

    Args:
        backend: Search backend instance.
        artist: Track artist.
        title: Track title.
        version: Version/remix info.
        max_queries: Maximum number of search queries to execute.
        max_results_per_query: Max results per individual query.
        stop_after_beatport: If True and Beatport returns results, skip
            remaining queries (saves time, Beatport is gold standard for EDM).

    Returns:
        TrackSearchResults with all collected snippets.
    """
    t0 = time.time()
    queries = _build_search_queries(artist, title, version)
    all_results: List[SearchResult] = []
    queries_made = 0

    # Detect remix for early-exit logic (#12)
    is_remix = bool(
        re.search(
            r"\b(remix|bootleg|rework|refix|flip|mashup|edit)\b",
            version,
            re.IGNORECASE,
        )
    )

    for q_info in queries[:max_queries]:
        query = q_info["query"]
        site = q_info["site"]

        _log.debug("Web search [%s]: %s", backend.name, query)
        results = backend.search(query, max_results=max_results_per_query)
        queries_made += 1

        # For targeted (site:beatport), tag with site name.
        # For broad queries, detect actual source from URL.
        # DJ-1 fix: fallback to "web" instead of tier name (detective/remixer)
        # so prompt shows [WEB] not [DETECTIVE] or [REMIXER].
        for r in results:
            if site == "beatport":
                r.source = "beatport"
            else:
                r.source = _detect_source(r.url) or "web"
        all_results.extend(results)

        # Early exit: only if Beatport returned a SPECIFIC track match
        # AND this is NOT a remix.  For remixes, always continue to Tier 3
        # (remixer identity) — Beatport may have the track under the wrong
        # genre and the remixer's style is the strongest signal (#12).
        if stop_after_beatport and site == "beatport" and results and not is_remix:
            if _is_beatport_specific_match(results, title):
                _log.debug(
                    "Beatport: specific track match (%d results), stopping early",
                    len(results),
                )
                break
            else:
                _log.debug(
                    "Beatport: only generic pages (%d results), continuing to detective",
                    len(results),
                )

    elapsed_ms = int((time.time() - t0) * 1000)

    return TrackSearchResults(
        artist=artist,
        title=title,
        version=version,
        results=all_results,
        backend=backend.name,
        search_time_ms=elapsed_ms,
        queries_made=queries_made,
    )
