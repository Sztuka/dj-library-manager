"""Rewind library tracks back to unsorted for re-processing.

Moves WAV/FLAC files from the library back to the unsorted inbox, converting
them to AIFF in the process. After rewind, the normal scan → review → apply
cycle produces a clean AIFF entry with fresh Rekordbox analysis.

Safety protocol per file:
  1. Compute audio_sha256 of source WAV/FLAC
  2. Convert to AIFF via ffmpeg (temp file)
  3. Verify audio_sha256 of AIFF matches — abort this file if not
  4. Move AIFF to unsorted/
  5. Move original WAV/FLAC to unsorted/.originals/
  6. WAL event DONE
  7. (After all files) single csv_lock write removing rewound rows
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from djlib.convert import audio_sha256, convert_to_aiff

log = logging.getLogger(__name__)

_CONVERT_EXTS = {".wav", ".flac"}
_PATH_FIELDS = ("old_full_path", "file_path", "original_path")

# WAL event names
REWIND_HASHED     = "hashed"
REWIND_CONVERTED  = "converted"
REWIND_VERIFIED   = "verified"
REWIND_MOVED      = "moved"
REWIND_DONE       = "done"
REWIND_FAILED     = "failed"
REWIND_SKIPPED    = "skipped"


# ── WAL ───────────────────────────────────────────────────────────────────────


class RewindState:
    """Append-only JSON Lines WAL for crash-safe rewind (mirrors PrepState)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def append_event(self, track_id: str, event: str, **kwargs: object) -> None:
        entry = {
            "track_id": track_id,
            "event": event,
            "ts": datetime.now(timezone.utc).isoformat(),
            **kwargs,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def get_track_states(self) -> Dict[str, str]:
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


class RewindLock:
    """flock-based guard: only one rewind process at a time."""

    def __init__(self, lock_path: Path) -> None:
        self._path = lock_path
        self._fd: Optional[int] = None

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
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
class RewindResult:
    rewound: int = 0
    skipped_wal: int = 0
    failed_hash_mismatch: int = 0
    failed_conversion: int = 0
    failed_other: int = 0
    originals: List[str] = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _find_src(row: Dict) -> Optional[Path]:
    """Return the first existing WAV/FLAC path in the row, or None."""
    for f in _PATH_FIELDS:
        p = str(row.get(f, "") or "").strip()
        if p and Path(p).suffix.lower() in _CONVERT_EXTS:
            candidate = Path(p)
            if candidate.exists():
                return candidate
    return None


def _dest_aiff(src: Path, unsorted_dir: Path) -> Path:
    """Return the target AIFF path in unsorted_dir for src."""
    return unsorted_dir / src.with_suffix(".aiff").name


def _originals_dir(unsorted_dir: Path, originals_dir: Optional[Path] = None) -> Path:
    if originals_dir is not None:
        return originals_dir
    # Default: sibling to Music Unsorted so Rekordbox and scan never touch it
    return unsorted_dir.parent / "Music Conversion Originals"


def _safe_move_aiff(tmp_aiff: Path, dest: Path) -> None:
    """Move tmp_aiff → dest, handling filename collision by appending suffix."""
    if dest.exists():
        # Audio already at destination — check if it's the same file
        stem = dest.stem
        ext = dest.suffix
        i = 2
        while True:
            candidate = dest.parent / f"{stem} ({i}){ext}"
            if not candidate.exists():
                dest = candidate
                break
            i += 1
    shutil.move(str(tmp_aiff), str(dest))


# ── Main orchestrator ─────────────────────────────────────────────────────────


def run_rewind(
    csv_path: Path,
    unsorted_dir: Path,
    logs_dir: Path,
    dry_run: bool = False,
    resume: bool = False,
    wal_path: Optional[Path] = None,
    originals_dir: Optional[Path] = None,
) -> RewindResult:
    """Move WAV/FLAC from library → unsorted as verified AIFF.

    Args:
        csv_path:     Path to library.csv
        unsorted_dir: ~/Music Unsorted/ (AIFF destination)
        logs_dir:     LOGS/ directory for WAL
        dry_run:      Print plan only, no writes
        resume:       Skip entries already recorded as done in WAL
        wal_path:     Explicit WAL path (defaults to LOGS/rewind-<stamp>.wal.jsonl)
    """
    from djlib.library_schema import load_library_csv, save_library_csv
    from djlib.locks import csv_lock

    result = RewindResult()

    # ── Pre-flight ─────────────────────────────────────────────────────────────
    if not dry_run:
        _check_ffmpeg_or_raise()

    # ── Process lock ───────────────────────────────────────────────────────────
    lock = RewindLock(logs_dir / "rewind.lock")
    if not dry_run:
        if not lock.acquire():
            raise RuntimeError(
                "Another rewind process is already running. "
                "If that is incorrect, delete LOGS/rewind.lock and retry."
            )

    try:
        return _run(
            csv_path=csv_path,
            unsorted_dir=unsorted_dir,
            logs_dir=logs_dir,
            dry_run=dry_run,
            resume=resume,
            wal_path=wal_path,
            originals_dir=originals_dir,
            result=result,
        )
    finally:
        if not dry_run:
            lock.release()


def _check_ffmpeg_or_raise() -> None:
    import subprocess
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
    except (FileNotFoundError, Exception):
        raise RuntimeError(
            "ffmpeg not found on PATH. Install via: brew install ffmpeg"
        )


def _run(
    csv_path: Path,
    unsorted_dir: Path,
    logs_dir: Path,
    dry_run: bool,
    resume: bool,
    wal_path: Optional[Path],
    originals_dir: Optional[Path],
    result: RewindResult,
) -> RewindResult:
    from djlib.library_schema import load_library_csv, save_library_csv
    from djlib.locks import csv_lock

    # ── Load library ───────────────────────────────────────────────────────────
    with csv_lock(csv_path):
        rows = load_library_csv(csv_path)

    # ── Find candidates ────────────────────────────────────────────────────────
    candidates = []
    for row in rows:
        src = _find_src(row)
        if src is None:
            continue
        live_loc = str(row.get("live_location", "") or "")
        if live_loc and live_loc != "nas":
            continue
        candidates.append((row, src))

    if not candidates:
        print("No WAV/FLAC candidates found in library.csv.")
        return result

    total_mb = sum(s.stat().st_size for _, s in candidates) / (1024 * 1024)
    print(f"Found {len(candidates)} WAV/FLAC file(s) (~{total_mb:.0f} MB).")

    if dry_run:
        orig_dir = _originals_dir(unsorted_dir, originals_dir)
        for row, src in candidates:
            dest = _dest_aiff(src, unsorted_dir)
            print(f"  DRY-RUN: {src.name}")
            print(f"           → {dest}")
            print(f"           original → {orig_dir / src.name}")
        return result

    # ── WAL setup ──────────────────────────────────────────────────────────────
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if wal_path is None:
        wal_path = logs_dir / f"rewind-{stamp}.wal.jsonl"

    wal = RewindState(wal_path)
    prior_states: Dict[str, str] = {}
    if resume:
        prior_states = wal.get_track_states()
        if prior_states:
            print(f"Resuming from WAL: {wal_path.name} ({len(prior_states)} prior events)")

    # ── Per-file processing ────────────────────────────────────────────────────
    originals_dir = _originals_dir(unsorted_dir, originals_dir)
    originals_dir.mkdir(parents=True, exist_ok=True)
    unsorted_dir.mkdir(parents=True, exist_ok=True)

    rewound_ids: List[str] = []  # track_ids successfully rewound

    for row, src in candidates:
        tid = row.get("track_id", "")

        # Resume: skip already-done entries
        if resume and prior_states.get(tid) == REWIND_DONE:
            result.skipped_wal += 1
            rewound_ids.append(tid)
            continue

        dest_aiff = _dest_aiff(src, unsorted_dir)
        dest_original = originals_dir / src.name

        print(f"\n  {src.name}")

        # Step 1: hash source
        print(f"    Hashing source…")
        src_hash = audio_sha256(src)
        if src_hash is None:
            log.warning("Cannot hash %s — skipping", src.name)
            wal.append_event(tid, REWIND_FAILED, src=str(src), reason="hash_failed")
            result.failed_other += 1
            continue
        wal.append_event(tid, REWIND_HASHED, src=str(src), audio_sha256=src_hash)

        # Step 2: convert to AIFF
        print(f"    Converting → AIFF…")
        tmp_aiff = convert_to_aiff(src)
        if tmp_aiff is None:
            wal.append_event(tid, REWIND_FAILED, src=str(src), reason="conversion_failed")
            result.failed_conversion += 1
            continue
        wal.append_event(tid, REWIND_CONVERTED, src=str(src), tmp=str(tmp_aiff))

        # Step 3: verify audio hash
        print(f"    Verifying audio hash…")
        aiff_hash = audio_sha256(tmp_aiff)
        if aiff_hash != src_hash:
            tmp_aiff.unlink(missing_ok=True)
            log.warning(
                "Audio hash mismatch for %s: src=%s aiff=%s",
                src.name, src_hash[:16], (aiff_hash or "None")[:16],
            )
            wal.append_event(
                tid, REWIND_FAILED, src=str(src),
                reason="hash_mismatch",
                src_hash=src_hash,
                aiff_hash=aiff_hash,
            )
            result.failed_hash_mismatch += 1
            continue
        wal.append_event(tid, REWIND_VERIFIED, src=str(src), audio_sha256=src_hash)

        # Step 4: move AIFF to unsorted
        try:
            _safe_move_aiff(tmp_aiff, dest_aiff)
        except OSError as exc:
            tmp_aiff.unlink(missing_ok=True)
            wal.append_event(tid, REWIND_FAILED, src=str(src), reason=str(exc))
            result.failed_other += 1
            continue

        # Step 5: move original to .originals/
        try:
            shutil.move(str(src), str(dest_original))
        except OSError as exc:
            # AIFF is safely in unsorted, original move failed — log but continue
            log.warning("Could not move original to .originals/: %s", exc)

        wal.append_event(
            tid, REWIND_DONE,
            src=str(src),
            dest_aiff=str(dest_aiff),
            dest_original=str(dest_original),
            audio_sha256=src_hash,
        )
        result.rewound += 1
        result.originals.append(str(dest_original))
        print(f"    ✓  → {dest_aiff.name}")
        rewound_ids.append(tid)

    # ── CSV update (single csv_lock write) ─────────────────────────────────────
    if rewound_ids:
        rewound_set = set(rewound_ids)
        with csv_lock(csv_path):
            rows = load_library_csv(csv_path)
            remaining = [r for r in rows if r.get("track_id", "") not in rewound_set]
            save_library_csv(csv_path, remaining)

    return result
