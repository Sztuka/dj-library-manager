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

- **djlib gig-tracking** — set by gig-prep, preserved across syncs:
  `live_location` (`nas` | `gig:<gig_id>`), `live_path` (current MacBook
  path while gig is active). Sync skips DJ-software update for tracks
  where `live_location != "nas"` to avoid overwriting with stale NAS data.

- **Cue points** — populated from DJ software on every sync, not djlib-owned
  (fresh data overwrites existing on each sync):
  `cue_points_rb` (JSON, Rekordbox cues), `cue_points_tk` (JSON, Traktor cues).
  Format: `{"v":1,"cues":[...]}`. Empty string means "not yet synced".
  `[]` means synced and track genuinely has no cues.

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
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from djlib.locks import csv_lock

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
    "year",
    "grouping",
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
    # ── Duplicate tracking (scan→apply cue merge) ─────────────────────
    "duplicate_paths",  # JSON array: paths of acoustic duplicates skipped during scan
    # ── Gig tracking (set by gig-prep, preserved across syncs) ─────────
    "live_location",    # "nas" | "gig:<gig_id>" — where the file currently lives
    "live_path",        # absolute path on MacBook while gig is active
    # ── Cue points (populated from DJ software on every sync) ──────────
    "cue_points_rb",    # JSON: {"v":1,"cues":[...]} from Rekordbox
    "cue_points_tk",    # JSON: {"v":1,"cues":[...]} from Traktor
    # ── Play count history (djlib-owned, never overwritten by sync) ────
    "historic_play_count",  # sum of play_counts captured before rewind/reconvert
    # ── Artist normalization (djlib-owned, blocks sync artist overwrite) ─
    "mb_artist_id",         # MusicBrainz artist MBID for the primary artist credit
    "artist_normalized",    # "yes" | "" — set after artist_normalizer merge; blocks DJ-software artist overwrite on sync
    # ── Playlists (djlib-owned, pipe-separated collection tags) ─────────────
    "playlists",            # e.g. "PornoStar|Ultimate BiA" — pushed to Rekordbox as playlists via push-playlists
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
    "duplicate_paths",
    # Gig tracking: set by gig-prep, must survive re-syncs from RB/Traktor.
    # Default empty string = track is on NAS (nominal state).
    "live_location",
    "live_path",
    # Artist normalization: both fields survive syncs unconditionally.
    "mb_artist_id",
    "artist_normalized",
    # Playlists: djlib-owned collection tags, never overwritten by sync.
    "playlists",
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
            # If artist was manually normalized, protect it from DJ-software overwrite.
            if str(prev.get("artist_normalized", "") or "") == "yes":
                out["artist"] = prev.get("artist", out.get("artist", ""))
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


def apply_gig_track_guard(
    merged_rows: List[Dict[str, object]],
    existing_rows: List[Dict[str, object]],
) -> tuple[List[Dict[str, object]], int]:
    """Restore the full pre-sync row for any track currently on an active gig.

    `merge_with_existing_library` preserves DJLIB_OWNED_FIELDS (including
    `live_location`) but still updates DJ-software-owned fields (bpm, key,
    artist, title …) from the fresh snapshot. For gig-tracks that is wrong:
    the NAS copy seen by Rekordbox is stale — the MacBook copy is live. We
    must not overwrite anything with data from the NAS-side DJ software.

    Returns (patched_rows, skip_count).
    """
    existing_full_by_tid: Dict[str, Dict[str, object]] = {
        str(r.get("track_id", "")): r
        for r in existing_rows
        if r.get("track_id")
    }

    skipped = 0
    for row in merged_rows:
        tid = str(row.get("track_id", ""))
        live_loc = str(row.get("live_location", "") or "")
        if live_loc and live_loc != "nas":
            if tid in existing_full_by_tid:
                row.update(existing_full_by_tid[tid])
                skipped += 1
    return merged_rows, skipped


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

    # Lock for the duration of backup + write + sidecar. Re-entrant: callers
    # doing read-modify-write may already hold the lock; that's fine.
    with csv_lock(csv_path):
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
        _write_sha256_sidecar(csv_path)
    return backup_path  # type: ignore[return-value]


def _write_sha256_sidecar(csv_path: Path) -> None:
    """Write SHA-256 checksum of csv_path to csv_path.sha256.

    Written after every save so load_library_csv can verify integrity.
    Uses atomic tmp+rename so the sidecar is never half-written.
    """
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    sidecar = csv_path.with_suffix(".csv.sha256")
    tmp = sidecar.with_suffix(".sha256.tmp")
    tmp.write_text(digest + "\n", encoding="utf-8")
    os.replace(tmp, sidecar)


def verify_library_sha256(csv_path: Path) -> bool:
    """Return True if library.csv matches its .sha256 sidecar.

    Returns True (passes) when the sidecar doesn't exist yet — first-ever
    write before the sidecar was introduced. Logs a warning on mismatch.
    """
    sidecar = csv_path.with_suffix(".csv.sha256")
    if not sidecar.exists():
        return True
    if not csv_path.exists():
        return True
    expected = sidecar.read_text(encoding="utf-8").strip()
    actual = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    if actual != expected:
        logger.warning(
            "library.csv SHA-256 mismatch — file may be corrupted. "
            "Expected %s, got %s. Check data/backups/ to restore.",
            expected[:12] + "…",
            actual[:12] + "…",
        )
        return False
    return True
