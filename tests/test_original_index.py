from __future__ import annotations

import errno
import os
import subprocess
import unicodedata
from pathlib import Path
from unittest.mock import patch

import pytest

from djlib import original


def _write_file(path: Path, content: bytes = b"") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _snapshot(root: Path) -> set:
    entries = set()
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            try:
                st = p.stat()
                entries.add((str(p), st.st_mtime_ns, st.st_size))
            except OSError:
                entries.add((str(p), None, None))
    return entries


# ── 1. errno classification ─────────────────────────────────────────────

def test_scan_distinguishes_eio_from_enoent(tmp_path, monkeypatch):
    root = tmp_path / "ORIGINAL"
    area_root = root / "BEFORE"
    file_a = area_root / "a.mp3"
    file_b = area_root / "b.mp3"
    _write_file(file_a)
    _write_file(file_b)

    real_stat = Path.stat

    def fake_stat(self, *args, **kwargs):
        if self.name == "a.mp3":
            raise OSError(errno.ENOENT, "No such file or directory", str(self))
        if self.name == "b.mp3":
            raise OSError(errno.EIO, "Input/output error", str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)

    index_path = tmp_path / "data" / "source_index.csv"
    result = original.scan_area(root, "before", index_path)

    rows = {r["filename"]: r for r in original.load_source_index(index_path)}
    assert rows["a.mp3"]["state"] == "missing"
    assert rows["b.mp3"]["state"] == "error"
    assert rows["b.mp3"]["state"] != "missing"
    assert result["errors"] == 1


# ── 2. partial walk never marks the rest missing ────────────────────────

def test_partial_walk_does_not_mark_rest_as_missing(tmp_path, monkeypatch):
    root = tmp_path / "ORIGINAL"
    area_root = root / "BEFORE"
    for i in range(5):
        _write_file(area_root / f"sub{i}" / "track.mp3")

    index_path = tmp_path / "data" / "source_index.csv"
    # First, clean complete scan so rows pre-exist with state="new".
    original.scan_area(root, "before", index_path)
    before_rows = {r["source_id"]: dict(r) for r in original.load_source_index(index_path)}
    assert all(r["state"] != "missing" for r in before_rows.values())

    real_walk = os.walk
    call_count = {"n": 0}

    def fake_walk(top, *args, **kwargs):
        for entry in real_walk(top, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] > 2:
                raise OSError(errno.EIO, "Simulated NAS I/O error")
            yield entry

    monkeypatch.setattr(original.os, "walk", fake_walk)

    result = original.scan_area(root, "before", index_path)
    assert result["complete"] is False
    assert result["walk_error"]

    after_rows = {r["source_id"]: r for r in original.load_source_index(index_path)}
    for source_id, row in before_rows.items():
        assert after_rows[source_id]["state"] != "missing"


# ── 3. checkpoints survive a mid-run crash ──────────────────────────────

def test_scan_checkpoints_survive_midrun_crash(tmp_path, monkeypatch):
    root = tmp_path / "ORIGINAL"
    area_root = root / "BEFORE"
    total_files = 300
    for i in range(total_files):
        _write_file(area_root / f"file_{i:04d}.mp3")

    crash_name = f"file_{total_files - 1:04d}.mp3"
    real_stat = Path.stat

    def fake_stat(self, *args, **kwargs):
        if self.name == crash_name:
            raise RuntimeError("simulated crash")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)

    index_path = tmp_path / "data" / "source_index.csv"
    with pytest.raises(RuntimeError):
        original.scan_area(root, "before", index_path, checkpoint_every=200)

    rows = original.load_source_index(index_path)
    assert len(rows) >= 200


# ── 4. rescan preserves fingerprint/state/batch_id/dup_of ──────────────

def test_rescan_preserves_fingerprint_and_state(tmp_path):
    root = tmp_path / "ORIGINAL"
    area_root = root / "BEFORE"
    _write_file(area_root / "track.mp3")

    index_path = tmp_path / "data" / "source_index.csv"
    original.scan_area(root, "before", index_path)

    rows = original.load_source_index(index_path)
    assert len(rows) == 1
    rows[0]["fingerprint"] = "abc123"
    rows[0]["state"] = "processed"
    rows[0]["batch_id"] = "batch-1"
    rows[0]["dup_of"] = "some-other-id"
    original.save_source_index(index_path, rows)

    original.scan_area(root, "before", index_path)

    rows_after = original.load_source_index(index_path)
    assert len(rows_after) == 1
    r = rows_after[0]
    assert r["fingerprint"] == "abc123"
    assert r["state"] == "processed"
    assert r["batch_id"] == "batch-1"
    assert r["dup_of"] == "some-other-id"


# ── 5. source_id is stable across NFC/NFD ───────────────────────────────

def test_source_id_stable_across_nfc_nfd(tmp_path):
    nfc_name = unicodedata.normalize("NFC", "plaża.mp3")
    nfd_name = unicodedata.normalize("NFD", "plaża.mp3")
    assert nfc_name != nfd_name  # sanity: the two forms really differ byte-wise

    id_nfc = original.make_source_id("before", nfc_name)
    id_nfd = original.make_source_id("before", nfd_name)
    assert id_nfc == id_nfd

    root = tmp_path / "ORIGINAL"
    area_root = root / "BEFORE"
    _write_file(area_root / nfc_name)

    index_path = tmp_path / "data" / "source_index.csv"
    original.scan_area(root, "before", index_path)
    original.scan_area(root, "before", index_path)

    rows = original.load_source_index(index_path)
    assert len(rows) == 1


# ── 6. fingerprint pass skips already-fingerprinted rows ────────────────

def test_fingerprint_pass_skips_already_fingerprinted(tmp_path):
    root = tmp_path / "ORIGINAL"
    area_root = root / "BEFORE"
    done_file = area_root / "done.mp3"
    pending_file = area_root / "pending.mp3"
    _write_file(done_file, b"x" * 10)
    _write_file(pending_file, b"x" * 10)

    st_done = done_file.stat()
    st_pending = pending_file.stat()

    rows = [
        {
            **{k: "" for k in original.SOURCE_INDEX_FIELDNAMES},
            "source_id": original.make_source_id("before", "done.mp3"),
            "area": "before",
            "rel_path": "done.mp3",
            "filename": "done.mp3",
            "fingerprint": "already-there",
            "size_bytes": str(st_done.st_size),
            "mtime": original._mtime_to_iso(st_done.st_mtime),
        },
        {
            **{k: "" for k in original.SOURCE_INDEX_FIELDNAMES},
            "source_id": original.make_source_id("before", "pending.mp3"),
            "area": "before",
            "rel_path": "pending.mp3",
            "filename": "pending.mp3",
            "fingerprint": "",
            "size_bytes": str(st_pending.st_size),
            "mtime": original._mtime_to_iso(st_pending.st_mtime),
        },
    ]
    index_path = tmp_path / "data" / "source_index.csv"
    original.save_source_index(index_path, rows)

    with patch.object(original, "fingerprint_info", return_value=(120, "NEWFP")) as mock_fp:
        result = original.fingerprint_area(root, index_path)

    assert mock_fp.call_count == 1
    assert mock_fp.call_args[0][0] == pending_file
    assert result["total"] == 1

    rows_after = {r["rel_path"]: r for r in original.load_source_index(index_path)}
    assert rows_after["done.mp3"]["fingerprint"] == "already-there"
    assert rows_after["pending.mp3"]["fingerprint"] == "NEWFP"


# ── 7. fingerprint pass recomputes on size/mtime drift ──────────────────

def test_fingerprint_pass_recomputes_when_size_or_mtime_changed(tmp_path):
    root = tmp_path / "ORIGINAL"
    area_root = root / "BEFORE"
    stale_file = area_root / "stale.mp3"
    _write_file(stale_file, b"x" * 10)

    rows = [
        {
            **{k: "" for k in original.SOURCE_INDEX_FIELDNAMES},
            "source_id": original.make_source_id("before", "stale.mp3"),
            "area": "before",
            "rel_path": "stale.mp3",
            "filename": "stale.mp3",
            "fingerprint": "old-fp",
            "size_bytes": "0",  # deliberately wrong, simulates drift since last fingerprint
            "mtime": "1970-01-01T00:00:00+00:00",
        },
    ]
    index_path = tmp_path / "data" / "source_index.csv"
    original.save_source_index(index_path, rows)

    with patch.object(original, "fingerprint_info", return_value=(120, "RECOMPUTED")) as mock_fp:
        result = original.fingerprint_area(root, index_path)

    assert mock_fp.call_count == 1
    assert result["total"] == 1
    rows_after = original.load_source_index(index_path)
    assert rows_after[0]["fingerprint"] == "RECOMPUTED"


# ── 8. fpcalc timeout doesn't crash the process ─────────────────────────

def test_fingerprint_timeout_marks_status_not_crash(tmp_path):
    root = tmp_path / "ORIGINAL"
    area_root = root / "BEFORE"
    _write_file(area_root / "slow.mp3")

    rows = [
        {
            **{k: "" for k in original.SOURCE_INDEX_FIELDNAMES},
            "source_id": original.make_source_id("before", "slow.mp3"),
            "area": "before",
            "rel_path": "slow.mp3",
            "filename": "slow.mp3",
            "fingerprint": "",
        },
    ]
    index_path = tmp_path / "data" / "source_index.csv"
    original.save_source_index(index_path, rows)

    timeout_exc = subprocess.TimeoutExpired(cmd="fpcalc", timeout=120)
    with patch.object(original, "fingerprint_info", side_effect=timeout_exc):
        result = original.fingerprint_area(root, index_path)

    assert result["timeout"] == 1
    rows_after = original.load_source_index(index_path)
    assert rows_after[0]["fp_status"] == "timeout"


# ── 9. #recycle and dotfiles are skipped ────────────────────────────────

def test_scan_skips_recycle_and_dotfiles(tmp_path):
    root = tmp_path / "ORIGINAL"
    area_root = root / "BEFORE"
    _write_file(area_root / "visible.mp3")
    _write_file(area_root / "#recycle" / "deleted.mp3")
    _write_file(area_root / ".hidden.mp3")
    _write_file(area_root / ".hidden_dir" / "also_hidden.mp3")

    index_path = tmp_path / "data" / "source_index.csv"
    original.scan_area(root, "before", index_path)

    rows = original.load_source_index(index_path)
    assert len(rows) == 1
    assert rows[0]["filename"] == "visible.mp3"


# ── 10. dry-run writes nothing to disk ──────────────────────────────────

def test_dry_run_writes_nothing(tmp_path):
    root = tmp_path / "ORIGINAL"
    area_root = root / "BEFORE"
    _write_file(area_root / "track.mp3", b"x" * 10)

    index_path = tmp_path / "data" / "source_index.csv"

    before = _snapshot(tmp_path)
    result = original.scan_area(root, "before", index_path, dry_run=True)
    after = _snapshot(tmp_path)
    assert before == after
    assert result["scanned"] == 1
    assert not index_path.exists()

    # Fingerprint dry-run: needs an existing index to act on, but must not
    # touch the CSV or the NAS files.
    original.scan_area(root, "before", index_path)
    rows = original.load_source_index(index_path)
    rows[0]["fingerprint"] = ""
    original.save_source_index(index_path, rows)

    before_fp = _snapshot(tmp_path)
    with patch.object(original, "fingerprint_info", return_value=(120, "SHOULDNOTPERSIST")):
        fp_result = original.fingerprint_area(root, index_path, dry_run=True)
    after_fp = _snapshot(tmp_path)
    assert before_fp == after_fp
    assert fp_result["total"] == 1

    rows_after = original.load_source_index(index_path)
    assert rows_after[0]["fingerprint"] == ""
