"""Version clustering for the Review UI.

Groups tracks that appear to be different versions of the same song
(Original Mix, Extended Mix, Radio Edit, etc.) using normalized artist
+ base-title slug matching.

Two tracks are in the same version group when:
  - artist slug matches (feat./& collab stripped, Unicode normalised)
  - base title slug matches (parenthetical suffixes stripped)

Intentionally conservative: covers (different artist, same title) land
in separate groups because their artist slugs differ.
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Optional

from djlib.near_dup import _base_title_slug, _artist_slug


def version_group_id(artist: str, title: str) -> str:
    """16-char hex key that groups all versions of the same song together."""
    a = _artist_slug(artist or "")
    t = _base_title_slug(title or "")
    if not a and not t:
        return ""
    raw = f"{a}|{t}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def extract_version_info(title: str) -> str:
    """Return the bracketed version/remix suffix from a title, or empty string.

    "Blue Monday (Extended Mix)" → "Extended Mix"
    "Blue Monday"                → ""
    """
    m = re.search(r"[\(\[](.*?)[\)\]]", title)
    return m.group(1).strip() if m else ""


def group_versions(
    rows: List[Dict],
    min_group_size: int = 2,
) -> Dict[str, List[Dict]]:
    """Return {group_id: [row, ...]} for groups with ≥ min_group_size tracks.

    Deduplicates by file_path before grouping: if the same physical file
    appears in both unsorted and library (double-scan / failed apply),
    the library row wins, avoiding false-positive version pairs.

    Each row must have a ``_source`` key (``"library"`` or ``"unsorted"``)
    set by the caller so the dedup preference can be applied.
    """
    # Deduplicate by file_path — library source wins over unsorted.
    seen_by_path: Dict[str, Dict] = {}
    no_path: List[Dict] = []
    for row in rows:
        fp = (row.get("file_path") or "").strip()
        if not fp:
            no_path.append(row)
            continue
        existing = seen_by_path.get(fp)
        if existing is None:
            seen_by_path[fp] = row
        elif row.get("_source") == "library":
            seen_by_path[fp] = row

    deduped = list(seen_by_path.values()) + no_path

    # Group by version_group_id
    groups: Dict[str, List[Dict]] = {}
    for row in deduped:
        artist = row.get("artist") or row.get("tag_artist_original") or ""
        title = row.get("title") or row.get("tag_title_original") or ""
        gid = version_group_id(artist, title)
        if not gid:
            continue
        groups.setdefault(gid, []).append(row)

    return {gid: members for gid, members in groups.items() if len(members) >= min_group_size}
