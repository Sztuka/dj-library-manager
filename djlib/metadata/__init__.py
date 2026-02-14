from __future__ import annotations
import os
from typing import Optional

# Global HTTP cache for external APIs (MB, Last.fm endpoints that use requests)

def _install_requests_cache() -> None:
    """Install HTTP cache for external APIs (Last.fm, SoundCloud).
    
    Uses filesystem backend instead of default SQLite for thread-safety.
    This allows parallel API calls in genre_resolver without SQLite locking issues.
    """
    try:
        import requests_cache
        cache_name = os.getenv("DJLIB_HTTP_CACHE_NAME", "djlib_http_cache")
        expire_days = int(os.getenv("DJLIB_HTTP_CACHE_TTL_DAYS", "14"))
        requests_cache.install_cache(
            cache_name=cache_name,
            backend="filesystem",
            use_cache_dir=True,  # Uses ~/.cache/djlib_http_cache on macOS/Linux
            expire_after=expire_days * 24 * 3600,
            allowable_codes=[200],  # Never cache error responses (403, 404, 500, 504…)
        )
    except Exception:
        # cache optional
        pass

_install_requests_cache()
