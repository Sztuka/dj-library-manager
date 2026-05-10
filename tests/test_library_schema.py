"""Tests for djlib.library_schema — atomic write, backup, rotation, sidecar."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from djlib.library_schema import (
    DEFAULT_BACKUP_RETENTION,
    DJLIB_OWNED_FIELDS,
    LIBRARY_FIELDNAMES,
    LIBRARY_SCHEMA_VERSION,
    merge_with_existing_library,
    save_library_csv,
)


def _read_csv(p: Path) -> list[dict[str, str]]:
    with p.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_first_write_creates_csv_and_sidecar(tmp_path: Path) -> None:
    dest = tmp_path / "library.csv"
    rows = [{"track_id": "abc", "artist": "Hot Chip", "title": "Down"}]
    backup = save_library_csv(dest, rows)
    assert backup is None  # nothing to back up on first write
    assert dest.exists()
    sidecar = dest.with_suffix(".schema.json")
    assert sidecar.exists()
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    assert meta["schema_version"] == LIBRARY_SCHEMA_VERSION
    assert meta["row_count"] == 1
    assert meta["fieldnames"] == LIBRARY_FIELDNAMES
    got = _read_csv(dest)
    assert len(got) == 1
    assert got[0]["track_id"] == "abc"
    assert got[0]["artist"] == "Hot Chip"


def test_second_write_creates_timestamped_backup(tmp_path: Path) -> None:
    dest = tmp_path / "library.csv"
    save_library_csv(dest, [{"track_id": "v1"}])
    backup = save_library_csv(dest, [{"track_id": "v2"}])
    assert backup is not None
    assert backup.parent == tmp_path / "backups"
    assert backup.name.startswith("library-")
    # Backup holds v1, live holds v2
    assert _read_csv(backup)[0]["track_id"] == "v1"
    assert _read_csv(dest)[0]["track_id"] == "v2"


def test_backup_rotation_keeps_only_last_N(tmp_path: Path) -> None:
    dest = tmp_path / "library.csv"
    # Write initial file, then overwrite 5 times with retention=3
    save_library_csv(dest, [{"track_id": "seed"}], backup_retention=3)
    # Stub timestamp so every backup gets a unique name even on fast FS
    stamps = iter(f"20260101-00000{i}" for i in range(1, 7))
    with patch("djlib.library_schema._now_timestamp", lambda: next(stamps)):
        for i in range(5):
            save_library_csv(dest, [{"track_id": f"v{i}"}], backup_retention=3)
    backups = sorted((tmp_path / "backups").iterdir())
    assert len(backups) == 3, [b.name for b in backups]


def test_unknown_fields_dropped_by_default(tmp_path: Path) -> None:
    dest = tmp_path / "library.csv"
    save_library_csv(dest, [{"track_id": "x", "not_a_real_field": "ignored"}])
    header = _read_csv(dest)[0]
    assert "not_a_real_field" not in header


def test_extra_fieldnames_preserved(tmp_path: Path) -> None:
    dest = tmp_path / "library.csv"
    save_library_csv(
        dest,
        [{"track_id": "x", "legacy_col": "keep"}],
        extra_fieldnames=["legacy_col"],
    )
    got = _read_csv(dest)[0]
    assert got["legacy_col"] == "keep"


def test_missing_fields_written_as_empty_string(tmp_path: Path) -> None:
    dest = tmp_path / "library.csv"
    save_library_csv(dest, [{"track_id": "x"}])
    row = _read_csv(dest)[0]
    # Every canonical field exists, even if empty
    for field in LIBRARY_FIELDNAMES:
        assert field in row
    assert row["artist"] == ""
    assert row["bpm"] == ""


def test_none_values_become_empty_string(tmp_path: Path) -> None:
    dest = tmp_path / "library.csv"
    save_library_csv(dest, [{"track_id": "x", "artist": None, "bpm": None}])
    row = _read_csv(dest)[0]
    assert row["artist"] == ""
    assert row["bpm"] == ""


def test_write_failure_does_not_corrupt_existing(tmp_path: Path) -> None:
    """If the write fails mid-flight, the original file must survive intact."""
    dest = tmp_path / "library.csv"
    save_library_csv(dest, [{"track_id": "original", "artist": "Kept"}])
    original_bytes = dest.read_bytes()

    # Force the writer to explode *after* writing some bytes but *before*
    # os.replace runs. `os.fsync` is a convenient chokepoint.
    with patch("djlib.library_schema.os.fsync", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            save_library_csv(dest, [{"track_id": "corrupted"}])

    # Original file untouched, no .tmp junk left lying around.
    # `.library.csv.lock` is expected (csv_lock sidecar, intentional).
    assert dest.read_bytes() == original_bytes
    leftovers = [
        p for p in tmp_path.iterdir()
        if p.name.startswith(".library.csv.") and not p.name.endswith(".lock")
    ]
    assert leftovers == []


def test_no_tmp_file_leaks_on_success(tmp_path: Path) -> None:
    dest = tmp_path / "library.csv"
    save_library_csv(dest, [{"track_id": "x"}])
    # `.library.csv.lock` is expected (csv_lock sidecar, intentional).
    leftovers = [
        p.name for p in tmp_path.iterdir()
        if p.name.startswith(".library.csv.") and not p.name.endswith(".lock")
    ]
    assert leftovers == []


def test_atomic_replace_single_step(tmp_path: Path, monkeypatch) -> None:
    """Verify we call `os.replace`, not a non-atomic alternative."""
    dest = tmp_path / "library.csv"
    save_library_csv(dest, [{"track_id": "a"}])
    calls: list[tuple] = []
    real_replace = os.replace

    def spy(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr("djlib.library_schema.os.replace", spy)
    save_library_csv(dest, [{"track_id": "b"}])
    # At least one replace landed on the live CSV path
    assert any(dst == str(dest) for _, dst in calls), calls


def test_sidecar_updated_on_rewrite(tmp_path: Path) -> None:
    dest = tmp_path / "library.csv"
    save_library_csv(dest, [{"track_id": "one"}])
    meta1 = json.loads(dest.with_suffix(".schema.json").read_text(encoding="utf-8"))
    save_library_csv(dest, [{"track_id": "one"}, {"track_id": "two"}])
    meta2 = json.loads(dest.with_suffix(".schema.json").read_text(encoding="utf-8"))
    assert meta1["row_count"] == 1
    assert meta2["row_count"] == 2


def test_default_retention_is_sane() -> None:
    assert DEFAULT_BACKUP_RETENTION >= 5
    assert DEFAULT_BACKUP_RETENTION <= 100


# ── field_sources carryover tests ────────────────────────────────────────────

def test_field_sources_in_schema() -> None:
    """field_sources must be declared in LIBRARY_FIELDNAMES and DJLIB_OWNED_FIELDS."""
    assert "field_sources" in LIBRARY_FIELDNAMES
    assert "field_sources" in DJLIB_OWNED_FIELDS


def test_field_sources_survives_merge() -> None:
    """merge_with_existing_library carries field_sources from existing row."""
    existing = [
        {
            "track_id": "abc",
            "artist": "Dixon",
            "title": "Tranquilizer",
            "file_hash": "deadbeef",
            "field_sources": json.dumps({"genre": "ai_classifier:nano+WS+LF", "year": "manual"}),
        }
    ]
    # Simulated fresh RB/Traktor snapshot: same track_id, no field_sources
    new_rows = [
        {
            "track_id": "abc",
            "artist": "Dixon",
            "title": "Tranquilizer",
            "rekordbox_id": "1234",
            "field_sources": "",
        }
    ]
    merged = merge_with_existing_library(new_rows, existing)
    assert len(merged) == 1
    assert merged[0]["field_sources"] == json.dumps(
        {"genre": "ai_classifier:nano+WS+LF", "year": "manual"}
    )


def test_field_sources_not_overwritten_by_empty_existing() -> None:
    """field_sources from new sync row should not be overwritten if existing is empty."""
    existing = [
        {"track_id": "xyz", "artist": "Burial", "field_sources": ""}
    ]
    new_rows = [
        {
            "track_id": "xyz",
            "artist": "Burial",
            "field_sources": json.dumps({"genre": "manual"}),
        }
    ]
    merged = merge_with_existing_library(new_rows, existing)
    # Existing is empty string → new value should win (DJLIB_OWNED_FIELDS only
    # carries over non-empty existing values)
    assert merged[0]["field_sources"] == json.dumps({"genre": "manual"})


def test_field_sources_round_trip_through_save(tmp_path: Path) -> None:
    """field_sources written via save_library_csv is readable back."""
    dest = tmp_path / "library.csv"
    payload = json.dumps({"genre": "ai_classifier:nano+WS+LF", "year": "musicbrainz"})
    rows = [{"track_id": "t1", "field_sources": payload}]
    save_library_csv(dest, rows)
    got = _read_csv(dest)
    assert got[0]["field_sources"] == payload
