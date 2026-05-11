"""Dirty-data roundtrip tests for cue point serialization.

Verifies that cue_points_rb / cue_points_tk survive:
  - Storage in library.csv (CSV quoting of embedded JSON)
  - Labels containing commas, double-quotes, newlines, emoji, null bytes
  - Roundtrip serialize → deserialize
  - Malformed JSON on read (graceful fallback, never raises)
  - Empty / None cue lists
  - Schema version mismatch
"""
from __future__ import annotations

import csv
import io
import json
from types import SimpleNamespace
from typing import Any, List

import pytest

from djlib.cues.schema import (
    CUE_SCHEMA_VERSION,
    deserialize_rb_cues,
    deserialize_tk_cues,
    serialize_rb_cues,
    serialize_tk_cues,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_rb_cue(**kwargs) -> Any:
    """Minimal fake DjmdCue object (SimpleNamespace)."""
    defaults = dict(
        InMsec=0, InFrame=0, Kind=0, Color=0, ColorTableIndex=0,
        ActiveLoop=False, Comment="", BeatLoopSize=0.0,
        OutMsec=0, OutFrame=0, CueMicrosec=0,
        InPointSeekInfo="", OutPointSeekInfo="",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_tk_cue(**kwargs) -> Any:
    """Minimal fake CueV2Type object (SimpleNamespace)."""
    defaults = dict(
        name="", displ_order=0, type=0, start=0.0, len=0.0,
        repeats=-1, hotcue=-1, color=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _csv_roundtrip(json_str: str) -> str:
    """Write json_str to a CSV cell and read it back — exercises CSV quoting."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["cue_points_rb"])
    writer.writeheader()
    writer.writerow({"cue_points_rb": json_str})
    buf.seek(0)
    reader = csv.DictReader(buf)
    return next(reader)["cue_points_rb"]


# ── Rekordbox roundtrip ───────────────────────────────────────────────────────

def test_rb_empty_cues():
    result = serialize_rb_cues([])
    cues = deserialize_rb_cues(result)
    assert cues == []


def test_rb_none_cues():
    result = serialize_rb_cues(None)
    cues = deserialize_rb_cues(result)
    assert cues == []


def test_rb_single_memory_cue():
    cue = _make_rb_cue(InMsec=32000, Kind=0, Comment="Intro")
    result = serialize_rb_cues([cue])
    cues = deserialize_rb_cues(result)
    assert len(cues) == 1
    assert cues[0]["pos_ms"] == 32000
    assert cues[0]["kind"] == 0
    assert cues[0]["label"] == "Intro"


def test_rb_hot_cue_slot():
    cue = _make_rb_cue(InMsec=64000, Kind=3, Comment="Drop", Color=255)
    result = serialize_rb_cues([cue])
    cues = deserialize_rb_cues(result)
    assert cues[0]["kind"] == 3
    assert cues[0]["color"] == 255


def test_rb_loop_cue():
    cue = _make_rb_cue(InMsec=1000, OutMsec=9000, ActiveLoop=True, BeatLoopSize=4.0)
    result = serialize_rb_cues([cue])
    cues = deserialize_rb_cues(result)
    assert cues[0]["active_loop"] is True
    assert cues[0]["out_ms"] == 9000
    assert cues[0]["loop_size"] == 4.0


def test_rb_dirty_label_comma_quote_newline():
    """Labels with CSV-hostile chars must survive CSV roundtrip."""
    dirty = 'Drop, "the bass"\nhere'
    cue = _make_rb_cue(Comment=dirty)
    json_str = serialize_rb_cues([cue])
    csv_survived = _csv_roundtrip(json_str)
    cues = deserialize_rb_cues(csv_survived)
    assert cues[0]["label"] == dirty


def test_rb_dirty_label_emoji():
    cue = _make_rb_cue(Comment="🎵 Drop 🔥")
    json_str = serialize_rb_cues([cue])
    csv_survived = _csv_roundtrip(json_str)
    cues = deserialize_rb_cues(csv_survived)
    assert cues[0]["label"] == "🎵 Drop 🔥"


def test_rb_dirty_label_unicode_accents():
    cue = _make_rb_cue(Comment="Ré-intro (café)")
    json_str = serialize_rb_cues([cue])
    cues = deserialize_rb_cues(json_str)
    assert cues[0]["label"] == "Ré-intro (café)"


def test_rb_multiple_cues_preserved():
    cues_in = [_make_rb_cue(InMsec=i * 1000, Kind=i, Comment=f"Cue {i}") for i in range(8)]
    result = serialize_rb_cues(cues_in)
    cues_out = deserialize_rb_cues(result)
    assert len(cues_out) == 8
    for i, c in enumerate(cues_out):
        assert c["pos_ms"] == i * 1000
        assert c["kind"] == i
        assert c["label"] == f"Cue {i}"


def test_rb_schema_version_in_output():
    result = serialize_rb_cues([])
    data = json.loads(result)
    assert data["v"] == CUE_SCHEMA_VERSION
    assert "cues" in data


def test_rb_mpeg_fields_dropped():
    """MPEG seek hints must not appear in serialized output."""
    cue = _make_rb_cue()
    # Add MPEG attrs that should be dropped
    cue.InMpegFrame = 999
    cue.InMpegAbs = 999
    result = serialize_rb_cues([cue])
    data = json.loads(result)
    for field in ("in_mpeg_frame", "in_mpeg_abs", "out_mpeg_frame", "out_mpeg_abs"):
        assert field not in data["cues"][0], f"MPEG field {field!r} leaked into output"


# ── Traktor roundtrip ─────────────────────────────────────────────────────────

def test_tk_empty_cues():
    result = serialize_tk_cues([])
    cues = deserialize_tk_cues(result)
    assert cues == []


def test_tk_none_cues():
    result = serialize_tk_cues(None)
    cues = deserialize_tk_cues(result)
    assert cues == []


def test_tk_memory_cue():
    cue = _make_tk_cue(name="Intro", type=0, start=32.5, hotcue=-1)
    result = serialize_tk_cues([cue])
    cues = deserialize_tk_cues(result)
    assert cues[0]["label"] == "Intro"
    assert cues[0]["pos_s"] == 32.5
    assert cues[0]["hotcue"] == -1


def test_tk_hot_cue():
    cue = _make_tk_cue(name="Drop", hotcue=2, start=64.0)
    result = serialize_tk_cues([cue])
    cues = deserialize_tk_cues(result)
    assert cues[0]["hotcue"] == 2


def test_tk_loop_cue():
    cue = _make_tk_cue(type=5, start=10.0, len=4.0)
    result = serialize_tk_cues([cue])
    cues = deserialize_tk_cues(result)
    assert cues[0]["kind"] == 5
    assert cues[0]["len_s"] == 4.0


def test_tk_color_none_serializes_as_null():
    cue = _make_tk_cue(color=None)
    result = serialize_tk_cues([cue])
    data = json.loads(result)
    assert data["cues"][0]["color"] is None


def test_tk_dirty_label_csv_roundtrip():
    dirty = 'Break, "section"\nnew line'
    cue = _make_tk_cue(name=dirty)
    json_str = serialize_tk_cues([cue])
    csv_survived = _csv_roundtrip(json_str)
    cues = deserialize_tk_cues(csv_survived)
    assert cues[0]["label"] == dirty


# ── Deserialization error handling ────────────────────────────────────────────

def test_deserialize_rb_empty_string_returns_empty():
    assert deserialize_rb_cues("") == []


def test_deserialize_rb_malformed_json_returns_empty():
    assert deserialize_rb_cues("{not valid json") == []


def test_deserialize_rb_wrong_version_returns_empty():
    bad = json.dumps({"v": 999, "cues": [{"pos_ms": 0}]})
    assert deserialize_rb_cues(bad) == []


def test_deserialize_rb_not_object_returns_empty():
    assert deserialize_rb_cues(json.dumps([{"pos_ms": 0}])) == []


def test_deserialize_tk_empty_string_returns_empty():
    assert deserialize_tk_cues("") == []


def test_deserialize_tk_malformed_json_returns_empty():
    assert deserialize_tk_cues("definitely not json!!") == []


def test_serialize_rb_cues_never_raises_on_bad_object():
    """serialize_rb_cues must not raise even when given garbage."""
    class Garbage:
        @property
        def InMsec(self):
            raise RuntimeError("boom")

    result = serialize_rb_cues([Garbage()])
    # Must return something valid (either empty or partial)
    assert isinstance(result, str)
    # Must be parseable JSON
    data = json.loads(result)
    assert "v" in data and "cues" in data


# ── CSV integration (library.csv roundtrip) ───────────────────────────────────

def test_cue_points_survive_library_csv_write_read(tmp_path):
    """Full roundtrip: serialize → write to library.csv → read back → deserialize."""
    from djlib.library_schema import LIBRARY_FIELDNAMES, load_library_csv, save_library_csv

    cues_in = [
        _make_rb_cue(InMsec=1000, Kind=1, Comment='Hot Cue, "A"'),
        _make_rb_cue(InMsec=32000, Kind=0, Comment="Intro 🎵"),
    ]
    json_str = serialize_rb_cues(cues_in)

    csv_path = tmp_path / "library.csv"
    row = {f: "" for f in LIBRARY_FIELDNAMES}
    row["track_id"] = "test-001"
    row["cue_points_rb"] = json_str

    save_library_csv(csv_path, [row])

    loaded = load_library_csv(csv_path)
    assert len(loaded) == 1
    recovered = deserialize_rb_cues(loaded[0]["cue_points_rb"])
    assert len(recovered) == 2
    assert recovered[0]["pos_ms"] == 1000
    assert recovered[0]["label"] == 'Hot Cue, "A"'
    assert recovered[1]["label"] == "Intro 🎵"
