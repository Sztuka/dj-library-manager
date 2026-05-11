"""Web search abstraction for genre enrichment via SearXNG.

Usage:
    from djlib.metadata.web_search import create_searcher, search_track_genre

    searcher = create_searcher()
    snippets = search_track_genre(searcher, artist="deadmau5", title="Strobe")
"""
from __future__ import annotations

from djlib.metadata.web_search.base import (
    SearchBackend,
    SearchResult,
    TrackSearchResults,
    search_track_genre,
)
from djlib.metadata.web_search.factory import create_searcher, list_backends

__all__ = [
    "SearchBackend",
    "SearchResult",
    "TrackSearchResults",
    "create_searcher",
    "list_backends",
    "search_track_genre",
]
