"""Factory for creating web search backend instances."""
from __future__ import annotations

import importlib
import logging
from typing import Any, Dict, List

from djlib.metadata.web_search.base import SearchBackend

_log = logging.getLogger(__name__)

_BACKENDS: Dict[str, tuple] = {
    "searxng": (
        "djlib.metadata.web_search.backend_searxng",
        "SearXNGBackend",
    ),
}


def list_backends() -> List[str]:
    """Return list of available backend names."""
    return list(_BACKENDS.keys())


def create_searcher(
    name: str = "searxng",
    **kwargs: Any,
) -> SearchBackend:
    """Create a search backend by name.

    Args:
        name: Backend name. Currently only "searxng" is supported.
        **kwargs: Passed to backend constructor.

    Returns:
        Configured SearchBackend instance.

    Raises:
        ValueError: Unknown backend name.
    """
    if name not in _BACKENDS:
        available = ", ".join(_BACKENDS.keys())
        raise ValueError(
            f"Unknown search backend '{name}'. Available: {available}"
        )

    module_path, class_name = _BACKENDS[name]
    module = importlib.import_module(module_path)
    backend_cls = getattr(module, class_name)

    return backend_cls(**kwargs)
