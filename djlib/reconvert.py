"""In-place batch conversion of WAV/FLAC files in library.csv to AIFF.

Why: Rekordbox does not reliably read WAV/FLAC ID3 tags or display cover art.
AIFF uses the same ID3 tag format as MP3 and is fully supported by Rekordbox.

Safety model:
- WAL (JSON Lines at LOGS/reconvert-<stamp>.state) for crash recovery
- Originals are NEVER auto-deleted — printed list at end for manual cleanup
- Skip if <same-stem>.aiff already exists and size > 0 (idempotent)
- Single csv_lock write at the end (old_full_path updated)
- Moves log written for sync-dj-libraries to pick up path change
- Process lock (flock) prevents concurrent reconvert runs
- Only touches tracks where live_location in ("", "nas")
"""
from __future__ import annotations

import csv as csv_mod
import fcntl
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from djlib.convert import convert_to_aiff

log = logging.getLogger(__name__)

_CONVERT_EXTS = {".wav", ".flac"}

# WAL event names
RECONVERT_START     = "convert_start"
RECONVERT_DONE      = "converted"
RECONVERT_FAILED    = "failed"
RECONVERT_SKIPPED   = "skipped"


# ── WAL ───────────────────────────────────────────────────────────────────────


class ReconvertState:
    """Append-only JSON Lines WAL for crash-safe reconvert (mirrors PrepState)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def append_event(self, track_id: str, event: str, **kwargs: object) -> None:
        entry = {
            "track_id": track_id,
            "event": event,
            "ts": datetime.now(timezone.utc).isoformat(),
            **kwargs,
        }
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def get_track_states(self) -> Dict[str, str]:
        """Return {track_id: last_event} for all entries in the log."""
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


# ── Process lock ──────────────────────────────────────────────────────────────


class ReconvertLock:
    """flock-based guard: only one reconvert process at a time."""

    def __init__(self, lock_path: Path) -> None:
        self._path = lock_path
        self._fd: Optional[int] = None

    def acquire(self) -> bool:
        self._fd = os.open(str(self._path), os.O_CREAT | os.O_WRONLY)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            os.close(self._fd)
            self._fd = None
            return False

    def release(self) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


# ── Result ────────────────────────────────────────────────────────────────────


@dataclass
class ReconvertResult:
    converted: int = 0
    skipped_already_aiff: int = 0
    skipped_wal: int = 0
    failed: int = 0
    originals_to_delete: List[str] = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _check_ffmpeg() -> bool:
    """Return True if ffmpeg is available on PATH."""
    import subprocess
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _disk_free_bytes(path: Path) -> int:
    """Return free bytes on the filesystem containing path."""
    import shutil
    return shutil.disk_usage(path).free


def _file_size_bytes(paths: List[Path]) -> int:
    """Return total size of existing files in paths."""
    total = 0
    for p in paths:
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


# ── Main orchestrator ─────────────────────────────────────────────────────────


def run_reconvert(
    csv_path: Path,
    logs_dir: Path,
    dry_run: bool = False,
    resume: bool = False,
    state_path: Optional[Path] = None,
) -> ReconvertResult:
    """Convert all WAV/FLAC entries in library.csv to AIFF in-place.

    Args:
        csv_path:   Path to library.csv
        logs_dir:   Directory for WAL + moves log
        dry_run:    Print plan only, no writes
        resume:     Resume using existing WAL (skip already-converted entries)
        state_path: Explicit WAL path (defaults to LOGS/reconvert-<stamp>.state)

    Returns:
        ReconvertResult with counters and list of originals to delete manually.
    """
    from djlib.library_schema import load_library_csv, save_library_csv
    from djlib.locks import csv_lock

    result = ReconvertResult()

    # ── Pre-flight: ffmpeg ─────────────────────────────────────────────────────
    if not dry_run and not _check_ffmpeg():
        raise RuntimeError(
            "ffmpeg not found on PATH — cannot convert files. "
            "Install via: brew install ffmpeg"
        )

    # ── Process lock ───────────────────────────────────────────────────────────
    logs_dir.mkdir(parents=True, exist_ok=True)
    lock = ReconvertLock(logs_dir / "reconvert.lock")
    if not dry_run:
        if not lock.acquire():
            raise RuntimeError(
                "Another reconvert process is already running. "
                "If that is incorrect, delete LOGS/reconvert.lock and retry."
            )

    try:
        return _run(
            csv_path=csv_path,
            logs_dir=logs_dir,
            dry_run=dry_run,
            resume=resume,
            state_path=state_path,
            result=result,
        )
    finally:
        if not dry_run:
            lock.release()


def _run(
    csv_path: Path,
    logs_dir: Path,
    dry_run: bool,
    resume: bool,
    state_path: Optional[Path],
    result: ReconvertResult,
) -> ReconvertResult:
    from djlib.library_schema import load_library_csv, save_library_csv
    from djlib.locks import csv_lock

    # ── Load library ───────────────────────────────────────────────────────────
    with csv_lock(csv_path):
        rows = load_library_csv(csv_path)

    # ── Find candidates ────────────────────────────────────────────────────────
    candidates = []
    for row in rows:
        path_str = row.get("old_full_path", "")
        if not path_str:
            continue
        src = Path(path_str)
        if src.suffix.lower() not in _CONVERT_EXTS:
            continue
        live_loc = str(row.get("live_location", "") or "")
        # Skip tracks currently on MacBook for a gig
        if live_loc and live_loc != "nas":
            continue
        if not src.exists():
            continue
        candidates.append((row, src))

    if not candidates:
        print("No WAV/FLAC candidates found in library.csv.")
        return result

    total_size = _file_size_bytes([src for _, src in candidates])
    total_mb = total_size / (1024 * 1024)

    print(f"Found {len(candidates)} WAV/FLAC file(s) to convert (~{total_mb:.0f} MB).")

    if not dry_run:
        # Disk space warning: AIFF is roughly same size as WAV (lossless)
        free = _disk_free_bytes(candidates[0][1].parent)
        if free < total_size * 1.1:
            free_mb = free / (1024 * 1024)
            print(
                f"WARNING: Low disk space — {free_mb:.0f} MB free, "
                f"~{total_mb:.0f} MB needed. Proceeding anyway."
            )

    if dry_run:
        for row, src in candidates:
            dest = src.with_suffix(".aiff")
            print(f"  DRY-RUN: {src.name} → {dest.name}")
        return result

    # ── WAL setup ──────────────────────────────────────────────────────────────
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if state_path is None:
        state_path = logs_dir / f"reconvert-{stamp}.state"

    wal = ReconvertState(state_path)
    prior_states: Dict[str, str] = {}
    if resume:
        prior_states = wal.get_track_states()
        if prior_states:
            print(f"Resuming from WAL: {state_path.name} ({len(prior_states)} prior events)")

    # ── Per-file conversion ────────────────────────────────────────────────────
    # Maps track_id → new_path for successful conversions
    converted_paths: Dict[str, Path] = {}

    for row, src in candidates:
        tid = row.get("track_id", "")
        dest = src.with_suffix(".aiff")

        # Resume: skip already-finished entries
        if resume and prior_states.get(tid) in (RECONVERT_DONE, RECONVERT_SKIPPED):
            result.skipped_wal += 1
            # Recover dest path for csv update
            if dest.exists() and dest.stat().st_size > 0:
                converted_paths[tid] = dest
            continue

        # Idempotent: skip if AIFF already exists and is non-empty
        if dest.exists() and dest.stat().st_size > 0:
            log.info("AIFF already exists, skipping: %s", dest.name)
            wal.append_event(tid, RECONVERT_SKIPPED, src=str(src), dest=str(dest), reason="aiff_exists")
            result.skipped_already_aiff += 1
            converted_paths[tid] = dest
            continue

        wal.append_event(tid, RECONVERT_START, src=str(src), dest=str(dest))

        tmp_path = convert_to_aiff(src)
        if tmp_path is None:
            log.warning("Conversion failed: %s", src.name)
            wal.append_event(tid, RECONVERT_FAILED, src=str(src), reason="ffmpeg_error")
            result.failed += 1
            continue

        # Atomic rename: temp → dest
        try:
            tmp_path.rename(dest)
        except OSError as exc:
            log.warning("Rename failed %s → %s: %s", tmp_path, dest, exc)
            tmp_path.unlink(missing_ok=True)
            wal.append_event(tid, RECONVERT_FAILED, src=str(src), reason=str(exc))
            result.failed += 1
            continue

        wal.append_event(tid, RECONVERT_DONE, src=str(src), dest=str(dest))
        result.converted += 1
        converted_paths[tid] = dest
        result.originals_to_delete.append(str(src))
        print(f"  ✓  {src.name} → {dest.name}")

    # ── CSV update (single csv_lock write) ─────────────────────────────────────
    if converted_paths:
        moves_log_path = logs_dir / f"moves-{stamp}.csv"
        move_rows: List[List[str]] = []

        with csv_lock(csv_path):
            rows = load_library_csv(csv_path)
            for row in rows:
                tid = row.get("track_id", "")
                new_path = converted_paths.get(tid)
                if new_path is None:
                    continue
                old_path = row.get("old_full_path", "")
                row["old_full_path"] = str(new_path)
                move_rows.append([old_path, str(new_path), tid])
            save_library_csv(csv_path, rows)

        # Write moves log for sync-dj-libraries
        logs_dir.mkdir(parents=True, exist_ok=True)
        with moves_log_path.open("w", newline="", encoding="utf-8") as mf:
            writer = csv_mod.writer(mf)
            writer.writerow(["src", "dest", "track_id"])
            writer.writerows(move_rows)
        print(f"\nMoves log: {moves_log_path}")

    return result
