from __future__ import annotations

import hashlib
import os
import unicodedata
from pathlib import Path

import pytest

from djlib import original
from djlib.csvdb import save_records, save_rejected


def _write_file(path: Path, content: bytes = b"") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _blank_row(source_id: str, area: str, rel_path: str, **overrides: str) -> dict:
    row = {k: "" for k in original.SOURCE_INDEX_FIELDNAMES}
    row["source_id"] = source_id
    row["area"] = area
    row["rel_path"] = rel_path
    row["filename"] = rel_path.rsplit("/", 1)[-1]
    row.update(overrides)
    return row


def _snapshot(root: Path) -> dict:
    snap = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            p = Path(dirpath) / name
            st = p.stat()
            snap[str(p)] = (st.st_mtime_ns, st.st_size, hashlib.sha256(p.read_bytes()).hexdigest())
    return snap


# ── original-match ───────────────────────────────────────────────────────


def test_match_fills_in_library_from_fingerprint(tmp_path):
    index_path = tmp_path / "data" / "source_index.csv"
    library_csv = tmp_path / "data" / "library.csv"
    rejected_csv = tmp_path / "data" / "library-rejected.csv"

    row = _blank_row(
        original.make_source_id("before", "a.mp3"), "before", "a.mp3", fingerprint="FP1"
    )
    original.save_source_index(index_path, [row])
    save_records(library_csv, [{"track_id": "tid-123", "fingerprint": "FP1"}])
    save_rejected(rejected_csv, [])

    result = original.match_source_index(index_path, library_csv, rejected_csv)

    rows = original.load_source_index(index_path)
    assert rows[0]["in_library"] == "tid-123"
    assert result["in_library"] == 1


def test_match_marks_rejected_distinguishably(tmp_path):
    index_path = tmp_path / "data" / "source_index.csv"
    library_csv = tmp_path / "data" / "library.csv"
    rejected_csv = tmp_path / "data" / "library-rejected.csv"

    row = _blank_row(
        original.make_source_id("before", "b.mp3"), "before", "b.mp3", fingerprint="FP2"
    )
    original.save_source_index(index_path, [row])
    save_records(library_csv, [])
    save_rejected(rejected_csv, [{"file_hash": "hash-xyz", "fingerprint": "FP2"}])

    result = original.match_source_index(index_path, library_csv, rejected_csv)

    rows = original.load_source_index(index_path)
    assert rows[0]["in_library"] == "rejected:hash-xyz"
    assert rows[0]["in_library"] != "hash-xyz"
    assert result["rejected"] == 1


def test_match_dup_winner_prefers_after_then_larger_size(tmp_path):
    index_path = tmp_path / "data" / "source_index.csv"
    library_csv = tmp_path / "data" / "library.csv"
    rejected_csv = tmp_path / "data" / "library-rejected.csv"
    save_records(library_csv, [])
    save_rejected(rejected_csv, [])

    # Rule 1: after beats before regardless of size.
    before_row = _blank_row(
        original.make_source_id("before", "x.mp3"), "before", "x.mp3",
        fingerprint="DUPFP", size_bytes="9000000",
    )
    after_row = _blank_row(
        original.make_source_id("after", "x.mp3"), "after", "x.mp3",
        fingerprint="DUPFP", size_bytes="1000",
    )
    original.save_source_index(index_path, [before_row, after_row])
    original.match_source_index(index_path, library_csv, rejected_csv)
    rows = {r["area"]: r for r in original.load_source_index(index_path)}
    assert rows["after"]["dup_of"] == ""
    assert rows["before"]["dup_of"] == after_row["source_id"]

    # Rule 2: within the same area, larger size_bytes wins.
    small_row = _blank_row(
        original.make_source_id("before", "y1.mp3"), "before", "y1.mp3",
        fingerprint="DUPFP2", size_bytes="100",
    )
    large_row = _blank_row(
        original.make_source_id("before", "y2.mp3"), "before", "y2.mp3",
        fingerprint="DUPFP2", size_bytes="900",
    )
    original.save_source_index(index_path, [small_row, large_row])
    original.match_source_index(index_path, library_csv, rejected_csv)
    rows2 = {r["rel_path"]: r for r in original.load_source_index(index_path)}
    assert rows2["y2.mp3"]["dup_of"] == ""
    assert rows2["y1.mp3"]["dup_of"] == large_row["source_id"]


def test_match_is_idempotent(tmp_path):
    index_path = tmp_path / "data" / "source_index.csv"
    library_csv = tmp_path / "data" / "library.csv"
    rejected_csv = tmp_path / "data" / "library-rejected.csv"

    row_a = _blank_row(
        original.make_source_id("before", "a.mp3"), "before", "a.mp3",
        fingerprint="FP1", size_bytes="100",
    )
    row_b = _blank_row(
        original.make_source_id("before", "a2.mp3"), "before", "a2.mp3",
        fingerprint="FP1", size_bytes="50",
    )
    original.save_source_index(index_path, [row_a, row_b])
    save_records(library_csv, [{"track_id": "tid-1", "fingerprint": "FP1"}])
    save_rejected(rejected_csv, [])

    original.match_source_index(index_path, library_csv, rejected_csv)
    first_pass = original.load_source_index(index_path)

    original.match_source_index(index_path, library_csv, rejected_csv)
    second_pass = original.load_source_index(index_path)

    assert first_pass == second_pass


def test_match_resets_stale_flags(tmp_path):
    index_path = tmp_path / "data" / "source_index.csv"
    library_csv = tmp_path / "data" / "library.csv"
    rejected_csv = tmp_path / "data" / "library-rejected.csv"

    row = _blank_row(
        original.make_source_id("before", "c.mp3"), "before", "c.mp3",
        fingerprint="",  # fingerprint gone / never had one — dup partner no longer exists
        dup_of="some-stale-id",
        in_library="some-stale-track-id",
    )
    original.save_source_index(index_path, [row])
    save_records(library_csv, [])
    save_rejected(rejected_csv, [])

    original.match_source_index(index_path, library_csv, rejected_csv)

    rows = original.load_source_index(index_path)
    assert rows[0]["dup_of"] == ""
    assert rows[0]["in_library"] == ""


# ── original-archive ─────────────────────────────────────────────────────


def test_archive_defaults_to_dry_run(tmp_path):
    root = tmp_path / "ORIGINAL"
    _write_file(root / "BEFORE" / "a.mp3", b"hello world")
    index_path = tmp_path / "data" / "source_index.csv"
    logs_dir = tmp_path / "LOGS"
    logs_dir.mkdir()

    row = _blank_row(
        original.make_source_id("before", "a.mp3"), "before", "a.mp3",
        in_library="tid-1", state="new",
    )
    original.save_source_index(index_path, [row])

    before_tree = _snapshot(tmp_path)
    result = original.archive_before_to_after(root, index_path, execute=False)
    after_tree = _snapshot(tmp_path)

    assert before_tree == after_tree
    assert result["execute"] is False
    assert result["qualified"] == 1
    assert list(logs_dir.iterdir()) == []


def test_archive_skips_sent_but_unprocessed(tmp_path):
    root = tmp_path / "ORIGINAL"
    _write_file(root / "BEFORE" / "sent.mp3", b"content")
    index_path = tmp_path / "data" / "source_index.csv"

    row = _blank_row(
        original.make_source_id("before", "sent.mp3"), "before", "sent.mp3",
        state="sent", in_library="",
    )
    original.save_source_index(index_path, [row])

    result = original.archive_before_to_after(root, index_path, execute=False)
    assert result["qualified"] == 0
    assert result["plan"] == []


def test_archive_moves_only_qualified_with_execute(tmp_path):
    root = tmp_path / "ORIGINAL"
    _write_file(root / "BEFORE" / "qualified.mp3", b"aaa")
    _write_file(root / "BEFORE" / "sent.mp3", b"bbb")
    index_path = tmp_path / "data" / "source_index.csv"

    qualified_row = _blank_row(
        original.make_source_id("before", "qualified.mp3"), "before", "qualified.mp3",
        in_library="tid-1", state="new",
    )
    sent_row = _blank_row(
        original.make_source_id("before", "sent.mp3"), "before", "sent.mp3",
        state="sent", in_library="",
    )
    original.save_source_index(index_path, [qualified_row, sent_row])

    result = original.archive_before_to_after(root, index_path, execute=True)

    assert result["moved"] == 1
    assert result["errors"] == 0
    assert (root / "AFTER" / "qualified.mp3").is_file()
    assert not (root / "BEFORE" / "qualified.mp3").exists()
    assert (root / "BEFORE" / "sent.mp3").is_file()  # untouched

    rows = {r["rel_path"]: r for r in original.load_source_index(index_path)}
    moved = rows["qualified.mp3"]
    assert moved["area"] == "after"
    assert moved["state"] == "processed"
    assert moved["source_id"] == original.make_source_id("after", "qualified.mp3")


def test_archive_collision_in_after_does_not_overwrite(tmp_path):
    root = tmp_path / "ORIGINAL"
    nfd_name = unicodedata.normalize("NFD", "café.mp3")
    _write_file(root / "AFTER" / nfd_name, b"existing-nas-content")
    _write_file(root / "BEFORE" / "café.mp3", b"new-content")

    index_path = tmp_path / "data" / "source_index.csv"
    row = _blank_row(
        original.make_source_id("before", "café.mp3"), "before", "café.mp3",
        in_library="tid-1", state="new",
    )
    original.save_source_index(index_path, [row])

    result = original.archive_before_to_after(root, index_path, execute=True)

    assert result["moved"] == 1
    existing = (root / "AFTER" / nfd_name)
    assert existing.is_file()
    assert existing.read_bytes() == b"existing-nas-content"

    after_files = sorted(p.name for p in (root / "AFTER").iterdir())
    assert len(after_files) == 2  # original + renamed copy, no overwrite


def test_archive_ignores_test_move_logs(tmp_path):
    root = tmp_path / "ORIGINAL"
    _write_file(root / "BEFORE" / "z.mp3", b"content")
    index_path = tmp_path / "data" / "source_index.csv"
    logs_dir = tmp_path / "LOGS"
    logs_dir.mkdir()
    (logs_dir / "moves-20251129-120000-TEST.csv").write_text(
        "track_id,src,dest\ntest_abc,~/Desktop/80s mix/z.mp3,~/Music Archive/z.mp3\n"
    )

    row = _blank_row(
        original.make_source_id("before", "z.mp3"), "before", "z.mp3",
        state="sent", in_library="",  # poisoned log must not make this qualify
    )
    original.save_source_index(index_path, [row])

    result = original.archive_before_to_after(root, index_path, execute=False)
    assert result["qualified"] == 0


def test_archive_partial_failure_continues(tmp_path, monkeypatch):
    root = tmp_path / "ORIGINAL"
    _write_file(root / "BEFORE" / "ok1.mp3", b"a")
    _write_file(root / "BEFORE" / "bad.mp3", b"b")
    _write_file(root / "BEFORE" / "ok2.mp3", b"c")
    index_path = tmp_path / "data" / "source_index.csv"

    rows = [
        _blank_row(original.make_source_id("before", n), "before", n, in_library="tid", state="new")
        for n in ("ok1.mp3", "bad.mp3", "ok2.mp3")
    ]
    original.save_source_index(index_path, rows)

    real_move = original.shutil.move

    def fake_move(src, dst):
        if "bad.mp3" in str(src):
            raise OSError("simulated NAS write failure")
        return real_move(src, dst)

    monkeypatch.setattr(original.shutil, "move", fake_move)

    result = original.archive_before_to_after(root, index_path, execute=True)

    assert result["moved"] == 2
    assert result["errors"] == 1
    assert (root / "AFTER" / "ok1.mp3").is_file()
    assert (root / "AFTER" / "ok2.mp3").is_file()
    assert (root / "BEFORE" / "bad.mp3").is_file()  # left in place after failure
