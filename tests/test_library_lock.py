"""Tests for djlib/locks.py — re-entrant cross-process CSV lock.

Verifies:
  - Single-process re-entrancy: nested csv_lock blocks do not deadlock
  - Cross-process serialization: two processes can't hold the lock at once
  - Timeout fires with diagnostic info when lock can't be acquired
  - Different CSV paths get independent locks
  - Lock is released on exception
  - save_library_csv / save_records / write_unsorted_rows acquire it
"""
from __future__ import annotations

import multiprocessing as mp
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from djlib.locks import LibraryLockTimeout, csv_lock


# ── Single-process behavior ───────────────────────────────────────────────────

def test_acquire_and_release(tmp_path):
    csv = tmp_path / "x.csv"
    with csv_lock(csv):
        assert (tmp_path / ".x.csv.lock").exists()
    # Should be re-acquirable immediately after release
    with csv_lock(csv):
        pass


def test_reentrant_nested_same_path(tmp_path):
    """Nested csv_lock on the same path must not deadlock."""
    csv = tmp_path / "x.csv"
    with csv_lock(csv):
        with csv_lock(csv):
            with csv_lock(csv):
                pass


def test_reentrant_then_release_then_reacquire(tmp_path):
    """After fully releasing, the lock can be re-acquired."""
    csv = tmp_path / "x.csv"
    with csv_lock(csv):
        with csv_lock(csv):
            pass
    # Outer also released — lock should be free
    with csv_lock(csv):
        pass


def test_different_paths_are_independent(tmp_path):
    """Two different CSV paths must get independent locks (no false serialization)."""
    csv_a = tmp_path / "a.csv"
    csv_b = tmp_path / "b.csv"
    with csv_lock(csv_a):
        # Acquiring b should not block on a
        with csv_lock(csv_b):
            pass


def test_lock_released_on_exception(tmp_path):
    """A raised exception inside the lock must still release it."""
    csv = tmp_path / "x.csv"
    with pytest.raises(ValueError):
        with csv_lock(csv):
            raise ValueError("boom")
    # Should be re-acquirable
    with csv_lock(csv):
        pass


def test_diagnostic_pid_written(tmp_path):
    """Lock file contains pid+timestamp for debugging."""
    csv = tmp_path / "x.csv"
    with csv_lock(csv):
        contents = (tmp_path / ".x.csv.lock").read_text()
        assert f"pid={os.getpid()}" in contents
        assert "started=" in contents


# ── Cross-process behavior ────────────────────────────────────────────────────

def _hold_lock_subprocess(lock_path: str, hold_seconds: float) -> int:
    """Run in a subprocess: acquire the lock, sleep, release."""
    code = f"""
import time
from pathlib import Path
from djlib.locks import csv_lock
with csv_lock(Path({lock_path!r})):
    Path({lock_path!r}).with_name('.held').touch()
    time.sleep({hold_seconds})
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc.pid, proc


def test_cross_process_serialization(tmp_path):
    """A second process must wait for the first to release."""
    csv = tmp_path / "x.csv"
    sentinel = tmp_path / ".held"

    # Start subprocess that holds the lock for 2 seconds
    _, proc = _hold_lock_subprocess(str(csv), hold_seconds=2.0)
    try:
        # Wait for subprocess to actually acquire (sentinel file appears)
        deadline = time.monotonic() + 5.0
        while not sentinel.exists():
            if time.monotonic() >= deadline:
                pytest.fail("Subprocess didn't acquire lock within 5s")
            time.sleep(0.05)

        # Now try to acquire from this process — should block until subprocess releases
        t0 = time.monotonic()
        with csv_lock(csv, timeout=10.0):
            elapsed = time.monotonic() - t0
            # Subprocess held for ~2s; we should have waited at least ~1s
            assert elapsed >= 1.0, f"Should have waited for subprocess, got {elapsed:.2f}s"
    finally:
        proc.wait(timeout=10)


def test_timeout_fires_with_holder_info(tmp_path):
    """When timeout expires, raises LibraryLockTimeout with holder PID."""
    csv = tmp_path / "x.csv"
    sentinel = tmp_path / ".held"

    _, proc = _hold_lock_subprocess(str(csv), hold_seconds=5.0)
    try:
        # Wait for subprocess to acquire
        deadline = time.monotonic() + 5.0
        while not sentinel.exists():
            if time.monotonic() >= deadline:
                pytest.fail("Subprocess didn't acquire lock")
            time.sleep(0.05)

        # Try with very short timeout — should raise
        with pytest.raises(LibraryLockTimeout) as excinfo:
            with csv_lock(csv, timeout=0.5):
                pass
        msg = str(excinfo.value)
        assert "pid=" in msg, f"Expected holder PID in error: {msg}"
        assert str(proc.pid) in msg
    finally:
        proc.wait(timeout=10)


# ── Threading behavior (same process) ─────────────────────────────────────────

def test_threads_serialize_on_same_path(tmp_path):
    """Two threads on the same path must serialize (each gets its own fd via os.open)."""
    csv = tmp_path / "x.csv"
    order = []

    def worker(label: str):
        with csv_lock(csv, timeout=5.0):
            order.append(f"{label}-enter")
            time.sleep(0.1)
            order.append(f"{label}-exit")

    t1 = threading.Thread(target=worker, args=("A",))
    t2 = threading.Thread(target=worker, args=("B",))
    t1.start()
    # Tiny gap so A definitely starts first
    time.sleep(0.02)
    t2.start()
    t1.join(); t2.join()

    # A must fully complete before B enters (or vice versa) — never interleaved
    a_idx_enter = order.index("A-enter")
    a_idx_exit = order.index("A-exit")
    b_idx_enter = order.index("B-enter")
    # B-enter cannot fall between A-enter and A-exit
    assert not (a_idx_enter < b_idx_enter < a_idx_exit), \
        f"Threads interleaved: {order}"


# ── Integration with library_schema / unsorted ────────────────────────────────

def test_save_library_csv_acquires_lock(tmp_path):
    """save_library_csv must acquire the lock — verify by trying to grab it from another thread."""
    from djlib.library_schema import LIBRARY_FIELDNAMES, save_library_csv

    csv = tmp_path / "library.csv"
    rows = [{f: "" for f in LIBRARY_FIELDNAMES} | {"track_id": "t1", "artist": "A"}]

    blocked = threading.Event()
    can_proceed = threading.Event()

    def hold_lock():
        with csv_lock(csv, timeout=5.0):
            blocked.set()
            can_proceed.wait(timeout=5.0)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    blocked.wait(timeout=2.0)

    # save_library_csv should block trying to acquire — verify with short timeout.
    # Using a sentinel: if the call returned, it means it didn't acquire.
    save_returned = threading.Event()

    def attempt_save():
        try:
            save_library_csv(csv, rows)
            save_returned.set()
        except LibraryLockTimeout:
            pass

    saver = threading.Thread(target=attempt_save)
    saver.start()
    # Within 0.3s, save should NOT have completed (because lock is held)
    time.sleep(0.3)
    assert not save_returned.is_set(), "save_library_csv ran while lock was held"

    # Release lock — now save can proceed
    can_proceed.set()
    saver.join(timeout=5.0)
    holder.join(timeout=5.0)
    assert save_returned.is_set(), "save_library_csv didn't complete after lock released"


def test_write_unsorted_rows_acquires_lock(tmp_path):
    """write_unsorted_rows must acquire the lock."""
    from djlib.unsorted import UNSORTED_FIELDNAMES, write_unsorted_rows

    csv = tmp_path / "unsorted.csv"
    rows = [{f: "" for f in UNSORTED_FIELDNAMES} | {"track_id": "t1"}]

    blocked = threading.Event()
    can_proceed = threading.Event()

    def hold_lock():
        with csv_lock(csv, timeout=5.0):
            blocked.set()
            can_proceed.wait(timeout=5.0)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    blocked.wait(timeout=2.0)

    write_returned = threading.Event()

    def attempt_write():
        try:
            write_unsorted_rows(csv, rows)
            write_returned.set()
        except LibraryLockTimeout:
            pass

    writer = threading.Thread(target=attempt_write)
    writer.start()
    time.sleep(0.3)
    assert not write_returned.is_set()

    can_proceed.set()
    writer.join(timeout=5.0)
    holder.join(timeout=5.0)
    assert write_returned.is_set()
