"""Re-entrant per-thread file locks for CSV read-modify-write safety.

Prevents silent data loss when two processes (or two Review UI tabs / Flask
threads) try to modify the same CSV concurrently.

Threat model: Review UI's `update_track` does load → modify → write. Without
locking, two concurrent calls both read the same baseline, each modify their
copy, each write back — the second write silently overwrites the first.
The SHA-256 sidecar will be valid for the second write, masking the loss.

Lock semantics:
- Advisory POSIX lock via `fcntl.flock` (LOCK_EX). Cooperative — only
  processes that opt in are protected. CSV writers in this codebase MUST opt in.
- Re-entrant within a single THREAD: nested `with csv_lock(path):` blocks
  on the same path do not deadlock. The outermost block owns the OS-level
  lock; inner blocks bump a per-thread counter. This lets `save_library_csv`
  acquire its own lock for atomic-write safety even when the caller already
  holds one for a wider RMW.
- Different threads in the same process serialize through fcntl as if they
  were different processes (each opens its own fd). The re-entrant tracking
  is intentionally per-thread to avoid one thread "stealing" another's hold.
- Lock file at `<csv>.parent / .<csv-name>.lock` — kept dotted to avoid
  showing up in `ls`.
- The lock file body holds `pid=<pid> tid=<tid> started=<unix-ts>` for
  diagnostics on timeout.
- Default timeout 30 s. Raises `LibraryLockTimeout` on timeout with the
  holder's diagnostic info.

Note: fcntl.flock locks are tied to the file descriptor, NOT the inode. A
second open() of the same path gets a separate fd that *will* block on the
existing flock — so cross-process AND cross-thread serialization both work.
"""
from __future__ import annotations

import fcntl
import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Tuple

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SEC = 30.0
_POLL_INTERVAL_SEC = 0.05


class LibraryLockTimeout(TimeoutError):
    """Raised when a CSV lock cannot be acquired within the timeout."""


class _ThreadLocalLocks(threading.local):
    """Per-thread re-entrant tracking. Each thread sees its OWN held dict.

    Without this, thread B calling csv_lock(p) while thread A holds it
    would incorrectly re-enter (seeing _held[p] from A), bypass the OS
    lock acquisition, and yield without serialization — silent corruption.
    """
    def __init__(self) -> None:
        # path-string → (fd, depth)
        self.held: Dict[str, Tuple[int, int]] = {}


_state = _ThreadLocalLocks()


def _lock_path_for(csv_path: Path) -> Path:
    """Return the lock file path for a given CSV path."""
    return csv_path.parent / f".{csv_path.name}.lock"


def _read_holder_info(fd: int) -> str:
    """Read the holder's diagnostic info from the lock file (best-effort)."""
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        data = os.read(fd, 256)
        return data.decode("utf-8", errors="replace").strip() or "(unknown)"
    except OSError:
        return "(unreadable)"


@contextmanager
def csv_lock(csv_path: Path, timeout: float = _DEFAULT_TIMEOUT_SEC):
    """Acquire an exclusive lock for read-modify-write on csv_path.

    Re-entrant within the calling thread. Safe to nest:

        with csv_lock(p):
            rows = load_csv(p)
            rows[0]["x"] = "y"
            save_csv(p, rows)   # save_csv internally also calls csv_lock — OK

    Args:
        csv_path: Path to the CSV file (the lock file is derived from it).
        timeout: Seconds to wait before raising LibraryLockTimeout. Default 30 s.

    Raises:
        LibraryLockTimeout: if the lock cannot be acquired within the timeout.
    """
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path_for(csv_path)
    key = str(lock_path.resolve())

    held = _state.held

    # Re-entry path: this THREAD already holds the lock for this path.
    existing = held.get(key)
    if existing is not None:
        fd, depth = existing
        held[key] = (fd, depth + 1)
        try:
            yield
        finally:
            fd, depth = held[key]
            if depth > 1:
                held[key] = (fd, depth - 1)
            # When depth == 1, we leave the entry alone — the outer block
            # is still active and will release on its own __exit__.
        return

    # First entry in this thread: acquire the OS-level lock.
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    holder = _read_holder_info(fd)
                    raise LibraryLockTimeout(
                        f"Could not acquire {lock_path} within {timeout:.1f}s. "
                        f"Holder: {holder}"
                    )
                time.sleep(_POLL_INTERVAL_SEC)

        # Write our diagnostic info into the lock file (truncate first).
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            info = (
                f"pid={os.getpid()} "
                f"tid={threading.get_ident()} "
                f"started={time.time():.3f}\n"
            )
            os.write(fd, info.encode("utf-8"))
        except OSError:
            pass  # Diagnostic only

        held[key] = (fd, 1)
        try:
            yield
        finally:
            held.pop(key, None)
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError as exc:
                log.warning("Failed to release lock on %s: %s", lock_path, exc)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
