"""
Utilities for exporting Essentia features + user labels into a training CSV.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

from djlib.audio.cache import compute_audio_id, get_analysis
from djlib.config import CSV_PATH
from djlib.csvdb import load_records


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPORT_PATH = REPO_ROOT / "data" / "training_dataset_full.csv"


def _preferred_path(row: Dict[str, str]) -> Path | None:
    """Return a filesystem path for the given library row, preferring final_path."""
    for key in ("final_path", "file_path"):
        raw = (row.get(key) or "").strip()
        if raw:
            p = Path(raw)
            if p.exists():
                return p
    return None


def _resolve_audio_id(row: Dict[str, str]) -> Tuple[str | None, bool]:
    """Return (audio_id, computed_now)."""
    audio_id = (row.get("file_hash") or "").strip()
    if audio_id:
        return audio_id, False
    path = _preferred_path(row)
    if not path:
        return None, False
    try:
        return compute_audio_id(path), True
    except Exception:
        return None, False


def _flatten_analysis(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Remove nested blobs and keep scalar Essentia features."""
    flat = {}
    for key, value in analysis.items():
        if key == "extras":
            continue
        flat[key] = value
    return flat


def export_training_dataset(
    out_path: Path | str | None = None,
    *,
    require_both_labels: bool = False,
) -> Dict[str, Any]:
    """Export Essentia features + genre/bucket labels to a CSV file."""
    dest = Path(out_path) if out_path else DEFAULT_EXPORT_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)

    rows = load_records(CSV_PATH)
    dataset_rows = []
    stats = {
        "total_rows": len(rows),
        "missing_audio_id": 0,
        "missing_features": 0,
        "missing_labels": 0,
        "computed_hashes": 0,
    }

    for row in rows:
        audio_id, computed_now = _resolve_audio_id(row)
        if not audio_id:
            stats["missing_audio_id"] += 1
            continue
        if computed_now:
            stats["computed_hashes"] += 1

        analysis = get_analysis(audio_id)
        if not analysis:
            stats["missing_features"] += 1
            continue

        genre_label = (row.get("genre") or "").strip()
        bucket_label = (row.get("target_subfolder") or "").strip()
        if require_both_labels and (not genre_label or not bucket_label):
            stats["missing_labels"] += 1
            continue
        if not genre_label and not bucket_label:
            stats["missing_labels"] += 1
            continue

        record = _flatten_analysis(analysis)
        record["genre_label"] = genre_label
        record["bucket_label"] = bucket_label
        record["file_path"] = (row.get("final_path") or row.get("file_path") or "").strip()
        record["track_id"] = row.get("track_id")
        for meta_key in (
            "bpm",
            "key_camelot",
            "energy_hint",
            "must_play",
            "occasion_tags",
            "notes",
            "pop_playcount",
            "pop_listeners",
            "is_duplicate",
        ):
            record[f"library_{meta_key}"] = row.get(meta_key)
        dataset_rows.append(record)

    if dataset_rows:
        fieldnames = _collect_fieldnames(dataset_rows)
        with dest.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for rec in dataset_rows:
                writer.writerow({k: rec.get(k, "") for k in fieldnames})
    else:
        # Still create an empty file with header for downstream tooling
        dest.write_text("genre_label,bucket_label\n", encoding="utf-8")

    stats["rows_exported"] = len(dataset_rows)
    stats["output_path"] = dest
    return stats


def _collect_fieldnames(rows: Iterable[Dict[str, Any]]) -> list[str]:
    """Collect column names preserving stable ordering."""
    seen: Dict[str, None] = {}
    for row in rows:
        for key in row.keys():
            seen.setdefault(key, None)
    # Ensure labels end up at the end for readability
    ordered = [k for k in seen.keys() if k not in {"genre_label", "bucket_label"}]
    ordered.extend([k for k in ("genre_label", "bucket_label") if k in seen])
    return ordered
