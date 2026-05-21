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


# ── compound artist preservation ─────────────────────────────────────────────

def test_do_merge_preserves_co_artist(tmp_path):
    """Merging 'dj snake' cluster must not erase 'Martin Garrix' from a collab track."""
    import yaml
    from djlib.cli import build_parser
    from djlib.artist_normalizer import _cluster_fingerprint

    aliases_path = tmp_path / "artist_aliases.yml"
    aliases_path.write_text(
        yaml.safe_dump({"canonical": {}, "dismissed": [], "pending": {}}),
        encoding="utf-8",
    )

    # One CSV row with a compound artist field
    lib_csv = tmp_path / "library.csv"
    unsorted_csv = tmp_path / "unsorted.csv"
    lib_csv.write_text(
        "track_id,artist,title,file_path\n"
        "t1,dj snake & Martin Garrix,Taki Taki,/music/taki.aiff\n",
        encoding="utf-8",
    )
    unsorted_csv.write_text("track_id,artist,title,file_path\n", encoding="utf-8")

    cluster = {
        "members": ["DJ Snake", "dj snake"],
        "confidence": 95,
        "method": "fuzzy",
        "canonical": "DJ Snake",
        "fingerprint": _cluster_fingerprint(["DJ Snake", "dj snake"]),
        "track_count": 1,
    }

    with patch("djlib.library_schema.load_library_csv") as mock_load_lib, \
         patch("djlib.library_schema.save_library_csv") as mock_save_lib, \
         patch("djlib.cli._load_unsorted", return_value=[]), \
         patch("djlib.artist_normalizer.load_aliases") as mock_load_aliases, \
         patch("djlib.artist_normalizer.save_aliases"), \
         patch("djlib.artist_normalizer.write_pending_entry"), \
         patch("djlib.artist_normalizer.promote_pending_to_canonical"), \
         patch("djlib.artist_normalizer.write_artist_tags", return_value=[]), \
         patch("djlib.artist_normalizer.write_audit_log"), \
         patch("djlib.artist_normalizer.collect_artists", return_value=[]), \
         patch("djlib.artist_normalizer.cluster_artists", return_value=[cluster]), \
         patch("djlib.cli.load_unsorted_rows", return_value=[]), \
         patch("djlib.cli.CSV_PATH", lib_csv), \
         patch("djlib.cli.UNSORTED_CSV", unsorted_csv), \
         patch("djlib.cli.LOGS_DIR", tmp_path), \
         patch("djlib.locks.csv_lock") as mock_lock:

        lib_row = {"track_id": "t1", "artist": "dj snake & Martin Garrix",
                   "title": "Taki Taki", "file_path": "/music/taki.aiff"}
        mock_load_lib.return_value = [lib_row]
        mock_load_aliases.return_value = {"canonical": {}, "dismissed": [], "pending": {}}
        mock_lock.return_value.__enter__ = lambda s: s
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        buf = io.StringIO()
        with patch("sys.stdout", buf), patch("builtins.input", return_value=""):
            args = build_parser().parse_args(["normalize-artists"])
            args.func(args)

    # save_library_csv must be called with the updated row
    assert mock_save_lib.called
    saved_rows = mock_save_lib.call_args[0][1]
    saved_artist = saved_rows[0]["artist"]
    assert "Martin Garrix" in saved_artist, f"Co-artist lost: {saved_artist!r}"
    assert "DJ Snake" in saved_artist, f"Canonical missing: {saved_artist!r}"


# ── pending entry rollback on tag failure ─────────────────────────────────────

def test_do_merge_cleans_pending_on_tag_failure(tmp_path):
    """If write_artist_tags fails, the pending WAL entry must be removed."""
    import yaml
    from djlib.cli import build_parser
    from djlib.artist_normalizer import _cluster_fingerprint

    cluster = {
        "members": ["Fatman Scoop", "Fat Man Scoop"],
        "confidence": 95,
        "method": "fuzzy",
        "canonical": "Fatman Scoop",
        "fingerprint": _cluster_fingerprint(["Fatman Scoop", "Fat Man Scoop"]),
        "track_count": 2,
    }

    pending_state: Dict = {"canonical": {}, "dismissed": [], "pending": {}}

    def fake_write_pending(path, fp, canonical, variants):
        pending_state["pending"][fp] = {"canonical": canonical, "variants": variants}

    def fake_save_aliases(path, data):
        pending_state.update(data)

    def fake_load_aliases(path):
        return dict(pending_state)

    with patch("djlib.library_schema.load_library_csv", return_value=[]), \
         patch("djlib.cli._load_unsorted", return_value=[]), \
         patch("djlib.cli.load_unsorted_rows", return_value=[]), \
         patch("djlib.artist_normalizer.load_aliases", side_effect=fake_load_aliases), \
         patch("djlib.artist_normalizer.save_aliases", side_effect=fake_save_aliases), \
         patch("djlib.artist_normalizer.write_pending_entry", side_effect=fake_write_pending), \
         patch("djlib.artist_normalizer.write_artist_tags", return_value=["/bad/file.aiff"]), \
         patch("djlib.artist_normalizer.collect_artists", return_value=[]), \
         patch("djlib.artist_normalizer.cluster_artists", return_value=[cluster]), \
         patch("djlib.cli.CSV_PATH", tmp_path / "library.csv"), \
         patch("djlib.cli.UNSORTED_CSV", tmp_path / "unsorted.csv"), \
         patch("djlib.cli.LOGS_DIR", tmp_path):

        buf = io.StringIO()
        with patch("sys.stdout", buf), patch("builtins.input", return_value=""):
            args = build_parser().parse_args(["normalize-artists"])
            args.func(args)

    assert not pending_state["pending"], (
        f"Pending entry not cleaned up after tag failure: {pending_state['pending']}"
    )


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
