"""Tests for version clustering logic."""
from __future__ import annotations

import pytest
from djlib.versions import version_group_id, extract_version_info, group_versions


# ── version_group_id ──────────────────────────────────────────────────────────

def test_same_base_title_same_group():
    a = version_group_id("New Order", "Blue Monday (Extended Mix)")
    b = version_group_id("New Order", "Blue Monday (Original Mix)")
    assert a == b

def test_different_title_different_group():
    a = version_group_id("New Order", "Blue Monday")
    b = version_group_id("New Order", "Bizarre Love Triangle")
    assert a != b

def test_different_artist_different_group():
    a = version_group_id("New Order", "Blue Monday")
    b = version_group_id("Orgy", "Blue Monday")
    assert a != b

def test_artist_case_insensitive():
    a = version_group_id("New Order", "Blue Monday")
    b = version_group_id("new order", "Blue Monday")
    assert a == b

def test_feat_artist_stripped():
    a = version_group_id("New Order", "Blue Monday")
    b = version_group_id("New Order feat. Someone", "Blue Monday")
    assert a == b

def test_empty_inputs_returns_empty():
    assert version_group_id("", "") == ""


# ── extract_version_info ──────────────────────────────────────────────────────

def test_extracts_parens():
    assert extract_version_info("Blue Monday (Extended Mix)") == "Extended Mix"

def test_extracts_brackets():
    assert extract_version_info("Blue Monday [Radio Edit]") == "Radio Edit"

def test_no_parens_returns_empty():
    assert extract_version_info("Blue Monday") == ""

def test_extracts_first_group():
    assert extract_version_info("Song (feat. X) (Radio Edit)") == "feat. X"


# ── group_versions ────────────────────────────────────────────────────────────

def _row(tid, artist, title, fp="", source="unsorted", near_dup=""):
    return {
        "track_id": tid,
        "artist": artist,
        "title": title,
        "file_path": fp,
        "_source": source,
        "near_duplicate_of": near_dup,
        "duration_seconds": "360",
        "bpm": "128",
        "key_camelot": "7B",
    }


def test_groups_same_song_different_versions():
    rows = [
        _row("a", "New Order", "Blue Monday (Extended Mix)", "/m/a.aiff"),
        _row("b", "New Order", "Blue Monday (Original Mix)", "/m/b.aiff"),
    ]
    groups = group_versions(rows)
    assert len(groups) == 1
    members_ids = {m["track_id"] for m in list(groups.values())[0]}
    assert members_ids == {"a", "b"}


def test_single_track_not_grouped():
    rows = [_row("a", "New Order", "Blue Monday")]
    groups = group_versions(rows)
    assert len(groups) == 0


def test_different_artists_separate_groups():
    rows = [
        _row("a", "New Order", "Blue Monday (Original)"),
        _row("b", "Orgy", "Blue Monday (Cover)"),
    ]
    groups = group_versions(rows)
    assert len(groups) == 0  # both are singletons after artist split


def test_deduplicates_by_file_path_library_wins():
    rows = [
        _row("u1", "New Order", "Blue Monday (Extended)", "/m/a.aiff", source="unsorted"),
        _row("l1", "New Order", "Blue Monday (Extended)", "/m/a.aiff", source="library"),
        _row("b1", "New Order", "Blue Monday (Original)", "/m/b.aiff", source="library"),
    ]
    groups = group_versions(rows)
    assert len(groups) == 1
    members = list(groups.values())[0]
    # l1 should win over u1 (library > unsorted, same path)
    member_ids = {m["track_id"] for m in members}
    assert "u1" not in member_ids
    assert "l1" in member_ids
    assert "b1" in member_ids


def test_cross_source_grouping():
    rows = [
        _row("u1", "Bicep", "Glue (Bicep Remix)", "/u/a.aiff", source="unsorted"),
        _row("l1", "Bicep", "Glue (Original)", "/l/b.aiff", source="library"),
    ]
    groups = group_versions(rows)
    assert len(groups) == 1


def test_min_group_size_respected():
    rows = [
        _row("a", "New Order", "Blue Monday (Extended)", "/m/a.aiff"),
        _row("b", "New Order", "Blue Monday (Original)", "/m/b.aiff"),
        _row("c", "Bicep", "Glue (Original)", "/m/c.aiff"),
    ]
    groups = group_versions(rows, min_group_size=3)
    assert len(groups) == 0


def test_group_versions_three_members():
    rows = [
        {**_row("a", "New Order", "Blue Monday (Radio Edit)", "/m/a.aiff"), "duration_seconds": "210"},
        {**_row("b", "New Order", "Blue Monday (Extended Mix)", "/m/b.aiff"), "duration_seconds": "480"},
        {**_row("c", "New Order", "Blue Monday (Original Mix)", "/m/c.aiff"), "duration_seconds": "360"},
    ]
    groups = group_versions(rows)
    assert len(groups) == 1
    members = list(groups.values())[0]
    assert len(members) == 3
    assert {m["track_id"] for m in members} == {"a", "b", "c"}
