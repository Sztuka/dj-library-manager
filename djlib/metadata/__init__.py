from __future__ import annotations
import os
from typing import Optional

# Global HTTP cache for external APIs (MB, Last.fm endpoints that use requests)

def _install_requests_cache() -> None:
    """Install HTTP cache for external APIs (Last.fm, SoundCloud, MusicBrainz).

    Uses in-memory backend so it is safe to share across Flask request threads
    and the ThreadPoolExecutor batch-enrich workers simultaneously.  The
    filesystem backend internally uses a SQLiteDict for redirect storage; that
    SQLiteDict shares one sqlite3.Connection across threads without proper
    locking, which causes SIGSEGV on Python 3.13 under concurrent load.

    Trade-off: cache is lost on server restart, but all enrichment results are
    persisted in the pending-suggestions sidecar (LOGS/pending-suggestions.json)
    and in library.csv, so the loss is purely of raw HTTP response caching.
    """
    try:
        import requests_cache
        expire_days = int(os.getenv("DJLIB_HTTP_CACHE_TTL_DAYS", "14"))
        requests_cache.install_cache(
            cache_name="djlib_http_cache",
            backend="memory",
            expire_after=expire_days * 24 * 3600,
            allowable_codes=[200],  # Never cache error responses (403, 404, 500, 504…)
        )
    except Exception:
        # cache optional
        pass

_install_requests_cache()
