"""Shared genre normalization utilities.

Single source of truth for normalizing genre strings across all genre modules.
Both genre_canonical.py and genre_resolver.py import from here to ensure
consistent matching behavior.
"""

from __future__ import annotations

import re


def normalize_genre(text: str) -> str:
    """Normalize a genre string for matching.

    Rules:
    - Lowercase
    - ALL non-alphanumeric characters (including &, /, ., etc.) replaced with spaces
    - Collapsed whitespace, trimmed

    This is the canonical normalization shared by genre_canonical.py and
    genre_resolver.py.  Both modules MUST use this function (or import it)
    to ensure the same synonym matches identically in the taxonomy lookup
    (genre_canonical) and the weighted scorer (genre_resolver).

    Examples:
        >>> normalize_genre("Drum & Bass")
        'drum bass'
        >>> normalize_genre("Nu Disco / Disco")
        'nu disco disco'
        >>> normalize_genre("  Tech-House ")
        'tech house'
        >>> normalize_genre("d'n'b")
        'd n b'
    """
    cleaned = re.sub(r"[^a-z0-9\s]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()
