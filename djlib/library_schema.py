"""Canonical schema for data/library.csv and safe-write helpers.

`library.csv` is the master track database. It is regenerated end-to-end by
`sync-dj-libraries` (merging Rekordbox + Traktor snapshots) and read by the
Review UI, apply flow, and ML export. Any process that writes to it MUST go
through `save_library_csv()` so that:

- Writes are atomic: partial library never visible on disk (tmp + fsync +
  os.replace). A crash mid-write leaves the previous good file untouched.
- A timestamped backup is snapshotted before each overwrite, so a bad sync
  can always be rolled back. Old backups auto-rotate.
- A schema-version sidecar is updated next to the CSV, so tools that load
  the file can detect format drift.

Field ownership (who is source-of-truth after PR2b merge-by-track_id lands):

- **djlib-owned** — preserved across syncs, never overwritten from RB/Traktor:
  `track_id`, `file_hash`, `original_path`, `added_date`, `snapshot_date`
  (the djlib snapshot date, not the DJ-software one).

- **DJ-software-owned** — freshly pulled from RB/Traktor every sync:
  `rekordbox_id`, `traktor_id`, `external_source`, `old_full_path`
  (where the DJ software currently sees the file), `bpm`, `key`, `rating`,
  `color`, `duration_seconds`, `play_count`, `last_played`, `cue_count`,
  `artist`, `title`.

- **Reserved for future PR2b/PR3** (declared so writers can start emitting
  them without further schema changes):
  `key_original` (pre-normalization form), `key_source` (where the key came
  from: `rekordbox` | `traktor` | `tag` | `beatport` | ``),
  `analysis_source` (BPM+Key provenance for the no-rekordbox workflow).

Note: PR2a only defines the schema and the safe writer; it does NOT yet
change how `sync-dj-libraries` populates fields. Merge-by-track_id lands
in PR2b — without it, djlib-owned fields are still wiped on every sync.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

LIBRARY_SCHEMA_VERSION = 1

# Canonical field order. New fields go AT THE END so column indexes stay
# stable for any consumer parsing by position (there shouldn't be any, but
# csv.DictReader tolerates extra or missing columns either way).
LIBRARY_FIELDNAMES: List[str] = [
    # ── Identity ────────────────────────────────────────────────────────
    "track_id",
    "rekordbox_id",
    "traktor_id",
    "external_source",
    "external_track_id",
    # ── File location ───────────────────────────────────────────────────
    "old_full_path",
    "original_path",
    "file_hash",
    # ── Bibliographic metadata ──────────────────────────────────────────
    "artist",
    "title",
    # ── Musical attributes (DJ-software-owned) ──────────────────────────
    "bpm",
    "key",
    "key_original",   # reserved for PR2b: pre-normalization form
    "key_source",     # reserved for PR2b: rekordbox|traktor|tag|beatport|''
    "rating",
    "color",
    "duration_seconds",
    "play_count",
    "last_played",
    "cue_count",
    # ── djlib-owned timestamps ──────────────────────────────────────────
    "added_date",     # when djlib first added this track (survives syncs)
    "date_added",     # DJ-software's own "date added" (RB/Traktor import)
    "snapshot_date",  # when this row was last refreshed from a sync
    # ── Reserved for PR3 (scan↔apply contract) ──────────────────────────
    "analysis_source",  # tags|rekordbox|traktor|beatport
    # ── Per-field provenance (ghost-row review) ───────────────────────────
    "field_sources",    # JSON: {"genre": "ai_classifier:nano+WS+LF", "year": "manual", ...}
]

# Default retention for the backup folder. Syncs can happen several times a
# day during active library work, so 20 gives ~a week of safety without
# runaway disk use. Each backup is a ~few-MB CSV.
DEFAULT_BACKUP_RETENTION = 20

_BACKUP_NAME_RE = re.compile(r"^library-(\d{8}-\d{6})\.csv$")


def _backup_dir_for(csv_path: Path) -> Path:
    return csv_path.parent / "backups"


def _now_timestamp() -> str:
    # UTC to avoid DST ambiguity in sort order
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _rotate_backups(backup_dir: Path, keep: int) -> int:
    """Delete oldest backups beyond `keep`. Returns number removed."""
    if not backup_dir.exists():
        return 0
    existing: List[Path] = []
    for p in backup_dir.iterdir():
        if p.is_file() and _BACKUP_NAME_RE.match(p.name):
            existing.append(p)
    # Name sort == timestamp sort because format is fixed-width YYYYMMDD-HHMMSS
    existing.sort(key=lambda p: p.name)
    removed = 0
    while len(existing) > keep:
        oldest = existing.pop(0)
        try:
            oldest.unlink()
            removed += 1
        except OSError as e:
            logger.warning("Could not remove old backup %s: %s", oldest, e)
    return removed


def _backup_existing(csv_path: Path, retention: int) -> Optional[Path]:
    """Copy current `csv_path` to `backups/library-<ts>.csv`. No-op if absent."""
    if not csv_path.exists():
        return None
    backup_dir = _backup_dir_for(csv_path)
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"library-{_now_timestamp()}.csv"
    # copy2 preserves mtime — useful when debugging "what did the library
    # look like before sync X"
    shutil.copy2(csv_path, dest)
    _rotate_backups(backup_dir, retention)
    return dest


def _write_schema_sidecar(csv_path: Path, row_count: int) -> None:
    sidecar = csv_path.with_suffix(".schema.json")
    payload = {
        "schema_version": LIBRARY_SCHEMA_VERSION,
        "fieldnames": LIBRARY_FIELDNAMES,
        "row_count": row_count,
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = sidecar.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, sidecar)


# Fields that djlib owns and must survive a sync from RB/Traktor.
# Rationale per field:
#   file_hash, original_path — computed by djlib at `apply`, neither DJ
#       software stores them.
#   added_date — when djlib first adopted the track; `date_added` coming
#       from RB/Traktor is the DJ-software import date, a different thing.
#   key_original, key_source — reserved for PR3 key-tracking; once set,
#       a re-sync should not clear them because RB doesn't know the
#       original form.
#   analysis_source — reserved for PR3 (tags|rekordbox|traktor|beatport).
DJLIB_OWNED_FIELDS: List[str] = [
    "file_hash",
    "original_path",
    "added_date",
    "key_original",
    "key_source",
    "analysis_source",
    "field_sources",
]


def merge_with_existing_library(
    new_rows: List[Dict[str, object]],
    existing_rows: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    """Merge fresh RB/Traktor snapshot with existing library.csv by track_id.

    Why this exists: `cmd_sync_dj_libraries` used to overwrite library.csv
    with pandas `to_csv`, which wiped every field that djlib owns because
    those fields don't come from Rekordbox/Traktor. The canonical writer
    from PR2a fixed the *mechanics* of the write; this function fixes the
    *content*. Together they make `sync-dj-libraries` non-destructive for
    djlib-owned state.

    Rules:
      1. For each `track_id` present in both new and existing: take the new
         row (DJ-software data is fresh) but overlay any non-empty
         `DJLIB_OWNED_FIELDS` value from the existing row. `added_date` is
         also backfilled from the row's own `date_added` when neither side
         has it — first sync after an import should not leave it blank.
      2. New `track_id`s pass through unchanged (with the same `added_date`
         backfill).
      3. Existing `track_id`s that the sync no longer returns become
         **orphans**: the row is kept but `external_source`, `rekordbox_id`,
         `traktor_id` are cleared. A DJ can still find the file via its
         djlib-owned path, and a later sync will re-adopt it if RB/Traktor
         see it again. (Deleting orphans outright would silently drop
         tracks that were merely re-imported under a new DB ID.)
    """
    existing_by_tid: Dict[str, Dict[str, object]] = {}
    for r in existing_rows:
        tid = str(r.get("track_id") or "").strip()
        if tid:
            existing_by_tid[tid] = r

    seen_tids: set = set()
    merged: List[Dict[str, object]] = []

    for row in new_rows:
        tid = str(row.get("track_id") or "").strip()
        out: Dict[str, object] = dict(row)

        if tid and tid in existing_by_tid:
            prev = existing_by_tid[tid]
            for field in DJLIB_OWNED_FIELDS:
                prev_val = prev.get(field, "")
                if prev_val not in ("", None):
                    out[field] = prev_val
            seen_tids.add(tid)

        if not out.get("added_date"):
            # Backfill: first time we're seeing this track, use whatever
            # "date added" the DJ software had, else stay empty.
            out["added_date"] = row.get("date_added", "") or (
                existing_by_tid.get(tid, {}).get("added_date", "") if tid else ""
            )

        merged.append(out)

    # Orphans: existed before, no longer returned by any DJ software
    for tid, prev in existing_by_tid.items():
        if tid in seen_tids:
            continue
        orphan = dict(prev)
        orphan["external_source"] = ""
        orphan["rekordbox_id"] = ""
        orphan["traktor_id"] = ""
        merged.append(orphan)

    return merged


def load_library_csv(csv_path: Path) -> List[Dict[str, str]]:
    """Read existing library.csv. Returns [] if absent or empty."""
    if not csv_path.exists():
        return []
    with csv_path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_library_csv(
    csv_path: Path,
    rows: List[Dict[str, object]],
    *,
    backup_retention: int = DEFAULT_BACKUP_RETENTION,
    extra_fieldnames: Optional[List[str]] = None,
) -> Path:
    """Atomically write library.csv with pre-write backup and schema sidecar.

    Args:
        csv_path: Destination (e.g. `data/library.csv`).
        rows: Iterable of dict rows. Missing fields are written as ``""``.
              Unknown fields are silently dropped unless declared in
              ``extra_fieldnames``.
        backup_retention: How many historical backups to keep.
        extra_fieldnames: Additional columns appended after the canonical
              ones. Use sparingly — this is a pressure valve for transient
              fields during migration, not a substitute for updating
              ``LIBRARY_FIELDNAMES``.

    Returns:
        Path to the backup that was created, or ``None`` if the destination
        did not exist yet (first-ever write).
    """
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = list(LIBRARY_FIELDNAMES)
    if extra_fieldnames:
        seen = set(fieldnames)
        for name in extra_fieldnames:
            if name not in seen:
                fieldnames.append(name)
                seen.add(name)

    backup_path = _backup_existing(csv_path, backup_retention)

    # Atomic write: tmp in same dir (so os.replace is atomic on same FS),
    # fsync before rename so the kernel has durably persisted the bytes.
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{csv_path.name}.",
        suffix=".tmp",
        dir=str(csv_path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                # DictWriter handles missing keys only if we pre-fill, so
                # normalize now.
                clean = {k: ("" if row.get(k) is None else row.get(k, "")) for k in fieldnames}
                writer.writerow(clean)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, csv_path)
    except Exception:
        # Leave no junk behind on failure; original file is untouched
        # because we only replace after fsync.
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise

    _write_schema_sidecar(csv_path, row_count=len(rows))
    return backup_path  # type: ignore[return-value]
