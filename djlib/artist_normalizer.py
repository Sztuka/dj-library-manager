"""Artist name normalization — cluster, merge, write tags.

Workflow:
  1. collect_artists() gathers all unique artist atoms from library + unsorted CSVs
  2. cluster_artists() groups similar names using fuzzy matching + MusicBrainz IDs
  3. User picks a canonical name in the Artists tab of the Review UI
  4. write_artist_tags() writes the canonical name to audio file ID3/Vorbis tags
  5. The merge endpoint updates CSVs and artist_aliases.yml

Pending mechanism (crash-safety):
  Before writing any tags, a `pending` entry is written to artist_aliases.yml.
  Only after all tags succeed is the entry promoted to `canonical`.
  On startup, any stale `pending` entries are visible for manual inspection.
"""
from __future__ import annotations

import csv
import logging
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

log = logging.getLogger(__name__)

# Regex to split compound artist strings (feat., ft., &, x).
# "& the X" band-name patterns are handled by MusicBrainz lookup downstream;
# here we split all of them so "Calvin Harris & Dua Lipa" clusters as two atoms.
_COMPOUND_RE = re.compile(
    r"\s+(?:feat\.?|ft\.?|vs\.?|x)\s+|\s*&\s*",
    re.IGNORECASE,
)


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _normalize_key(name: str) -> str:
    return _nfc(name).lower().strip()


def _split_compound(name: str) -> List[str]:
    parts = _COMPOUND_RE.split(name)
    return [p.strip() for p in parts if p.strip()]


def load_aliases(path: Path) -> Dict:
    """Load artist_aliases.yml. Returns empty structure if absent."""
    if not path.exists():
        return {"canonical": {}, "dismissed": [], "pending": {}}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("canonical", {})
    data.setdefault("dismissed", [])
    data.setdefault("pending", {})
    return data


def save_aliases(path: Path, data: Dict) -> None:
    """Atomically write artist_aliases.yml."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".artist_aliases.", suffix=".yml.tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _cluster_fingerprint(members: List[str]) -> str:
    return "|".join(sorted(_normalize_key(m) for m in members))


def collect_artists(library_rows: List[Dict], unsorted_rows: List[Dict]) -> List[Tuple[str, str]]:
    """Return list of (artist_name, mb_artist_id) tuples, deduplicated by normalized name.

    One entry per unique normalized name. mb_artist_id is the first non-empty
    value seen for that name (empty string if never enriched).
    """
    seen: Dict[str, Tuple[str, str]] = {}
    for row in list(library_rows) + list(unsorted_rows):
        raw = (row.get("artist") or "").strip()
        if not raw:
            continue
        for atom in _split_compound(raw):
            key = _normalize_key(atom)
            if key and key not in seen:
                mb_id = (row.get("mb_artist_id") or "").strip()
                seen[key] = (atom, mb_id)
    return list(seen.values())


def cluster_artists(
    artists: List[Tuple[str, str]],
    aliases: Dict,
    threshold: int = 70,
    show_dismissed: bool = False,
) -> List[Dict]:
    """Group similar artist names into clusters.

    Returns list of cluster dicts:
      {members, canonical, confidence, method, track_count}

    Matching strategy (in priority order):
      1. Shared non-empty mb_artist_id → 100% confidence, method="mbz"
      2. rapidfuzz token_sort_ratio >= threshold → method="fuzzy"
    """
    try:
        from rapidfuzz import fuzz
    except ImportError:
        log.warning("rapidfuzz not installed — artist clustering disabled")
        return []

    dismissed_set = set(aliases.get("dismissed", []))
    canonical_map = aliases.get("canonical", {})

    # Group by mb_artist_id first (100% confidence)
    mb_clusters: Dict[str, List[str]] = {}
    no_mb: List[Tuple[str, str]] = []
    for name, mb_id in artists:
        if mb_id:
            mb_clusters.setdefault(mb_id, []).append(name)
        else:
            no_mb.append((name, mb_id))

    results: List[Dict] = []

    for mb_id, members in mb_clusters.items():
        if len(members) < 2:
            # Single member — still add to no_mb for fuzzy pass
            no_mb.append((members[0], mb_id))
            continue
        fp = _cluster_fingerprint(members)
        if not show_dismissed and fp in dismissed_set:
            continue
        canonical = _pick_canonical(members, canonical_map)
        results.append({
            "members": members,
            "canonical": canonical,
            "confidence": 100,
            "method": "mbz",
            "fingerprint": fp,
        })

    # Fuzzy clustering on remaining artists
    names_only = [name for name, _ in no_mb]
    if len(names_only) >= 2:
        # Union-find for single-linkage clustering
        parent = list(range(len(names_only)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            parent[find(i)] = find(j)

        for i, name_a in enumerate(names_only):
            for j in range(i + 1, len(names_only)):
                score = fuzz.token_sort_ratio(_normalize_key(name_a), _normalize_key(names_only[j]))
                if score >= threshold:
                    union(i, j)

        groups: Dict[int, List[int]] = {}
        for i in range(len(names_only)):
            root = find(i)
            groups.setdefault(root, []).append(i)

        for indices in groups.values():
            if len(indices) < 2:
                continue
            members = [names_only[i] for i in indices]
            fp = _cluster_fingerprint(members)
            if not show_dismissed and fp in dismissed_set:
                continue
            # Confidence = min pairwise score within cluster
            min_score = 100
            for a in range(len(members)):
                for b in range(a + 1, len(members)):
                    s = fuzz.token_sort_ratio(_normalize_key(members[a]), _normalize_key(members[b]))
                    min_score = min(min_score, s)
            canonical = _pick_canonical(members, canonical_map)
            results.append({
                "members": members,
                "canonical": canonical,
                "confidence": min_score,
                "method": "fuzzy",
                "fingerprint": fp,
            })

    # Sort: largest clusters first, then lowest confidence first
    results.sort(key=lambda c: (-len(c["members"]), c["confidence"]))
    return results


def _pick_canonical(members: List[str], canonical_map: Dict) -> Optional[str]:
    """Return existing canonical if any member is already mapped, else None."""
    normalized = {_normalize_key(k): v for k, v in canonical_map.items()}
    for m in members:
        v = normalized.get(_normalize_key(m))
        if v is not None:
            return v
    return None


def write_pending_entry(aliases_path: Path, fingerprint: str, canonical: str, variants: List[str]) -> None:
    """Write a pending merge entry before any tag writes begin."""
    data = load_aliases(aliases_path)
    data["pending"][fingerprint] = {"canonical": canonical, "variants": variants}
    save_aliases(aliases_path, data)


def promote_pending_to_canonical(aliases_path: Path, fingerprint: str, canonical: str, variants: List[str]) -> None:
    """After all tags succeed: move pending → canonical, remove pending entry."""
    data = load_aliases(aliases_path)
    for variant in variants:
        data["canonical"][_nfc(variant)] = _nfc(canonical)
    data["pending"].pop(fingerprint, None)
    save_aliases(aliases_path, data)


def dismiss_cluster(aliases_path: Path, members: List[str]) -> None:
    """Soft-delete a cluster by adding its fingerprint to dismissed list."""
    data = load_aliases(aliases_path)
    fp = _cluster_fingerprint(members)
    if fp not in data["dismissed"]:
        data["dismissed"].append(fp)
    save_aliases(aliases_path, data)


def write_artist_tags(file_paths: List[str], canonical: str) -> List[str]:
    """Write canonical artist name to audio file tags via mutagen.

    Returns list of paths that failed (empty = all succeeded).
    """
    from djlib.tags import write_tags  # local import to avoid circular deps

    failed = []
    for path in file_paths:
        try:
            write_tags(Path(path), {"artist": canonical})
        except Exception as exc:
            log.warning("Failed to write artist tag to %s: %s", path, exc)
            failed.append(path)
    return failed


def write_audit_log(logs_dir: Path, canonical: str, variants: List[str], tracks_affected: int, method: str, confidence: int) -> None:
    """Append one row to LOGS/artist-merges-YYYY-MM-DD.csv."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = logs_dir / f"artist-merges-{today}.csv"
    fieldnames = ["timestamp", "canonical", "variants", "tracks_affected", "method", "confidence"]
    write_header = not log_path.exists()
    with log_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "canonical": canonical,
            "variants": "|".join(variants),
            "tracks_affected": tracks_affected,
            "method": method,
            "confidence": confidence,
        })
