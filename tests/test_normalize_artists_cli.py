"""Tests for dj normalize-artists CLI command."""
from __future__ import annotations

import csv
import io
import tempfile
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch, MagicMock

import pytest

from djlib.cli import _merge_playlists  # sanity-import to verify cli loads


# ── helpers ───────────────────────────────────────────────────────────────────

def _run_normalize(argv: List[str], input_text: str = "") -> str:
    """Run `dj normalize-artists <argv>` and capture stdout."""
    import sys
    from djlib.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["normalize-artists"] + argv)

    buf = io.StringIO()
    with patch("sys.stdout", buf), patch("builtins.input", side_effect=input_text.splitlines()):
        try:
            args.func(args)
        except SystemExit:
            pass
    return buf.getvalue()


def _fake_cluster(members, confidence=80, method="fuzzy", canonical=None):
    return {
        "members": members,
        "confidence": confidence,
        "method": method,
        "canonical": canonical or members[0],
        "fingerprint": "|".join(sorted(m.lower() for m in members)),
        "track_count": len(members),
    }


# ── dry-run output ────────────────────────────────────────────────────────────

def test_dry_run_lists_clusters(tmp_path):
    clusters = [
        _fake_cluster(["Fatman Scoop", "Fat Man Scoop"], confidence=96),
        _fake_cluster(["The Tremeloes", "Tremeloes"], confidence=82),
    ]
    empty_aliases = {"canonical": {}, "dismissed": [], "pending": {}}

    with patch("djlib.library_schema.load_library_csv", return_value=[]), \
         patch("djlib.cli._load_unsorted", return_value=[]), \
         patch("djlib.artist_normalizer.load_aliases", return_value=empty_aliases), \
         patch("djlib.artist_normalizer.collect_artists", return_value=[]), \
         patch("djlib.artist_normalizer.cluster_artists", return_value=clusters), \
         patch("djlib.cli.CSV_PATH", tmp_path / "library.csv"), \
         patch("djlib.cli.UNSORTED_CSV", tmp_path / "unsorted.csv"):
        out = _run_normalize(["--dry-run"])

    assert "2 cluster" in out
    assert "Fatman Scoop" in out
    assert "Fat Man Scoop" in out
    assert "DRY-RUN" in out
    assert "No changes made" in out


def test_dry_run_no_clusters(tmp_path):
    with patch("djlib.library_schema.load_library_csv", return_value=[]), \
         patch("djlib.cli._load_unsorted", return_value=[]), \
         patch("djlib.artist_normalizer.load_aliases", return_value={"canonical": {}, "dismissed": [], "pending": {}}), \
         patch("djlib.artist_normalizer.collect_artists", return_value=[]), \
         patch("djlib.artist_normalizer.cluster_artists", return_value=[]), \
         patch("djlib.cli.CSV_PATH", tmp_path / "library.csv"), \
         patch("djlib.cli.UNSORTED_CSV", tmp_path / "unsorted.csv"):
        out = _run_normalize(["--dry-run"])

    assert "clean" in out.lower()


# ── confidence sorting ────────────────────────────────────────────────────────

def test_dry_run_sorted_by_confidence_descending(tmp_path):
    clusters = [
        _fake_cluster(["A", "B"], confidence=70),
        _fake_cluster(["X", "Y"], confidence=96),
        _fake_cluster(["P", "Q"], confidence=82),
    ]
    with patch("djlib.library_schema.load_library_csv", return_value=[]), \
         patch("djlib.cli._load_unsorted", return_value=[]), \
         patch("djlib.artist_normalizer.load_aliases", return_value={"canonical": {}, "dismissed": [], "pending": {}}), \
         patch("djlib.artist_normalizer.collect_artists", return_value=[]), \
         patch("djlib.artist_normalizer.cluster_artists", return_value=clusters), \
         patch("djlib.cli.CSV_PATH", tmp_path / "library.csv"), \
         patch("djlib.cli.UNSORTED_CSV", tmp_path / "unsorted.csv"):
        out = _run_normalize(["--dry-run"])

    # [1] should be the 96% cluster, [3] the 70%
    lines = out.splitlines()
    first_cluster = next(l for l in lines if l.startswith("[1/"))
    last_cluster  = next(l for l in lines if l.startswith("[3/"))
    assert "96%" in first_cluster
    assert "70%" in last_cluster


# ── confidence display rounded ────────────────────────────────────────────────

def test_confidence_displayed_as_integer(tmp_path):
    clusters = [_fake_cluster(["A", "B"], confidence=72.72727272)]
    with patch("djlib.library_schema.load_library_csv", return_value=[]), \
         patch("djlib.cli._load_unsorted", return_value=[]), \
         patch("djlib.artist_normalizer.load_aliases", return_value={"canonical": {}, "dismissed": [], "pending": {}}), \
         patch("djlib.artist_normalizer.collect_artists", return_value=[]), \
         patch("djlib.artist_normalizer.cluster_artists", return_value=clusters), \
         patch("djlib.cli.CSV_PATH", tmp_path / "library.csv"), \
         patch("djlib.cli.UNSORTED_CSV", tmp_path / "unsorted.csv"):
        out = _run_normalize(["--dry-run"])

    assert "72.7" not in out  # no ugly float
    assert "73%" in out        # rounded


# ── argparse wiring ───────────────────────────────────────────────────────────

def test_argparse_defaults():
    from djlib.cli import build_parser
    args = build_parser().parse_args(["normalize-artists"])
    assert args.dry_run is False
    assert args.auto is False
    assert args.threshold == 70
    assert args.min_confidence == 100
    assert args.show_dismissed is False


def test_argparse_flags():
    from djlib.cli import build_parser
    args = build_parser().parse_args([
        "normalize-artists", "--dry-run", "--auto", "--threshold", "85",
        "--min-confidence", "80", "--show-dismissed",
    ])
    assert args.dry_run is True
    assert args.auto is True
    assert args.threshold == 85
    assert args.min_confidence == 80
    assert args.show_dismissed is True
