"""SearXNG search backend (self-hosted, privacy-respecting metasearch).

SearXNG aggregates results from Google, Bing, DuckDuckGo and others.
Requires a running SearXNG instance (Docker recommended).

Setup:
    docker compose -f docker/docker-compose.searxng.yml up -d
    # Instance runs at http://localhost:8888 by default
"""
from __future__ import annotations

import logging
import os
from typing import List

import requests

from djlib.metadata.web_search.base import SearchBackend, SearchResult

_log = logging.getLogger(__name__)

_DEFAULT_SEARXNG_URL = "http://localhost:8888"


class SearXNGBackend(SearchBackend):
    """SearXNG metasearch engine backend.

    Pros:
        - Free, self-hosted, no API key
        - Aggregates multiple search engines (Google, Bing, DDG, etc.)
        - Full control over search engines, result ranking, privacy
        - JSON API out of the box

    Cons:
        - Requires Docker (or manual install)
        - Depends on upstream engines — if Google blocks, quality degrades
        - Extra infrastructure to maintain
    """

    name = "searxng"
    requires_api_key = False
    requires_docker = True

    def __init__(
        self,
        base_url: str = "",
        delay: float = 1.0,
        max_results_per_query: int = 3,
        timeout: float = 10.0,
    ) -> None:
        super().__init__(delay=delay, max_results_per_query=max_results_per_query, timeout=timeout)
        self.base_url = (
            base_url
            or os.getenv("DJLIB_SEARXNG_URL", "").strip()
            or _DEFAULT_SEARXNG_URL
        ).rstrip("/")

    def _search(self, query: str, max_results: int) -> List[SearchResult]:
        resp = requests.get(
            f"{self.base_url}/search",
            params={
                "q": query,
                "format": "json",
                "categories": "general",
                "language": "en",
                "pageno": 1,
            },
            headers={"Accept": "application/json"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("results", [])[:max_results]:
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", ""),
                )
            )
        return results

    def is_available(self) -> bool:
        """Check if SearXNG instance is reachable."""
        try:
            resp = requests.get(
                f"{self.base_url}/healthz",
                timeout=3,
            )
            return resp.status_code == 200
        except Exception:
            return False
