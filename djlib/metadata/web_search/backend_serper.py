"""Serper.dev backend (Google Search results via API).

Pricing: $0.001/request, 2500 free credits on signup.
Requires API key from https://serper.dev/

Set key via:
    export DJLIB_SERPER_API_KEY=...
    or add serper_api_key to config.local.yml
"""
from __future__ import annotations

import logging
import os
from typing import List

import requests

from djlib.metadata.web_search.base import SearchBackend, SearchResult

_log = logging.getLogger(__name__)


class SerperBackend(SearchBackend):
    """Serper.dev Google Search API backend.

    Pros:
        - Real Google Search results (highest quality)
        - Very cheap ($0.001/request = $1/1000 searches)
        - 2500 free credits on signup
        - Fast (~150-300ms), reliable API
        - Rich snippets, knowledge panels

    Cons:
        - Requires API key (free signup, credit card for paid)
        - Not free long-term (but very cheap)
    """

    name = "serper"
    requires_api_key = True

    def __init__(
        self,
        api_key: str = "",
        delay: float = 0.3,
        max_results_per_query: int = 3,
        timeout: float = 10.0,
    ) -> None:
        super().__init__(delay=delay, max_results_per_query=max_results_per_query, timeout=timeout)
        self.api_key = api_key or os.getenv("DJLIB_SERPER_API_KEY", "").strip()
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
                return str(d.get("serper_api_key", "") or "").strip()
        except Exception:
            pass
        return ""

    def _search(self, query: str, max_results: int) -> List[SearchResult]:
        if not self.api_key:
            _log.warning("Serper.dev API key not configured")
            return []

        resp = requests.post(
            "https://google.serper.dev/search",
            json={
                "q": query,
                "num": max_results,
                "gl": "us",
                "hl": "en",
            },
            headers={
                "X-API-KEY": self.api_key,
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []

        # Organic results
        for item in data.get("organic", [])[:max_results]:
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    snippet=item.get("snippet", ""),
                )
            )

        # Knowledge graph (bonus — often has genre info directly)
        kg = data.get("knowledgeGraph", {})
        if kg and kg.get("description"):
            results.append(
                SearchResult(
                    title=kg.get("title", "Knowledge Graph"),
                    url=kg.get("descriptionLink", ""),
                    snippet=kg.get("description", ""),
                    source="knowledge_graph",
                )
            )

        return results

    def is_available(self) -> bool:
        return bool(self.api_key)
