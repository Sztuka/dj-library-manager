"""Tests for djlib.lww_merge — LWW per-field merge engine."""
from __future__ import annotations

import pytest

from djlib.lww_merge import AuditEntry, MERGE_FIELDS, merge_gig_tracks, merge_track


# ── Helpers ───────────────────────────────────────────────────────────────────


FIELDS = ["cue_points_rb", "rating", "play_count", "bpm"]


def _row(**kwargs) -> dict:
    base = {f: "" for f in FIELDS}
    base.update(kwargs)
    return base


def _merge(baseline, live, fresh, fields=None):
    return merge_track("t1", baseline, live, fresh, merge_fields=fields or FIELDS)


# ── No-change cases ───────────────────────────────────────────────────────────


def test_no_change_anywhere_no_audit():
    row = _row(rating="204", play_count="5", bpm="128.00", cue_points_rb="")
    resolved, audit = _merge(row, dict(row), dict(row))
    assert resolved == row
    assert audit == []


def test_fields_not_in_fresh_are_kept_from_live():
    """Fields absent from fresh dict are not evaluated — live value kept."""
    baseline = _row(rating="51")
    live     = _row(rating="204")    # changed by sync
    fresh    = {}                     # rekordbox returned nothing for this field

    resolved, audit = _merge(baseline, live, fresh)
    assert resolved["rating"] == "204"  # live kept
    assert audit == []


# ── Fresh wins ────────────────────────────────────────────────────────────────


def test_fresh_changed_overwrites_live():
    baseline = _row(rating="51")
    live     = _row(rating="51")
    fresh    = _row(rating="204")

    resolved, audit = _merge(baseline, live, fresh)
    assert resolved["rating"] == "204"
    assert len(audit) == 1
    assert audit[0].source == "fresh"
    assert audit[0].conflict is False


def test_fresh_changed_cue_points():
    cues_before = '{"v":1,"cues":[]}'
    cues_after  = '{"v":1,"cues":[{"kind":1,"pos_ms":1000}]}'
    baseline = _row(cue_points_rb=cues_before)
    live     = _row(cue_points_rb=cues_before)
    fresh    = _row(cue_points_rb=cues_after)

    resolved, audit = _merge(baseline, live, fresh)
    assert resolved["cue_points_rb"] == cues_after
    assert audit[0].field_name == "cue_points_rb"
    assert audit[0].source == "fresh"


def test_fresh_wins_multiple_fields():
    baseline = _row(rating="51", play_count="3")
    live     = _row(rating="51", play_count="3")
    fresh    = _row(rating="204", play_count="10")

    resolved, audit = _merge(baseline, live, fresh)
    assert resolved["rating"] == "204"
    assert resolved["play_count"] == "10"
    assert len(audit) == 2
    assert all(e.source == "fresh" for e in audit)


# ── Live wins ─────────────────────────────────────────────────────────────────


def test_live_changed_but_fresh_unchanged_keeps_live():
    baseline = _row(rating="51")
    live     = _row(rating="102")   # sync updated it while gig was on
    fresh    = _row(rating="51")    # DJ didn't touch rating

    resolved, audit = _merge(baseline, live, fresh)
    assert resolved["rating"] == "102"
    assert audit[0].source == "live"
    assert audit[0].conflict is False


# ── Conflict: both changed, fresh wins ────────────────────────────────────────


def test_conflict_fresh_wins_and_flagged():
    baseline = _row(rating="51")
    live     = _row(rating="102")   # sync updated
    fresh    = _row(rating="204")   # DJ also changed

    resolved, audit = _merge(baseline, live, fresh)
    assert resolved["rating"] == "204"   # fresh wins
    assert audit[0].source == "fresh"
    assert audit[0].conflict is True


def test_conflict_flagged_in_audit_entry_values():
    baseline = _row(bpm="128.00")
    live     = _row(bpm="130.00")
    fresh    = _row(bpm="126.00")

    _, audit = _merge(baseline, live, fresh)
    e = audit[0]
    assert e.baseline_value == "128.00"
    assert e.live_value     == "130.00"
    assert e.fresh_value    == "126.00"
    assert e.resolved_value == "126.00"
    assert e.conflict is True


# ── None / empty edge cases ───────────────────────────────────────────────────


def test_none_values_treated_as_empty():
    baseline = {"rating": None, "play_count": ""}
    live     = {"rating": None, "play_count": ""}
    fresh    = {"rating": None, "play_count": ""}

    resolved, audit = merge_track("t1", baseline, live, fresh, merge_fields=["rating", "play_count"])
    assert audit == []


def test_none_in_fresh_treated_as_empty_string():
    baseline = _row(rating="51")
    live     = _row(rating="51")
    fresh    = {"rating": None}  # None from a problematic DB row

    resolved, audit = _merge(baseline, live, fresh)
    # None → "" which equals baseline "" — depends on baseline
    # Here baseline is "51", fresh is "", so fresh changed baseline
    assert resolved["rating"] == ""
    assert audit[0].source == "fresh"


def test_zero_value_not_treated_as_empty():
    """play_count=0 must not be collapsed to '' (0 is a valid value)."""
    baseline = _row(play_count="5")
    live     = _row(play_count="5")
    fresh    = _row(play_count="0")  # track was never played after gig prep? unlikely but valid

    resolved, audit = _merge(baseline, live, fresh)
    assert resolved["play_count"] == "0"
    assert audit[0].source == "fresh"


# ── Schema drift ──────────────────────────────────────────────────────────────


def test_field_in_fresh_not_in_baseline_treated_as_empty_baseline():
    """If baseline doesn't have a field, baseline value defaults to ''."""
    baseline = {}      # old schema — field didn't exist yet
    live     = {}
    fresh    = {"rating": "204"}

    resolved, audit = merge_track("t1", baseline, live, fresh, merge_fields=["rating"])
    assert resolved["rating"] == "204"
    assert audit[0].source == "fresh"


def test_extra_fields_in_live_pass_through_unchanged():
    """Fields not in merge_fields are taken from live as-is."""
    baseline = _row(rating="51")
    live     = dict(_row(rating="51"), title="My Track", artist="DJ Test")
    fresh    = _row(rating="204")

    resolved, _ = _merge(baseline, live, fresh)
    assert resolved["title"] == "My Track"
    assert resolved["artist"] == "DJ Test"


# ── Audit entry structure ──────────────────────────────────────────────────────


def test_audit_entry_track_id():
    baseline = _row(rating="51")
    live     = _row(rating="51")
    fresh    = _row(rating="204")

    _, audit = merge_track("my-track-id", baseline, live, fresh, merge_fields=FIELDS)
    assert audit[0].track_id == "my-track-id"


def test_no_audit_entry_when_no_change():
    row = _row(rating="204")
    _, audit = _merge(row, dict(row), dict(row))
    assert not any(e.field_name == "rating" for e in audit)


# ── merge_gig_tracks (batch) ──────────────────────────────────────────────────


def test_merge_gig_tracks_happy_path():
    gig_rows = [
        {"track_id": "t1", "rating": "51",  "play_count": "3", "cue_points_rb": "", "bpm": ""},
        {"track_id": "t2", "rating": "204", "play_count": "1", "cue_points_rb": "", "bpm": ""},
    ]
    live = {
        "t1": {"track_id": "t1", "rating": "51",  "play_count": "3",  "cue_points_rb": "", "bpm": "", "title": "Track One"},
        "t2": {"track_id": "t2", "rating": "204", "play_count": "1",  "cue_points_rb": "", "bpm": "", "title": "Track Two"},
    }
    fresh = {
        "t1": {"rating": "255", "play_count": "5",  "cue_points_rb": "", "bpm": ""},
        "t2": {"rating": "204", "play_count": "10", "cue_points_rb": "", "bpm": ""},
    }

    resolved_rows, audit = merge_gig_tracks(gig_rows, live, fresh)

    assert len(resolved_rows) == 2
    by_tid = {r["track_id"]: r for r in resolved_rows}

    assert by_tid["t1"]["rating"] == "255"       # fresh won
    assert by_tid["t1"]["play_count"] == "5"     # fresh won
    assert by_tid["t1"]["title"] == "Track One"  # pass-through from live

    assert by_tid["t2"]["play_count"] == "10"    # fresh won (count went up)


def test_merge_gig_tracks_missing_from_fresh_keeps_live():
    """Track absent from fresh_by_tid → all fields from live unchanged."""
    gig_rows = [{"track_id": "t1", "rating": "51", "play_count": "3", "cue_points_rb": "", "bpm": ""}]
    live = {"t1": {"track_id": "t1", "rating": "51", "play_count": "3", "cue_points_rb": "", "bpm": ""}}
    fresh = {}  # rekordbox_reader found nothing for t1 (no rekordbox_id)

    resolved_rows, audit = merge_gig_tracks(gig_rows, live, fresh)
    assert resolved_rows[0]["rating"] == "51"
    assert audit == []


def test_merge_gig_tracks_preserves_order():
    gig_rows = [
        {"track_id": f"t{i}", "rating": "", "play_count": "", "cue_points_rb": "", "bpm": ""}
        for i in range(5)
    ]
    live = {f"t{i}": {"track_id": f"t{i}", "rating": "", "play_count": "", "cue_points_rb": "", "bpm": ""} for i in range(5)}

    resolved_rows, _ = merge_gig_tracks(gig_rows, live, {})
    assert [r["track_id"] for r in resolved_rows] == [f"t{i}" for i in range(5)]


def test_merge_gig_tracks_empty_track_id_skipped():
    """Rows with empty track_id must be skipped with a warning, not silently merged."""
    gig_rows = [
        {"track_id": "",   "rating": "51",  "play_count": "", "cue_points_rb": "", "bpm": ""},
        {"track_id": "t1", "rating": "102", "play_count": "", "cue_points_rb": "", "bpm": ""},
    ]
    live = {"t1": {"track_id": "t1", "rating": "102", "play_count": "", "cue_points_rb": "", "bpm": ""}}

    resolved_rows, _ = merge_gig_tracks(gig_rows, live, {})
    # Only t1 should appear — empty track_id row is skipped
    assert len(resolved_rows) == 1
    assert resolved_rows[0]["track_id"] == "t1"


def test_merge_gig_tracks_missing_from_live_falls_back_to_baseline():
    """Track absent from library.csv uses baseline snapshot for non-merge fields.

    This is documented data loss: non-merge fields edited between prep and merge
    are overwritten with the baseline snapshot. A warning is logged.
    """
    gig_rows = [{"track_id": "t1", "rating": "51", "play_count": "", "cue_points_rb": "", "bpm": "",
                 "title": "Old Title"}]
    live = {}  # track vanished from library.csv
    fresh = {"t1": {"rating": "204", "play_count": "", "cue_points_rb": "", "bpm": ""}}

    resolved_rows, audit = merge_gig_tracks(gig_rows, live, fresh)
    assert len(resolved_rows) == 1
    # fresh rating applied
    assert resolved_rows[0]["rating"] == "204"
    # title comes from baseline (old snapshot) — documented limitation
    assert resolved_rows[0]["title"] == "Old Title"


def test_play_count_lww_not_max():
    """play_count uses LWW (fresh wins on conflict), not MAX.

    Known limitation: if sync incremented count while DJ also played,
    the lower fresh count wins. Documented trade-off vs overcount risk.
    """
    baseline = _row(play_count="3")
    live     = _row(play_count="5")   # sync added 2 plays
    fresh    = _row(play_count="4")   # DJ played once → baseline+1

    resolved, audit = _merge(baseline, live, fresh)
    # fresh wins conflict → "4" (lower than live "5") — known limitation
    assert resolved["play_count"] == "4"
    assert audit[0].conflict is True
    assert audit[0].source == "fresh"


def test_merge_gig_tracks_collects_all_audit_entries():
    gig_rows = [
        {"track_id": "t1", "rating": "51",  "play_count": "1", "cue_points_rb": "", "bpm": ""},
        {"track_id": "t2", "rating": "102", "play_count": "2", "cue_points_rb": "", "bpm": ""},
    ]
    live = {
        "t1": {"track_id": "t1", "rating": "51",  "play_count": "1", "cue_points_rb": "", "bpm": ""},
        "t2": {"track_id": "t2", "rating": "102", "play_count": "2", "cue_points_rb": "", "bpm": ""},
    }
    fresh = {
        "t1": {"rating": "255", "play_count": "1", "cue_points_rb": "", "bpm": ""},  # rating changed
        "t2": {"rating": "102", "play_count": "9", "cue_points_rb": "", "bpm": ""},  # count changed
    }

    _, audit = merge_gig_tracks(gig_rows, live, fresh)
    fields_changed = {(e.track_id, e.field_name) for e in audit}
    assert ("t1", "rating") in fields_changed
    assert ("t2", "play_count") in fields_changed
