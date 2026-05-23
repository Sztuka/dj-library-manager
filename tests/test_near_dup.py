"""Tests for near-duplicate detection."""
from __future__ import annotations

import pytest
from djlib.near_dup import _artist_slug, _base_title_slug, _rows_match, flag_near_dups


# ── _base_title_slug ──────────────────────────────────────────────────────────

def test_slug_strips_parens():
    assert _base_title_slug("Unwritten (Talon Remix)") == "unwritten"

def test_slug_strips_brackets():
    assert _base_title_slug("Blue Monday [Extended]") == "bluemonday"

def test_slug_strips_multiple_groups():
    assert _base_title_slug("Song (feat. Artist) (Radio Edit)") == "song"

def test_slug_normalizes_unicode():
    assert _base_title_slug("Café del Mar") == "cafedelmar"

def test_slug_lowercase_alnum():
    assert _base_title_slug("FEEL THE SAME") == "feelthesame"

def test_slug_same_base():
    a = _base_title_slug("Unwritten (Talon Remix)")
    b = _base_title_slug("Unwritten (Talon Afrohouse Remix)")
    assert a == b == "unwritten"


# ── _artist_slug ─────────────────────────────────────────────────────────────

def test_artist_slug_strips_feat():
    assert _artist_slug("Artist feat. Someone") == "artist"

def test_artist_slug_strips_ampersand():
    assert _artist_slug("Artist & Other") == "artist"

def test_artist_slug_strips_vs():
    assert _artist_slug("Artist vs. Other") == "artist"

def test_artist_slug_normalizes_unicode():
    assert _artist_slug("Sigur Rós") == "sigurros"

def test_artist_slug_empty():
    assert _artist_slug("") == ""

def test_artist_slug_x_collab():
    assert _artist_slug("Martin Garrix x Tiësto") == "martingarrix"

def test_artist_slug_x_in_name_not_stripped():
    # "x-Press" — the x is part of the name, not a collab separator
    assert _artist_slug("DJ x-Press") == "djxpress"

def test_artist_slug_x_as_last_letter_not_stripped():
    # "DJ X" — X is the artist name, not a collab separator (no following artist)
    assert _artist_slug("DJ X") == "djx"

def test_artist_slug_multiple_ampersands():
    assert _artist_slug("A & B & C") == "a"


# ── _rows_match ───────────────────────────────────────────────────────────────

def _row(**kwargs):
    defaults = {"track_id": "tid", "key_camelot": "7B", "bpm": "124", "duration_seconds": "360"}
    defaults.update(kwargs)
    return defaults

def test_rows_match_identical():
    a = _row(track_id="a")
    b = _row(track_id="b")
    assert _rows_match(a, b) is True

def test_rows_match_bpm_within_1():
    a = _row(track_id="a", bpm="124")
    b = _row(track_id="b", bpm="125")
    assert _rows_match(a, b) is True

def test_rows_no_match_bpm_diff_2():
    a = _row(track_id="a", bpm="124")
    b = _row(track_id="b", bpm="126")
    assert _rows_match(a, b) is False

def test_rows_match_dur_within_5():
    a = _row(track_id="a", duration_seconds="360")
    b = _row(track_id="b", duration_seconds="364")
    assert _rows_match(a, b) is True

def test_rows_no_match_dur_diff_6():
    a = _row(track_id="a", duration_seconds="360")
    b = _row(track_id="b", duration_seconds="367")
    assert _rows_match(a, b) is False

def test_rows_no_match_different_key():
    a = _row(track_id="a", key_camelot="7B")
    b = _row(track_id="b", key_camelot="8A")
    assert _rows_match(a, b) is False

def test_rows_match_missing_bpm_is_inconclusive():
    a = _row(track_id="a", bpm="")
    b = _row(track_id="b", bpm="124")
    assert _rows_match(a, b) is True  # missing = inconclusive, not mismatch

def test_rows_match_missing_duration_is_inconclusive():
    a = _row(track_id="a", duration_seconds="")
    b = _row(track_id="b", duration_seconds="360")
    assert _rows_match(a, b) is True

def test_rows_no_match_different_artist():
    a = _row(track_id="a", artist="Original Artist")
    b = _row(track_id="b", artist="Cover Artist")
    assert _rows_match(a, b) is False

def test_rows_match_same_artist():
    a = _row(track_id="a", artist="Bicep")
    b = _row(track_id="b", artist="Bicep")
    assert _rows_match(a, b) is True

def test_rows_match_missing_artist_is_inconclusive():
    a = _row(track_id="a", artist="")
    b = _row(track_id="b", artist="Bicep")
    assert _rows_match(a, b) is True

def test_rows_match_artist_ignores_feat_suffix():
    a = _row(track_id="a", artist="Bicep feat. Clara Hill")
    b = _row(track_id="b", artist="Bicep")
    assert _rows_match(a, b) is True


# ── flag_near_dups ────────────────────────────────────────────────────────────

def _staging_row(tid, title, key="7B", bpm="124", dur="360"):
    return {
        "track_id": tid,
        "title": title,
        "key_camelot": key,
        "bpm": bpm,
        "duration_seconds": dur,
        "near_duplicate_of": "",
    }

def test_flag_near_dups_same_base_same_key_bpm():
    rows = [
        _staging_row("a", "Unwritten (Talon Remix)"),
        _staging_row("b", "Unwritten (Talon Afrohouse Remix)"),
    ]
    count = flag_near_dups(rows)
    assert count == 2
    assert rows[0]["near_duplicate_of"] == "b"
    assert rows[1]["near_duplicate_of"] == "a"

def test_flag_near_dups_different_key_no_flag():
    rows = [
        _staging_row("a", "Unwritten (Remix)", key="7B"),
        _staging_row("b", "Unwritten (Edit)", key="8A"),
    ]
    count = flag_near_dups(rows)
    assert count == 0

def test_flag_near_dups_different_bpm_no_flag():
    rows = [
        _staging_row("a", "Song (Mix A)", bpm="120"),
        _staging_row("b", "Song (Mix B)", bpm="128"),
    ]
    count = flag_near_dups(rows)
    assert count == 0

def test_flag_near_dups_against_library():
    staging = [_staging_row("s1", "Blue Monday (Extended Mix)")]
    library = [_staging_row("l1", "Blue Monday (Original Mix)")]
    count = flag_near_dups(staging, library)
    assert count == 1
    assert staging[0]["near_duplicate_of"] == "l1"
    # Library row should NOT be modified
    assert "near_duplicate_of" not in library[0] or library[0].get("near_duplicate_of") == ""

def test_flag_near_dups_resets_stale_flags():
    rows = [
        _staging_row("a", "Totally Different Song"),
        _staging_row("b", "Another Track"),
    ]
    rows[0]["near_duplicate_of"] = "stale-id"  # simulate stale flag from previous scan
    count = flag_near_dups(rows)
    assert count == 0
    assert rows[0]["near_duplicate_of"] == ""  # stale flag cleared

def test_flag_near_dups_short_slug_skipped():
    # Slug "ok" is too short (< 3 chars) — should not be grouped
    rows = [
        _staging_row("a", "OK (Mix A)"),
        _staging_row("b", "OK (Mix B)"),
    ]
    count = flag_near_dups(rows)
    assert count == 0

def test_flag_near_dups_no_false_positive_different_titles():
    rows = [
        _staging_row("a", "Acid Rain"),
        _staging_row("b", "Blue Monday"),
    ]
    count = flag_near_dups(rows)
    assert count == 0


def test_ghost_row_same_track_id_not_flagged():
    """Track moved from Rekordbox to Music Unsorted creates a ghost row in
    library.csv with the same track_id. Must NOT be flagged as a near-duplicate
    — it is the same file, not a separate copy."""
    staging = [_staging_row("uuid-123", "Blue Monday")]
    library = [{"track_id": "uuid-123", "title": "Blue Monday", "artist": "New Order"}]
    count = flag_near_dups(staging, library)
    assert count == 0
    assert staging[0]["near_duplicate_of"] == ""


def test_different_track_id_same_title_still_flagged():
    """Two tracks with the same title but different track_ids are genuinely
    suspect and should still be flagged."""
    staging = [_staging_row("tid-A", "Blue Monday")]
    library = [{"track_id": "tid-B", "title": "Blue Monday", "artist": "New Order"}]
    count = flag_near_dups(staging, library)
    assert count == 1
    assert staging[0]["near_duplicate_of"] == "tid-B"
