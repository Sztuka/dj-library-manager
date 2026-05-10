"""Gig preparation helpers — Phase 2, slice 1: dry-run only.

parse_m3u      — extract file paths from an M3U/M3U8 playlist
resolve_tracks — map filesystem paths → library.csv rows by old_full_path
validate_gig_prep — collect all pre-flight errors before any writes happen
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


# ── M3U parser ───────────────────────────────────────────────────────────────


def parse_m3u(path: Path) -> List[str]:
    """Return file paths found in an M3U or M3U8 playlist.

    Skips blank lines, #EXTM3U headers, and #EXTINF metadata lines.
    Handles both absolute and relative paths (relative resolved against
    the playlist's parent directory).
    """
    playlist_dir = path.parent
    paths: List[str] = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if os.path.isabs(line):
                paths.append(line)
            else:
                paths.append(str((playlist_dir / line).resolve()))
    return paths


# ── Track resolver ────────────────────────────────────────────────────────────


@dataclass
class ResolveResult:
    playlist_path: str
    track_id: Optional[str]
    library_row: Optional[Dict[str, str]]
    match_type: str  # "exact" | "filename" | "not_found"


def resolve_tracks(
    paths: List[str],
    library_rows: List[Dict[str, str]],
) -> List[ResolveResult]:
    """Map playlist file paths to library.csv rows.

    Match strategy (in order):
    1. Exact match on old_full_path
    2. Filename-only match (basename) — fallback for path drift
    """
    by_full: Dict[str, Dict[str, str]] = {}
    by_name: Dict[str, List[Dict[str, str]]] = {}
    for row in library_rows:
        fp = row.get("old_full_path", "")
        if fp:
            by_full[fp] = row
        name = Path(fp).name if fp else ""
        if name:
            by_name.setdefault(name, []).append(row)

    results: List[ResolveResult] = []
    for p in paths:
        if p in by_full:
            row = by_full[p]
            results.append(ResolveResult(
                playlist_path=p,
                track_id=row.get("track_id") or None,
                library_row=row,
                match_type="exact",
            ))
        else:
            name = Path(p).name
            candidates = by_name.get(name, [])
            if len(candidates) == 1:
                row = candidates[0]
                results.append(ResolveResult(
                    playlist_path=p,
                    track_id=row.get("track_id") or None,
                    library_row=row,
                    match_type="filename",
                ))
            else:
                results.append(ResolveResult(
                    playlist_path=p,
                    track_id=None,
                    library_row=None,
                    match_type="not_found",
                ))
    return results


# ── Validator ─────────────────────────────────────────────────────────────────


@dataclass
class ValidationError:
    kind: str   # "NOT_IN_LIBRARY" | "ON_ANOTHER_GIG" | "FILE_MISSING"
    path: str
    detail: str = ""


def validate_gig_prep(
    gig_id: str,
    resolved: List[ResolveResult],
    check_files_exist: bool = True,
) -> List[ValidationError]:
    """Collect all pre-flight errors. Never raises — returns full list so the
    DJ sees every problem before fixing anything."""
    errors: List[ValidationError] = []
    for r in resolved:
        if r.match_type == "not_found" or r.track_id is None:
            errors.append(ValidationError(
                kind="NOT_IN_LIBRARY",
                path=r.playlist_path,
            ))
            continue

        live_loc = (r.library_row or {}).get("live_location", "") or ""
        if live_loc and live_loc != "nas" and live_loc != f"gig:{gig_id}":
            errors.append(ValidationError(
                kind="ON_ANOTHER_GIG",
                path=r.playlist_path,
                detail=live_loc,
            ))

        if check_files_exist and not Path(r.playlist_path).exists():
            errors.append(ValidationError(
                kind="FILE_MISSING",
                path=r.playlist_path,
            ))

    return errors


# ── Size estimator ────────────────────────────────────────────────────────────


def estimate_total_bytes(resolved: List[ResolveResult]) -> int:
    """Sum file sizes for resolved tracks that exist on disk."""
    total = 0
    for r in resolved:
        if r.track_id and Path(r.playlist_path).exists():
            try:
                total += Path(r.playlist_path).stat().st_size
            except OSError:
                pass
    return total


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n //= 1024
    return f"{n:.0f} TB"
