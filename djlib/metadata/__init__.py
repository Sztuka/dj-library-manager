from __future__ import annotations
import os
from typing import Optional

# Global HTTP cache for external APIs (MB, Last.fm endpoints that use requests)

def _install_requests_cache() -> None:
    try:
        import requests_cache
        cache_name = os.getenv("DJLIB_HTTP_CACHE_NAME", "djlib_http_cache")
        expire_days = int(os.getenv("DJLIB_HTTP_CACHE_TTL_DAYS", "14"))
        requests_cache.install_cache(cache_name=cache_name, expire_after=expire_days * 24 * 3600)
    except Exception:
        # cache optional
        pass

_install_requests_cache()
