"""Tests for `dj backfill-fingerprints` — computes missing fingerprint values in library.csv."""
from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import patch

from djlib.cli import build_parser
from djlib.library_schema import load_library_csv


def _write_library(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = sorted({k for r in rows for k in r})
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def _run_backfill(argv: list[str]) -> None:
    parser = build_parser()
    args = parser.parse_args(["backfill-fingerprints"] + argv)
    args.func(args)


def test_skips_rows_with_existing_fingerprint(tmp_path):
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"fake audio")
    lib = tmp_path / "library.csv"
    _write_library(
        lib,
        [{"track_id": "t1", "file_path": str(audio), "fingerprint": "already-set"}],
    )

    with patch("djlib.cli.CSV_PATH", lib), \
         patch("djlib.cli.fingerprint_info") as mock_fp:
        _run_backfill([])

    mock_fp.assert_not_called()
    rows = load_library_csv(lib)
    assert rows[0]["fingerprint"] == "already-set"


def test_dry_run_does_not_write(tmp_path):
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"fake audio")
    lib = tmp_path / "library.csv"
    _write_library(lib, [{"track_id": "t1", "file_path": str(audio), "fingerprint": ""}])

    with patch("djlib.cli.CSV_PATH", lib), \
         patch("djlib.cli.fingerprint_info", return_value=(180, "AQADnew")):
        _run_backfill(["--dry-run"])

    rows = load_library_csv(lib)
    assert rows[0]["fingerprint"] == ""


def test_computes_and_saves_fingerprint(tmp_path):
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"fake audio")
    lib = tmp_path / "library.csv"
    _write_library(lib, [{"track_id": "t1", "file_path": str(audio), "fingerprint": ""}])

    with patch("djlib.cli.CSV_PATH", lib), \
         patch("djlib.cli.fingerprint_info", return_value=(180, "AQADnew")):
        _run_backfill([])

    rows = load_library_csv(lib)
    assert rows[0]["fingerprint"] == "AQADnew"


def test_missing_file_does_not_crash(tmp_path):
    lib = tmp_path / "library.csv"
    _write_library(
        lib,
        [{"track_id": "t1", "file_path": str(tmp_path / "nope.mp3"), "fingerprint": ""}],
    )

    with patch("djlib.cli.CSV_PATH", lib), \
         patch("djlib.cli.fingerprint_info") as mock_fp:
        _run_backfill([])

    mock_fp.assert_not_called()
    rows = load_library_csv(lib)
    assert rows[0]["fingerprint"] == ""


def test_falls_back_to_original_path_then_old_full_path(tmp_path):
    audio = tmp_path / "original.mp3"
    audio.write_bytes(b"fake audio")
    lib = tmp_path / "library.csv"
    _write_library(
        lib,
        [
            {
                "track_id": "t1",
                "file_path": "",
                "original_path": str(audio),
                "old_full_path": "",
                "fingerprint": "",
            }
        ],
    )

    with patch("djlib.cli.CSV_PATH", lib), \
         patch("djlib.cli.fingerprint_info", return_value=(180, "AQADfallback")):
        _run_backfill([])

    rows = load_library_csv(lib)
    assert rows[0]["fingerprint"] == "AQADfallback"


def test_limit_caps_number_of_rows_processed(tmp_path):
    lib = tmp_path / "library.csv"
    rows = []
    for i in range(3):
        audio = tmp_path / f"t{i}.mp3"
        audio.write_bytes(b"fake audio")
        rows.append({"track_id": f"t{i}", "file_path": str(audio), "fingerprint": ""})
    _write_library(lib, rows)

    with patch("djlib.cli.CSV_PATH", lib), \
         patch("djlib.cli.fingerprint_info", return_value=(180, "AQADlimited")) as mock_fp:
        _run_backfill(["--limit", "1"])

    assert mock_fp.call_count == 1
