"""Unit tests for djlib.cli._merge_playlists."""
from __future__ import annotations

from djlib.cli import _merge_playlists


def test_union_adds_new_playlist():
    assert _merge_playlists("PornoStar", "UltimateSet") == "PornoStar|UltimateSet"


def test_union_deduplicates():
    assert _merge_playlists("PornoStar|UltimateSet", "PornoStar") == "PornoStar|UltimateSet"


def test_empty_new_keeps_prev():
    assert _merge_playlists("PornoStar|UltimateSet", "") == "PornoStar|UltimateSet"


def test_empty_both_stays_empty():
    assert _merge_playlists("", "") == ""


def test_clear_sentinel_removes_all():
    assert _merge_playlists("PornoStar|UltimateSet", "CLEAR") == ""


def test_clear_sentinel_case_insensitive():
    assert _merge_playlists("PornoStar", "clear") == ""
    assert _merge_playlists("PornoStar", "Clear") == ""


def test_clear_with_prev_empty():
    assert _merge_playlists("", "CLEAR") == ""


def test_new_only_no_prev():
    assert _merge_playlists("", "UltimateSet") == "UltimateSet"


def test_pipe_separated_new_playlists():
    assert _merge_playlists("A", "B|C") == "A|B|C"


def test_preserves_order_prev_first():
    result = _merge_playlists("Alpha", "Beta")
    assert result.startswith("Alpha")
