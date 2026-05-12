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
    filename: str = "",
) -> List[Dict[str, str]]:
    """Build ranked search queries for a track.

    Returns list of {query, site, purpose} dicts, ordered by expected quality.

    Strategy ("targeted sites"):
      - Q1: ``site:beatport.com`` — gold standard for EDM subgenres.
      - Q2: ``site:traxsource.com`` — best for house subgenres (Afro, Soulful, etc.).
      - Q3: ``site:discogs.com`` — universal, has Style field (Deep House, Tech House).
      - Q4: ``site:last.fm`` — non-EDM tag cloud from user consensus.
      - Q5: ``site:soundcloud.com`` — niche remixes, bootlegs, free DLs. Uses filename.
      - Q6 (remixes only): Broad remixer identity query.

    Targeted ``site:`` queries return metadata pages with real genre labels
    instead of SEO articles ("what genre is X?").  Each site has a different
    taxonomy strength, so multiple sources give the classifier diverse signals.
    """
    artist_clean = _normalize_query(artist)
    title_clean = _normalize_query(title)
    version_clean = _normalize_query(version)

    # Filename without extension for SoundCloud query
    filename_stem = ""
    if filename:
        filename_stem = re.sub(r"\.[a-zA-Z0-9]{2,5}$", "", filename).strip()

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
    # Base query: artist + title (+ version for Beatport which needs exact match)
    base_q = f"{artist_clean} {title_clean}".strip()
    base_q_full = base_q
    if version_clean:
        base_q_full = f"{base_q} {version_clean}"

    # === Q1: Beatport (EDM gold standard — best subgenre precision) ===
    if base_q:
        queries.append({
            "query": f"{base_q_full} site:beatport.com",
            "site": "beatport",
            "purpose": "EDM subgenre from Beatport catalog",
        })

    # === Q2: Traxsource (house subgenres — Afro, Soulful, Jackin, Deep) ===
    if base_q:
        queries.append({
            "query": f"{base_q_full} site:traxsource.com",
            "site": "traxsource",
            "purpose": "House subgenre from Traxsource catalog",
        })

    # === Q3: Discogs (universal — Style field: Electronic > House > Deep House) ===
    if base_q:
        queries.append({
            "query": f"{base_q} site:discogs.com",
            "site": "discogs",
            "purpose": "Genre/Style from Discogs release page",
        })

    # === Q4: Last.fm (non-EDM strength — user tag cloud consensus) ===
    if base_q:
        queries.append({
            "query": f"{base_q} site:last.fm",
            "site": "last.fm",
            "purpose": "User tag cloud (strong for non-EDM: pop, R&B, hip-hop)",
        })

    # === Q5: Hypeddit (free DJ edits/bootlegs — genre in hidden form field) ===
    # Hypeddit is strong for unofficial edits not on Beatport/Traxsource.
    # The scraper extracts genre from <input name="genre"> which is reliable.
    if base_q:
        queries.append({
            "query": f"{base_q_full} site:hypeddit.com",
            "site": "hypeddit",
            "purpose": "Hypeddit DJ edit page (genre from hidden form field)",
        })

    # === Q6: SoundCloud (niche remixes, bootlegs, free DLs — use filename) ===
    sc_q = filename_stem or base_q_full
    if sc_q:
        queries.append({
            "query": f"{sc_q} site:soundcloud.com",
            "site": "soundcloud",
            "purpose": "SoundCloud track page (niche remixes, bootlegs)",
        })

    # === Q7 (remixes only): Remixer identity — broad query ===
    # Who is this producer?  Finds Wikipedia, RA, DJMag profiles, interviews.
    # Broad query (no site:) because remixer info can be anywhere.
    if is_remix and remixer:
        queries.append({
            "query": f"{remixer} producer music style",
            "site": "remixer",
            "purpose": f"Remixer identity: {remixer}'s music style",
        })

    # === Edge case: only filename, no parsed artist/title ===
    if not queries and filename_stem:
        queries.append({
            "query": filename_stem,
            "site": "generic",
            "purpose": "Fallback: raw filename search",
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
        "bandcamp", "rateyourmusic", "whosampled",
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
    filename: str = "",
    max_queries: int = 6,
    max_results_per_query: int = 2,
    stop_after_beatport: bool = False,
) -> TrackSearchResults:
    """Search for a track's genre info using the given backend.

    Executes targeted site queries (v2 strategy):
      1. ``site:beatport.com`` — EDM gold standard.
      2. ``site:traxsource.com`` — house subgenres.
      3. ``site:discogs.com`` — universal Style field.
      4. ``site:last.fm`` — non-EDM user tags.
      5. ``site:soundcloud.com`` — niche remixes/bootlegs (uses filename).
      6. Remixer identity query (remixes only) — broad.

    Args:
        backend: Search backend instance.
        artist: Track artist.
        title: Track title.
        version: Version/remix info.
        filename: Original filename (used for SoundCloud query).
        max_queries: Maximum number of search queries to execute.
        max_results_per_query: Max results per individual query.
        stop_after_beatport: If True and Beatport returns results, skip
            remaining queries.

    Returns:
        TrackSearchResults with deduplicated results.
    """
    t0 = time.time()
    queries = _build_search_queries(artist, title, version, filename=filename)
    all_results: List[SearchResult] = []
    seen_urls: set = set()  # URL dedup across queries
    queries_made = 0

    # Detect remix for early-exit logic
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

        # Tag results with detected source from URL.
        # For targeted site: queries, _detect_source will match the site.
        # For broad (remixer) queries, detect from URL or fallback to "web".
        for r in results:
            r.source = _detect_source(r.url) or "web"

        # Deduplicate by URL across all queries
        for r in results:
            url_key = r.url.rstrip("/").lower()
            if url_key not in seen_urls:
                seen_urls.add(url_key)
                all_results.append(r)

        # Early exit (disabled by default in v2 — we want all sites)
        if stop_after_beatport and site == "beatport" and results and not is_remix:
            if _is_beatport_specific_match(results, title):
                _log.debug(
                    "Beatport: specific track match (%d results), stopping early",
                    len(results),
                )
                break

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
