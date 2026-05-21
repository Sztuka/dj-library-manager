"""Near-duplicate detection for unsorted tracks.

Two tracks are considered near-duplicates when they share the same base title
slug (title stripped of remix/version suffixes, normalized to lowercase alnum)
AND all available signals agree within tolerance:

  - key_camelot: exact match (if both present)
  - bpm:         ±1 BPM (if both present)
  - duration_seconds: ±5 s (if both present)

The check is intentionally conservative: missing signals are treated as
"inconclusive" (not a mismatch), so a pair where one row has no BPM still
counts as a near-duplicate if the other signals match.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional

_PAREN_RE = re.compile(r"[\(\[][^\)\]]*[\)\]]")


def _base_title_slug(title: str) -> str:
    """Strip parenthetical/bracketed version suffixes, normalize to lowercase alnum."""
    s = _PAREN_RE.sub(" ", title)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _bpm_int(val: str) -> Optional[int]:
    try:
        f = float(val)
        return round(f) if f > 0 else None
    except (ValueError, TypeError):
        return None


def _dur_int(val: str) -> Optional[int]:
    try:
        f = float(val)
        return round(f) if f > 0 else None
    except (ValueError, TypeError):
        return None


def _artist_slug(artist: str) -> str:
    """Normalize artist for comparison: lowercase alnum, strip feat./& suffixes."""
    # x(?=\s) instead of x\b or x(?=\s|$): requires whitespace after x,
    # so trailing "DJ X" is preserved but "Artist x Artist2" is stripped.
    s = re.sub(r"\s+(feat\.?|ft\.?|&|x(?=\s)|vs\.?|and\b).*", "", artist, flags=re.IGNORECASE)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _rows_match(a: Dict, b: Dict) -> bool:
    """True when two rows are near-duplicates (same slug already established by caller)."""
    # Artist: if both present and clearly different, not a dup (cover guard).
    # Missing artist = inconclusive, same as missing BPM/duration.
    art_a = _artist_slug(a.get("artist") or a.get("tag_artist_original") or "")
    art_b = _artist_slug(b.get("artist") or b.get("tag_artist_original") or "")
    if art_a and art_b and art_a != art_b:
        return False

    # Key: exact Camelot match if both present
    key_a = (a.get("key_camelot") or a.get("key") or "").strip().upper()
    key_b = (b.get("key_camelot") or b.get("key") or "").strip().upper()
    if key_a and key_b and key_a != key_b:
        return False

    # BPM: ±1
    bpm_a = _bpm_int(a.get("bpm") or "")
    bpm_b = _bpm_int(b.get("bpm") or "")
    if bpm_a is not None and bpm_b is not None and abs(bpm_a - bpm_b) > 1:
        return False

    # Duration: ±5 s
    dur_a = _dur_int(a.get("duration_seconds") or "")
    dur_b = _dur_int(b.get("duration_seconds") or "")
    if dur_a is not None and dur_b is not None and abs(dur_a - dur_b) > 5:
        return False

    return True


def flag_near_dups(
    staging_rows: List[Dict],
    library_rows: Optional[List[Dict]] = None,
) -> int:
    """Set ``near_duplicate_of`` on staging rows that match another track.

    Checks staging rows against each other AND against ``library_rows`` (if
    provided). Library rows are never modified.

    Resets ``near_duplicate_of`` to ``""`` on all staging rows before
    recomputing, so re-running after metadata edits gives a fresh result.

    Returns the number of staging rows flagged.
    """
    # Reset so re-scans don't carry stale flags
    for row in staging_rows:
        row["near_duplicate_of"] = ""

    all_candidate_rows: List[Dict] = list(staging_rows)
    if library_rows:
        all_candidate_rows.extend(library_rows)

    staging_ids: set = {r.get("track_id") for r in staging_rows if r.get("track_id")}

    # Build slug → rows index
    by_slug: Dict[str, List[Dict]] = {}
    for row in all_candidate_rows:
        # Prefer enriched title; fall back to raw tag
        title = row.get("title") or row.get("tag_title_original") or ""
        slug = _base_title_slug(title)
        if slug and len(slug) >= 3:  # skip slugs that are too short (e.g. "ok", "hi")
            by_slug.setdefault(slug, []).append(row)

    flagged = 0
    for group in by_slug.values():
        if len(group) < 2:
            continue
        for row_a in group:
            tid_a = row_a.get("track_id")
            if tid_a not in staging_ids:
                continue  # only flag staging rows
            if row_a.get("near_duplicate_of"):
                continue  # already flagged in this pass
            for row_b in group:
                if row_b is row_a:
                    continue
                tid_b = row_b.get("track_id") or ""
                if _rows_match(row_a, row_b):
                    row_a["near_duplicate_of"] = tid_b
                    flagged += 1
                    break

    return flagged
