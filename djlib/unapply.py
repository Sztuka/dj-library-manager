"""Reverse `dj apply`: move tracks back from library to unsorted staging.

WAL (LOGS/unapply-*.wal.jsonl) makes the operation crash-safe and resumable.

Per-track events:
  UNAPPLY_HASH_OK   — file_hash verified before move
  UNAPPLY_MOVED     — file physically moved to restored path
  UNAPPLY_FAILED    — error for this track (with reason=)

Session events (no track_id, written once):
  UNAPPLY_COMMITTED — both CSVs saved successfully

Resume rule: if session has UNAPPLY_COMMITTED → nothing left to do.
             if track has UNAPPLY_MOVED → skip file move, include in CSV update.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from djlib.fingerprint import file_sha256

# ── WAL event constants ───────────────────────────────────────────────────────

UNAPPLY_HASH_OK   = "UNAPPLY_HASH_OK"
UNAPPLY_MOVED     = "UNAPPLY_MOVED"
UNAPPLY_FAILED    = "UNAPPLY_FAILED"
UNAPPLY_COMMITTED = "UNAPPLY_COMMITTED"

# Fields tried in order to find the current file location in a library row.
_PATH_FIELDS = ("old_full_path", "file_path", "final_path", "original_path")

# Fields mapped 1:1 from library row to restored unsorted row.
_DIRECT_MAP = (
    "track_id", "rekordbox_id", "traktor_id", "file_hash",
    "artist", "title", "bpm", "rating",
    "must_play", "occasion_tags", "notes", "playlists",
    "pop_playcount", "pop_listeners",
)


# ── WAL ───────────────────────────────────────────────────────────────────────


class UnapplyState:
    """Append-only JSON Lines WAL (mirrors RewindState from rewind.py)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def append_event(self, event: str, track_id: str = "", **kwargs: object) -> None:
        entry: Dict = {"event": event, "ts": datetime.now(timezone.utc).isoformat()}
        if track_id:
            entry["track_id"] = track_id
        entry.update(kwargs)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def get_track_states(self) -> Dict[str, str]:
        """Return last WAL event per track_id."""
        states: Dict[str, str] = {}
        if not self._path.exists():
            return states
        with self._path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    tid = entry.get("track_id", "")
                    evt = entry.get("event", "")
                    if tid and evt:
                        states[tid] = evt
                except json.JSONDecodeError:
                    pass
        return states

    def is_committed(self) -> bool:
        """True if the session-level UNAPPLY_COMMITTED event exists."""
        if not self._path.exists():
            return False
        with self._path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("event") == UNAPPLY_COMMITTED:
                        return True
                except (json.JSONDecodeError, AttributeError):
                    pass
        return False


# ── Result ────────────────────────────────────────────────────────────────────


@dataclass
class UnapplyResult:
    moved: int = 0
    skipped_not_in_library: int = 0
    skipped_wrong_location: int = 0   # live_location != "nas"
    skipped_already_done: int = 0     # WAL resume: already committed
    failed_hash_mismatch: int = 0
    failed_other: int = 0
    skipped_wal_resumed: int = 0      # file already moved in prior run
    committed: bool = False
    wal_path: Optional[Path] = None
    unapply_log: Optional[Path] = None


# ── Entry discovery ───────────────────────────────────────────────────────────


def find_move_entries(
    logs_dir: Path,
    track_ids: Optional[List[str]] = None,
    last_run: bool = False,
    last_n: Optional[int] = None,
) -> List[Dict[str, str]]:
    """Return move log entries matching the selection criteria.

    Each entry is a dict with keys: src, dest, track_id, log_file.
    Results are ordered newest-first (most recent apply run first).
    """
    logs = sorted(logs_dir.glob("moves-*.csv"), key=lambda p: p.name, reverse=True)
    if not logs:
        return []

    def _read_log(path: Path) -> List[Dict[str, str]]:
        entries = []
        try:
            with path.open(newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("track_id") and row.get("src") and row.get("dest"):
                        entries.append({
                            "src": row["src"],
                            "dest": row["dest"],
                            "track_id": row["track_id"],
                            "log_file": str(path),
                        })
        except Exception:
            pass
        return entries

    if last_run:
        # All entries from the single most recent moves-*.csv, LIFO order.
        return list(reversed(_read_log(logs[0])))

    if last_n is not None:
        # Newest entries first across as many files as needed.
        collected: List[Dict[str, str]] = []
        seen_ids: set = set()
        for log_path in logs:
            for entry in reversed(_read_log(log_path)):
                tid = entry["track_id"]
                if tid not in seen_ids:
                    seen_ids.add(tid)
                    collected.append(entry)
                if len(collected) >= last_n:
                    break
            if len(collected) >= last_n:
                break
        return collected

    if track_ids is not None:
        # Find most recent occurrence of each requested track_id.
        remaining = set(track_ids)
        result: Dict[str, Dict[str, str]] = {}
        for log_path in logs:
            if not remaining:
                break
            for entry in reversed(_read_log(log_path)):
                tid = entry["track_id"]
                if tid in remaining and tid not in result:
                    result[tid] = entry
                    remaining.discard(tid)
        return list(result.values())

    return []


# ── Unsorted row reconstruction ───────────────────────────────────────────────


def _build_unsorted_row(
    lib_row: Dict[str, str],
    restored_path: Path,
) -> Dict[str, str]:
    """Build an unsorted.csv row from a library.csv row + restored file path.

    Fields that don't survive in library.csv (*_suggest, ai_*, genre,
    version_info, occasion_tags) will be empty — the user will need to
    re-enrich after unapply.
    """
    row: Dict[str, str] = {}

    # Direct 1:1 fields
    for f in _DIRECT_MAP:
        row[f] = lib_row.get(f) or ""

    # Path
    row["file_path"] = str(restored_path)

    # Dates: preserve original added_date rather than recording unapply time.
    row["added_date"] = lib_row.get("added_date") or ""

    # Year: library stores in "year" field (same name as unsorted).
    row["year"] = lib_row.get("year") or ""

    # Key: library stores camelot key in "key" field; unsorted uses "key_camelot".
    row["key_camelot"] = lib_row.get("key") or lib_row.get("key_camelot") or ""

    # Reset workflow state
    row["disposition"] = ""
    row["near_duplicate_of"] = ""

    # Fields lost at apply time (written to audio tags, not to library.csv)
    for lost in (
        "genre", "version_info", "genre_suggest", "artist_suggest",
        "title_suggest", "version_suggest", "year_suggest", "duration_suggest",
        "ai_artist", "ai_title", "ai_version", "ai_genre",
        "ai_confidence", "ai_reasoning", "ai_classify_date",
        "tag_artist_original", "tag_title_original", "tag_genre_original",
        "tag_bpm_original", "tag_key_original",
        "genre_mapping_status", "genres_musicbrainz", "genres_lastfm",
        "genres_soundcloud", "genres_beatport",
        "fingerprint", "meta_source", "is_duplicate", "duplicate_paths",
    ):
        row[lost] = ""

    return row


# ── Main operation ────────────────────────────────────────────────────────────


def run_unapply(
    entries: List[Dict[str, str]],
    unsorted_csv: Path,
    library_csv_path: Path,
    logs_dir: Path,
    inbox_dir: Path,
    dry_run: bool = False,
    resume: bool = False,
    wal_path: Optional[Path] = None,
) -> UnapplyResult:
    """Move tracks back from library to unsorted staging.

    Crash-safe: writes a WAL at ``wal_path`` (or LOGS/unapply-<stamp>.wal.jsonl).
    Use ``resume=True`` to continue after an interrupted run.
    """
    from djlib.library_schema import load_library_csv, save_library_csv
    from djlib.unsorted import UNSORTED_FIELDNAMES, load_unsorted_rows, write_unsorted_rows

    result = UnapplyResult()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    if wal_path is None:
        wal_path = logs_dir / f"unapply-{stamp}.wal.jsonl"
    result.wal_path = wal_path
    wal = UnapplyState(wal_path)

    # Resume guard: if CSVs were already committed, nothing to do.
    if resume and wal.is_committed():
        result.skipped_already_done = len(entries)
        result.committed = True
        return result

    prior_states: Dict[str, str] = wal.get_track_states() if resume else {}

    # Load library index for fast lookup by track_id.
    lib_rows = load_library_csv(library_csv_path) if library_csv_path.exists() else []
    lib_by_id: Dict[str, Dict[str, str]] = {r.get("track_id", ""): r for r in lib_rows}

    # Phase 1: verify + move files.
    moved_entries: List[Dict] = []  # {entry, lib_row, restored_path}

    for entry in entries:
        tid = entry["track_id"]
        src_original = entry["src"]   # where the file was before apply
        dest_in_log  = entry["dest"]  # where apply put it

        # Resume: file already moved in a prior run — skip to CSV phase.
        if prior_states.get(tid) in (UNAPPLY_MOVED,):
            restored = _resolve_restored_path(src_original, inbox_dir, Path(dest_in_log).name)
            lib_row = lib_by_id.get(tid, {})
            moved_entries.append({"entry": entry, "lib_row": lib_row, "restored_path": restored})
            result.skipped_wal_resumed += 1
            continue

        lib_row = lib_by_id.get(tid)
        if lib_row is None:
            print(f"  [SKIP] {tid[:8]}… — not found in library.csv")
            result.skipped_not_in_library += 1
            continue

        live_loc = (lib_row.get("live_location") or "nas").strip()
        if live_loc != "nas" and live_loc != "":
            print(f"  [SKIP] {tid[:8]}… — live_location={live_loc!r} (file on active gig)")
            result.skipped_wrong_location += 1
            continue

        # Find current file path.
        current_path: Optional[Path] = None
        for pf in _PATH_FIELDS:
            candidate = lib_row.get(pf, "")
            if candidate and Path(candidate).exists():
                current_path = Path(candidate)
                break

        if current_path is None:
            print(f"  [FAIL] {tid[:8]}… — file not found (checked: {_PATH_FIELDS})")
            wal.append_event(UNAPPLY_FAILED, track_id=tid, reason="file_not_found")
            result.failed_other += 1
            continue

        # Hash check.
        expected_hash = lib_row.get("file_hash", "")
        if expected_hash:
            actual_hash = file_sha256(current_path)
            if actual_hash != expected_hash:
                print(f"  [FAIL] {tid[:8]}… — hash mismatch (expected {expected_hash[:8]}… got {actual_hash[:8]}…)")
                wal.append_event(UNAPPLY_FAILED, track_id=tid, reason="hash_mismatch",
                                 expected=expected_hash, actual=actual_hash)
                result.failed_hash_mismatch += 1
                continue
        wal.append_event(UNAPPLY_HASH_OK, track_id=tid, path=str(current_path))

        restored_path = _resolve_restored_path(src_original, inbox_dir, current_path.name)

        if dry_run:
            artist = lib_row.get("artist", "?")
            title  = lib_row.get("title", "?")
            print(f"  [DRY-RUN] Would move: {current_path.name}")
            print(f"            {artist} — {title}")
            print(f"            {current_path} → {restored_path}")
            continue

        # Move the file.
        try:
            restored_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(current_path), str(restored_path))
        except OSError as exc:
            print(f"  [FAIL] {tid[:8]}… — move failed: {exc}")
            wal.append_event(UNAPPLY_FAILED, track_id=tid, reason=str(exc))
            result.failed_other += 1
            continue

        wal.append_event(UNAPPLY_MOVED, track_id=tid,
                         src=str(current_path), dest=str(restored_path))
        print(f"  [MOVED] {current_path.name} → {restored_path}")
        moved_entries.append({"entry": entry, "lib_row": lib_row, "restored_path": restored_path})
        result.moved += 1

    if dry_run:
        _print_dry_run_warning(moved_entries)
        return result

    if not moved_entries:
        return result

    # Phase 2: update CSVs.
    # Load fresh copies now (state may have changed since Phase 1 started).
    lib_rows_fresh = load_library_csv(library_csv_path) if library_csv_path.exists() else []
    unsorted_rows  = load_unsorted_rows(unsorted_csv) if unsorted_csv.exists() else []

    moved_ids = {e["entry"]["track_id"] for e in moved_entries}
    existing_unsorted_ids = {r.get("track_id", "") for r in unsorted_rows}

    # Build updated library rows (remove unapply'd tracks).
    new_lib_rows = [r for r in lib_rows_fresh if r.get("track_id") not in moved_ids]

    # Build new unsorted rows to add.
    new_unsorted: List[Dict[str, str]] = []
    for item in moved_entries:
        tid = item["entry"]["track_id"]
        if tid in existing_unsorted_ids:
            print(f"  [SKIP CSV] {tid[:8]}… already in unsorted.csv")
            continue
        unsorted_row = _build_unsorted_row(item["lib_row"], item["restored_path"])
        # Fill any missing UNSORTED_FIELDNAMES keys with "".
        for col in UNSORTED_FIELDNAMES:
            unsorted_row.setdefault(col, "")
        new_unsorted.append(unsorted_row)

    # Write unsorted FIRST: worst crash = track in both CSVs (safe, scan dedupes).
    write_unsorted_rows(unsorted_csv, unsorted_rows + new_unsorted)
    save_library_csv(library_csv_path, new_lib_rows)

    wal.append_event(UNAPPLY_COMMITTED, moved=len(moved_entries))
    result.committed = True

    # Write unapply audit log.
    unapply_log = logs_dir / f"unapply-{stamp}.csv"
    try:
        with unapply_log.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["src", "dest", "track_id"])
            for item in moved_entries:
                w.writerow([
                    str(item.get("lib_row", {}).get("old_full_path", "")),
                    str(item["restored_path"]),
                    item["entry"]["track_id"],
                ])
        result.unapply_log = unapply_log
    except Exception as exc:
        print(f"  [WARN] Could not write unapply log: {exc}")

    return result


# ── Helpers ───────────────────────────────────────────────────────────────────


def _resolve_restored_path(src_original: str, inbox_dir: Path, filename: str) -> Path:
    """Return where to restore the file.

    Prefers the original src directory (from the moves log); falls back to
    inbox_dir when that directory no longer exists.
    """
    src = Path(src_original)
    if src.parent.exists():
        return src
    return inbox_dir / filename


def _print_dry_run_warning(moved_entries: List) -> None:
    if not moved_entries:
        return
    print()
    print("  ⚠️  After unapply, the following fields will be EMPTY and require re-enrichment:")
    print("     genre, version_info, occasion_tags, notes, *_suggest, ai_*")
    print("     Run 'dj enrich-online' after unapply to restore metadata suggestions.")
