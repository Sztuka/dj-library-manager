"""DuckDuckGo search backend (free, no API key required).

Uses the ``ddgs`` package which scrapes DDG's public interface.
No official API — may break if DDG changes their frontend.

Install: pip install ddgs
"""
from __future__ import annotations

import logging
from typing import List

from djlib.metadata.web_search.base import SearchBackend, SearchResult

_log = logging.getLogger(__name__)


class DuckDuckGoBackend(SearchBackend):
    """DuckDuckGo web search via ddgs library.

    Pros:
        - Free, no API key, no account needed
        - Good snippet quality
        - Supports site: operator

    Cons:
        - Unofficial scraping — can break on DDG frontend changes
        - Rate limits unclear (~20-30 req/min in practice)
        - No SLA or reliability guarantee
    """

    name = "ddg"
    requires_api_key = False

    def __init__(
        self,
        delay: float = 1.0,
        max_results_per_query: int = 3,
        timeout: float = 10.0,
    ) -> None:
        super().__init__(delay=delay, max_results_per_query=max_results_per_query, timeout=timeout)

    def _search(self, query: str, max_results: int) -> List[SearchResult]:
        try:
            from ddgs import DDGS
        except ImportError:
            _log.error(
                "ddgs package not installed. Run: pip install ddgs"
            )
            return []

        results = DDGS().text(query, max_results=max_results)

        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("href", ""),
                snippet=r.get("body", ""),
            )
            for r in results
        ]

    def is_available(self) -> bool:
        try:
            from ddgs import DDGS  # noqa: F401
            return True
        except ImportError:
            return False
