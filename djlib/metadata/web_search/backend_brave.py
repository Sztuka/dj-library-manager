"""Brave Search API backend.

Free tier: 2000 requests/month. Paid: $0.003/request.
Requires API key from https://brave.com/search/api/

Set key via:
    export DJLIB_BRAVE_API_KEY=BSA...
    or add brave_api_key to config.local.yml
"""
from __future__ import annotations

import logging
import os
from typing import List

import requests

from djlib.metadata.web_search.base import SearchBackend, SearchResult

_log = logging.getLogger(__name__)


class BraveSearchBackend(SearchBackend):
    """Brave Search API backend.

    Pros:
        - 2000 free requests/month (enough for ~700 tracks with 3 queries each)
        - Real search index (not scraping), reliable API
        - Good snippet quality, fast responses (~200-400ms)
        - Supports site: operator

    Cons:
        - Requires API key (free signup)
        - 2000 req/month limit on free tier ($0.003/req beyond)
        - Results sometimes less comprehensive than Google
    """

    name = "brave"
    requires_api_key = True

    def __init__(
        self,
        api_key: str = "",
        delay: float = 0.5,
        max_results_per_query: int = 3,
        timeout: float = 10.0,
    ) -> None:
        super().__init__(delay=delay, max_results_per_query=max_results_per_query, timeout=timeout)
        self.api_key = api_key or os.getenv("DJLIB_BRAVE_API_KEY", "").strip()
        if not self.api_key:
            self.api_key = self._load_from_config()

    @staticmethod
    def _load_from_config() -> str:
        """Try to load API key from config files."""
        try:
            from djlib.config import _first_existing, _CANDIDATES, _read_yaml
            existing = _first_existing(_CANDIDATES)
            if existing:
                d = _read_yaml(existing)
                return str(d.get("brave_api_key", "") or "").strip()
        except Exception:
            pass
        return ""

    def _search(self, query: str, max_results: int) -> List[SearchResult]:
        if not self.api_key:
            _log.warning("Brave Search API key not configured")
            return []

        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={
                "q": query,
                "count": max_results,
                "text_decorations": "false",
            },
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": self.api_key,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("web", {}).get("results", [])[:max_results]:
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", ""),
                )
            )
        return results

    def is_available(self) -> bool:
        return bool(self.api_key)
