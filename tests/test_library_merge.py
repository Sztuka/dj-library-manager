"""Tests for djlib.library_schema.merge_with_existing_library.

The merge is what keeps `sync-dj-libraries` non-destructive: djlib-owned
fields (`file_hash`, `original_path`, `added_date`, reserved key/analysis
columns) must survive a sync even though RB/Traktor don't know about them.
"""
from __future__ import annotations

from djlib.library_schema import (
    DJLIB_OWNED_FIELDS,
    apply_gig_track_guard,
    merge_with_existing_library,
)


def test_empty_existing_returns_new_unchanged_plus_added_date_backfill():
    new = [
        {"track_id": "a", "artist": "A", "date_added": "2025-01-01", "rekordbox_id": "rb1"},
        {"track_id": "b", "artist": "B", "date_added": "", "rekordbox_id": "rb2"},
    ]
    out = merge_with_existing_library(new, [])
    assert len(out) == 2
    # added_date backfilled from date_added when present…
    assert out[0]["added_date"] == "2025-01-01"
    # …otherwise left empty (nothing to backfill from)
    assert out[1]["added_date"] == ""


def test_djlib_owned_fields_survive_sync():
    """The whole point of PR2b: file_hash/original_path/added_date etc.
    stay on the row even though RB/Traktor don't know them."""
    existing = [
        {
            "track_id": "abc",
            "file_hash": "sha256-xyz",
            "original_path": "/Users/dj/Music Unsorted/x.mp3",
            "added_date": "2024-03-15",
            "key_source": "tag",
            "analysis_source": "tags",
        }
    ]
    # Fresh sync pulls the same track_id but all djlib fields blank
    new = [
        {
            "track_id": "abc",
            "artist": "Hot Chip",
            "title": "Down",
            "bpm": "120",
            "key": "5A",
            "rating": "5",
            "rekordbox_id": "rb-new",
        }
    ]
    out = merge_with_existing_library(new, existing)
    assert len(out) == 1
    row = out[0]
    # djlib-owned fields preserved
    assert row["file_hash"] == "sha256-xyz"
    assert row["original_path"] == "/Users/dj/Music Unsorted/x.mp3"
    assert row["added_date"] == "2024-03-15"
    assert row["key_source"] == "tag"
    assert row["analysis_source"] == "tags"
    # DJ-software-owned fields come from the new sync
    assert row["artist"] == "Hot Chip"
    assert row["bpm"] == "120"
    assert row["rekordbox_id"] == "rb-new"


def test_new_values_for_djlib_fields_only_fill_blanks():
    """If the new row happens to carry a djlib-owned field (e.g. a prior
    sync stored it), the existing non-empty value still wins — existing
    is authoritative for djlib-owned state."""
    existing = [{"track_id": "t", "file_hash": "keep-me"}]
    new = [{"track_id": "t", "file_hash": "overwrite-attempt", "artist": "X"}]
    out = merge_with_existing_library(new, existing)
    assert out[0]["file_hash"] == "keep-me"


def test_orphans_kept_with_cleared_external_ids():
    """A track that disappeared from RB/Traktor becomes an orphan: row
    stays in library.csv, but the dead external IDs are wiped so consumers
    (REVIEW UI, apply) can tell it's no longer live."""
    existing = [
        {
            "track_id": "orphan",
            "artist": "Forgotten",
            "rekordbox_id": "rb-999",
            "traktor_id": "tr-999",
            "external_source": "rekordbox+traktor",
            "added_date": "2023-06-01",
            "file_hash": "sha256-forgotten",
        }
    ]
    new = [{"track_id": "other", "artist": "Fresh", "rekordbox_id": "rb-new"}]
    out = merge_with_existing_library(new, existing)
    tids = {r["track_id"] for r in out}
    assert tids == {"orphan", "other"}
    orphan = [r for r in out if r["track_id"] == "orphan"][0]
    assert orphan["external_source"] == ""
    assert orphan["rekordbox_id"] == ""
    assert orphan["traktor_id"] == ""
    # djlib-owned fields survive on the orphan too
    assert orphan["added_date"] == "2023-06-01"
    assert orphan["file_hash"] == "sha256-forgotten"
    assert orphan["artist"] == "Forgotten"


def test_rows_without_track_id_pass_through_as_new():
    """Defensive: rows without a track_id shouldn't crash the merge.
    They cannot match against existing, so they're appended as-is."""
    new = [{"artist": "No TID", "date_added": "2025-05-05"}]
    out = merge_with_existing_library(new, [])
    assert len(out) == 1
    assert out[0]["artist"] == "No TID"
    assert out[0]["added_date"] == "2025-05-05"


def test_djlib_owned_fields_list_is_stable():
    """Canary: changing this list has real-world implications (e.g. added
    a field that silently starts surviving syncs). Force a conscious change."""
    assert set(DJLIB_OWNED_FIELDS) == {
        "file_path",
        "file_hash",
        "original_path",
        "added_date",
        "key_original",
        "key_source",
        "analysis_source",
        "field_sources",
        "duplicate_paths",
        # Gig tracking: set by gig-prep, must survive DJ-software re-syncs
        # so a sync during an active gig doesn't overwrite the live_location.
        "live_location",
        "live_path",
        # Artist normalization: survive syncs so normalized names aren't overwritten.
        "mb_artist_id",
        "artist_normalized",
        # Playlists: djlib-owned collection tags, never overwritten by sync.
        "playlists",
    }


def test_existing_row_without_track_id_is_ignored_as_match_key():
    """Existing rows with no track_id can't anchor a merge. They're
    effectively dropped (which is fine — a row with no track_id is
    already broken state and sync won't resurrect it)."""
    existing = [{"track_id": "", "artist": "Broken", "file_hash": "lost"}]
    new = [{"track_id": "abc", "artist": "OK"}]
    out = merge_with_existing_library(new, existing)
    assert len(out) == 1
    assert out[0]["track_id"] == "abc"


def test_added_date_preserved_over_date_added_from_sync():
    """`added_date` from djlib wins over `date_added` from the fresh sync.
    They are *different things*: added_date = when djlib adopted the track;
    date_added = when RB/Traktor imported it (which could be much later)."""
    existing = [{"track_id": "t", "added_date": "2024-01-01"}]
    new = [{"track_id": "t", "date_added": "2025-12-31", "artist": "X"}]
    out = merge_with_existing_library(new, existing)
    assert out[0]["added_date"] == "2024-01-01"
    # date_added from new row still passes through
    assert out[0]["date_added"] == "2025-12-31"


# ── apply_gig_track_guard ────────────────────────────────────────────────────


def test_gig_track_not_updated_by_sync():
    """A track with live_location='gig:friday' must not have its
    DJ-software-owned fields (bpm, artist, …) overwritten during sync."""
    existing = [
        {
            "track_id": "gig-tid",
            "live_location": "gig:friday",
            "artist": "Original Artist",
            "bpm": "128",
            "rating": "5",
        }
    ]
    # Sync brought back different DJ-software values
    merged = [
        {
            "track_id": "gig-tid",
            "live_location": "gig:friday",  # preserved by merge_with_existing_library
            "artist": "Stale NAS Artist",
            "bpm": "130",
            "rating": "3",
        }
    ]
    patched, skipped = apply_gig_track_guard(merged, existing)
    assert skipped == 1
    assert patched[0]["artist"] == "Original Artist"
    assert patched[0]["bpm"] == "128"
    assert patched[0]["rating"] == "5"


def test_nas_track_not_affected_by_guard():
    """Tracks with live_location='nas' (or empty) pass through unchanged."""
    existing = [{"track_id": "nas-tid", "live_location": "nas", "bpm": "120"}]
    merged = [{"track_id": "nas-tid", "live_location": "nas", "bpm": "124"}]
    patched, skipped = apply_gig_track_guard(merged, existing)
    assert skipped == 0
    assert patched[0]["bpm"] == "124"


def test_guard_skips_tracks_with_no_existing_row():
    """If a gig-track appears in merged but not in existing (shouldn't happen
    in practice), guard skips it gracefully without raising."""
    existing: list = []
    merged = [{"track_id": "ghost", "live_location": "gig:xyz", "artist": "X"}]
    patched, skipped = apply_gig_track_guard(merged, existing)
    assert skipped == 0  # no existing row to restore from
    assert patched[0]["artist"] == "X"
