"""Factory for creating web search backend instances."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from djlib.metadata.web_search.base import SearchBackend

_log = logging.getLogger(__name__)

# Registry mapping backend name → (module_path, class_name)
# Lazy imports to avoid pulling in dependencies for unused backends.
_BACKENDS: Dict[str, tuple] = {
    "ddg": (
        "djlib.metadata.web_search.backend_ddg",
        "DuckDuckGoBackend",
    ),
    "searxng": (
        "djlib.metadata.web_search.backend_searxng",
        "SearXNGBackend",
    ),
    "brave": (
        "djlib.metadata.web_search.backend_brave",
        "BraveSearchBackend",
    ),
    "serper": (
        "djlib.metadata.web_search.backend_serper",
        "SerperBackend",
    ),
}


def list_backends() -> List[str]:
    """Return list of available backend names."""
    return list(_BACKENDS.keys())


def create_searcher(
    name: str = "ddg",
    **kwargs: Any,
) -> SearchBackend:
    """Create a search backend by name.

    Args:
        name: Backend name ("ddg", "searxng", "brave", "serper").
        **kwargs: Passed to backend constructor (api_key, delay, etc.).

    Returns:
        Configured SearchBackend instance.

    Raises:
        ValueError: Unknown backend name.
        ImportError: Backend dependencies not installed.
    """
    if name not in _BACKENDS:
        available = ", ".join(_BACKENDS.keys())
        raise ValueError(
            f"Unknown search backend '{name}'. Available: {available}"
        )

    module_path, class_name = _BACKENDS[name]

    import importlib
    module = importlib.import_module(module_path)
    backend_cls = getattr(module, class_name)

    return backend_cls(**kwargs)
