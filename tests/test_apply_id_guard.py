"""Unit tests for the rekordbox_id / traktor_id preservation guard in cmd_apply.

The guard runs after `merged.update(record)` and prevents empty incoming IDs
from wiping valid IDs that existed in library.csv (ghost-row scenario).

These tests replicate the exact dict operations from cmd_apply lines 1823-1843
so the logic is verifiable without running the full cmd_apply pipeline.
"""
from __future__ import annotations


def _run_id_guard(existing_row: dict, record: dict) -> dict:
    """Mirror of the cmd_apply merge block (lines 1822-1843 in cli.py).

    Any change to the guard logic in cli.py must be reflected here.
    """
    merged = dict(existing_row)
    merged.update(record)

    for _dj_id in ("rekordbox_id", "traktor_id"):
        _incoming = (record.get(_dj_id) or "").strip()
        _existing_raw = existing_row.get(_dj_id) or ""
        if not _incoming and _existing_raw.strip():
            merged[_dj_id] = _existing_raw

    _mrb = (merged.get("rekordbox_id") or "").strip()
    _mtr = (merged.get("traktor_id") or "").strip()
    if _mrb:
        merged["analysis_source"] = "rekordbox"
    elif _mtr:
        merged["analysis_source"] = merged.get("analysis_source") or "traktor"
    elif not merged.get("analysis_source"):
        merged["analysis_source"] = existing_row.get("analysis_source") or ""

    return merged


# ── Happy path: ghost-row scenario ──────────────────────────────────────────


def test_ghost_row_rekordbox_id_preserved():
    """Track moved from Rekordbox to unsorted: scan gives rekordbox_id=''.
    Guard must restore the library.csv value."""
    existing = {"track_id": "t", "rekordbox_id": "rb-42", "analysis_source": "rekordbox"}
    record = {"track_id": "t", "rekordbox_id": "", "analysis_source": ""}
    merged = _run_id_guard(existing, record)
    assert merged["rekordbox_id"] == "rb-42"


def test_ghost_row_analysis_source_preserved():
    """analysis_source must be re-derived from the preserved rekordbox_id,
    not taken from the incoming record (which has it as '')."""
    existing = {"track_id": "t", "rekordbox_id": "rb-42", "analysis_source": "rekordbox"}
    record = {"track_id": "t", "rekordbox_id": "", "analysis_source": ""}
    merged = _run_id_guard(existing, record)
    assert merged["analysis_source"] == "rekordbox"


def test_ghost_row_traktor_id_preserved():
    """Same scenario for traktor_id."""
    existing = {"track_id": "t", "traktor_id": "tr-7", "analysis_source": "traktor"}
    record = {"track_id": "t", "traktor_id": "", "analysis_source": ""}
    merged = _run_id_guard(existing, record)
    assert merged["traktor_id"] == "tr-7"
    assert merged["analysis_source"] == "traktor"


# ── Legitimate ID update passes through ─────────────────────────────────────


def test_new_rekordbox_id_from_scan_wins():
    """If the scanner DID read a (different) rekordbox_id, the new value wins."""
    existing = {"track_id": "t", "rekordbox_id": "rb-old", "analysis_source": "rekordbox"}
    record = {"track_id": "t", "rekordbox_id": "rb-new", "analysis_source": "rekordbox"}
    merged = _run_id_guard(existing, record)
    assert merged["rekordbox_id"] == "rb-new"
    assert merged["analysis_source"] == "rekordbox"


def test_new_track_no_existing_id():
    """New track with no existing row: record passes through unchanged."""
    existing = {"track_id": "t", "rekordbox_id": "", "analysis_source": ""}
    record = {"track_id": "t", "rekordbox_id": "rb-fresh", "analysis_source": "rekordbox"}
    merged = _run_id_guard(existing, record)
    assert merged["rekordbox_id"] == "rb-fresh"
    assert merged["analysis_source"] == "rekordbox"


# ── Whitespace / dirty-data edge cases ───────────────────────────────────────


def test_incoming_whitespace_id_treated_as_empty():
    """Scanner emitted '  ' (whitespace-only) as rekordbox_id.
    Guard must treat it as empty and preserve the existing valid ID."""
    existing = {"track_id": "t", "rekordbox_id": "rb-42", "analysis_source": "rekordbox"}
    record = {"track_id": "t", "rekordbox_id": "  ", "analysis_source": ""}
    merged = _run_id_guard(existing, record)
    assert merged["rekordbox_id"] == "rb-42"
    assert merged["analysis_source"] == "rekordbox"


def test_existing_whitespace_id_not_preserved():
    """Existing rekordbox_id is '  ' (whitespace-only garbage).
    Guard must NOT restore it into merged — whitespace is not a valid ID."""
    existing = {"track_id": "t", "rekordbox_id": "  ", "analysis_source": ""}
    record = {"track_id": "t", "rekordbox_id": "", "analysis_source": ""}
    merged = _run_id_guard(existing, record)
    # '  '.strip() is falsy — guard does not restore the whitespace
    assert (merged.get("rekordbox_id") or "").strip() == ""


def test_existing_zero_string_id_is_preserved():
    """'0' is truthy in Python. If existing has rekordbox_id='0' and incoming
    is '', guard preserves it (it may be a valid RB edge-case ID)."""
    existing = {"track_id": "t", "rekordbox_id": "0", "analysis_source": "rekordbox"}
    record = {"track_id": "t", "rekordbox_id": "", "analysis_source": ""}
    merged = _run_id_guard(existing, record)
    # "0".strip() == "0" which is truthy → preserved
    assert merged["rekordbox_id"] == "0"


def test_incoming_none_treated_as_empty():
    """record.get() returning None (missing key) is handled by `or ''`."""
    existing = {"track_id": "t", "rekordbox_id": "rb-42", "analysis_source": "rekordbox"}
    record = {"track_id": "t"}  # rekordbox_id key absent entirely
    merged = _run_id_guard(existing, record)
    assert merged["rekordbox_id"] == "rb-42"
    assert merged["analysis_source"] == "rekordbox"


# ── analysis_source derivation ───────────────────────────────────────────────


def test_analysis_source_not_downgraded_when_no_ids_and_existing_is_tags():
    """Track applied with --allow-no-rekordbox: existing analysis_source='tags'.
    Incoming record has analysis_source=''. Guard must not blank out 'tags'."""
    existing = {"track_id": "t", "rekordbox_id": "", "traktor_id": "", "analysis_source": "tags"}
    record = {"track_id": "t", "rekordbox_id": "", "traktor_id": "", "analysis_source": ""}
    merged = _run_id_guard(existing, record)
    assert merged["analysis_source"] == "tags"


def test_analysis_source_rekordbox_beats_traktor():
    """If both IDs present, rekordbox wins for analysis_source."""
    existing = {"track_id": "t", "rekordbox_id": "rb-1", "traktor_id": "tr-1", "analysis_source": "traktor"}
    record = {"track_id": "t", "rekordbox_id": "rb-1", "traktor_id": "tr-1", "analysis_source": "tags"}
    merged = _run_id_guard(existing, record)
    assert merged["analysis_source"] == "rekordbox"
