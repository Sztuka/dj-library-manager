"""Tests for djlib.rekordbox_reader — read-only Rekordbox DB fetcher."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from djlib.rekordbox_reader import fetch_gig_tracks, _default_db_path


# ── Helpers ───────────────────────────────────────────────────────────────────


def _fake_cue(kind=1, pos_ms=1000, out_ms=0, color=0xFF0000, comment="Kick",
              active_loop=False):
    return SimpleNamespace(
        Kind=kind, InMsec=pos_ms, OutMsec=out_ms, Color=color,
        Comment=comment, ActiveLoop=active_loop, InFrame=0, OutFrame=0,
        ColorTableIndex=0, BeatLoopSize=0.0, CueMicrosec=0,
        InPointSeekInfo="", OutPointSeekInfo="",
    )


def _fake_content(rb_id, rating=204, play_count=5, bpm=12800,
                  last_played="2026-05-11", cues=None):
    return SimpleNamespace(
        ID=rb_id,
        Rating=rating,
        DJPlayCount=play_count,
        BPM=bpm,
        LastPlayed=last_played,
        Cues=cues or [],
    )


def _make_db(contents):
    db = MagicMock()
    db.get_content.return_value = contents
    db.session.close.return_value = None
    return db


# ── _default_db_path ──────────────────────────────────────────────────────────


def test_default_db_path_returns_path_or_none():
    result = _default_db_path()
    assert result is None or isinstance(result, Path)


# ── fetch_gig_tracks — basic ──────────────────────────────────────────────────


def test_fetch_returns_known_track(tmp_path):
    fake_db_path = tmp_path / "master6.db"
    fake_db_path.touch()

    content = _fake_content(rb_id=42, rating=204, play_count=7)
    gig_tracks = [{"track_id": "tid1", "rekordbox_id": "42"}]

    with patch("pyrekordbox.Rekordbox6Database", return_value=_make_db([content])):
        result = fetch_gig_tracks(gig_tracks, db_path=fake_db_path)

    assert "tid1" in result
    assert result["tid1"]["rating"] == "204"
    assert result["tid1"]["play_count"] == "7"


def test_fetch_skips_track_without_rekordbox_id(tmp_path):
    fake_db_path = tmp_path / "master6.db"
    fake_db_path.touch()

    gig_tracks = [{"track_id": "tid1", "rekordbox_id": ""}]

    with patch("pyrekordbox.Rekordbox6Database", return_value=_make_db([])):
        result = fetch_gig_tracks(gig_tracks, db_path=fake_db_path)

    assert result == {}


def test_fetch_skips_track_not_in_db(tmp_path):
    fake_db_path = tmp_path / "master6.db"
    fake_db_path.touch()

    content = _fake_content(rb_id=99)
    gig_tracks = [{"track_id": "tid1", "rekordbox_id": "42"}]  # 42 ≠ 99

    with patch("pyrekordbox.Rekordbox6Database", return_value=_make_db([content])):
        result = fetch_gig_tracks(gig_tracks, db_path=fake_db_path)

    assert result == {}


def test_fetch_empty_gig_tracks(tmp_path):
    fake_db_path = tmp_path / "master6.db"
    fake_db_path.touch()

    with patch("pyrekordbox.Rekordbox6Database") as mock_cls:
        result = fetch_gig_tracks([], db_path=fake_db_path)

    assert result == {}
    mock_cls.assert_not_called()  # no DB open if nothing to fetch


def test_fetch_multiple_tracks(tmp_path):
    fake_db_path = tmp_path / "master6.db"
    fake_db_path.touch()

    contents = [
        _fake_content(rb_id=1, rating=51,  play_count=3),
        _fake_content(rb_id=2, rating=255, play_count=20),
        _fake_content(rb_id=3, rating=0,   play_count=0),  # not in gig_tracks
    ]
    gig_tracks = [
        {"track_id": "tid1", "rekordbox_id": "1"},
        {"track_id": "tid2", "rekordbox_id": "2"},
    ]

    with patch("pyrekordbox.Rekordbox6Database", return_value=_make_db(contents)):
        result = fetch_gig_tracks(gig_tracks, db_path=fake_db_path)

    assert set(result.keys()) == {"tid1", "tid2"}
    assert result["tid1"]["rating"] == "51"
    assert result["tid2"]["play_count"] == "20"
    assert "tid3" not in result  # unrequested track not in result


# ── cue points serialization ──────────────────────────────────────────────────


def test_fetch_cue_points_serialized(tmp_path):
    fake_db_path = tmp_path / "master6.db"
    fake_db_path.touch()

    cues = [_fake_cue(kind=1, pos_ms=2000, color=0xFF0000, comment="Drop")]
    content = _fake_content(rb_id=10, cues=cues)
    gig_tracks = [{"track_id": "tid1", "rekordbox_id": "10"}]

    with patch("pyrekordbox.Rekordbox6Database", return_value=_make_db([content])):
        result = fetch_gig_tracks(gig_tracks, db_path=fake_db_path)

    cue_json = result["tid1"]["cue_points_rb"]
    parsed = json.loads(cue_json)
    assert parsed["v"] == 1
    assert len(parsed["cues"]) == 1
    assert parsed["cues"][0]["kind"] == 1
    assert parsed["cues"][0]["pos_ms"] == 2000
    assert parsed["cues"][0]["label"] == "Drop"


def test_fetch_empty_cues_returns_valid_json(tmp_path):
    fake_db_path = tmp_path / "master6.db"
    fake_db_path.touch()

    content = _fake_content(rb_id=5, cues=[])
    gig_tracks = [{"track_id": "tid1", "rekordbox_id": "5"}]

    with patch("pyrekordbox.Rekordbox6Database", return_value=_make_db([content])):
        result = fetch_gig_tracks(gig_tracks, db_path=fake_db_path)

    parsed = json.loads(result["tid1"]["cue_points_rb"])
    assert parsed == {"v": 1, "cues": []}


# ── BPM field ─────────────────────────────────────────────────────────────────


def test_fetch_bpm_divided_by_100(tmp_path):
    fake_db_path = tmp_path / "master6.db"
    fake_db_path.touch()

    content = _fake_content(rb_id=1, bpm=12800)  # 128.00
    gig_tracks = [{"track_id": "tid1", "rekordbox_id": "1"}]

    with patch("pyrekordbox.Rekordbox6Database", return_value=_make_db([content])):
        result = fetch_gig_tracks(gig_tracks, db_path=fake_db_path)

    assert result["tid1"]["bpm"] == "128.00"


def test_fetch_bpm_zero_returns_empty(tmp_path):
    fake_db_path = tmp_path / "master6.db"
    fake_db_path.touch()

    content = _fake_content(rb_id=1, bpm=0)
    gig_tracks = [{"track_id": "tid1", "rekordbox_id": "1"}]

    with patch("pyrekordbox.Rekordbox6Database", return_value=_make_db([content])):
        result = fetch_gig_tracks(gig_tracks, db_path=fake_db_path)

    assert result["tid1"]["bpm"] == ""


# ── Error handling ────────────────────────────────────────────────────────────


def test_fetch_raises_if_no_db_path_and_not_found():
    with patch("djlib.rekordbox_reader._default_db_path", return_value=None):
        with pytest.raises(FileNotFoundError, match="master6.db"):
            fetch_gig_tracks([{"track_id": "t1", "rekordbox_id": "1"}])


def test_fetch_closes_session_on_success(tmp_path):
    fake_db_path = tmp_path / "master6.db"
    fake_db_path.touch()

    content = _fake_content(rb_id=1)
    gig_tracks = [{"track_id": "tid1", "rekordbox_id": "1"}]
    mock_db = _make_db([content])

    with patch("pyrekordbox.Rekordbox6Database", return_value=mock_db):
        fetch_gig_tracks(gig_tracks, db_path=fake_db_path)

    mock_db.session.close.assert_called_once()


def test_fetch_closes_session_even_on_error(tmp_path):
    fake_db_path = tmp_path / "master6.db"
    fake_db_path.touch()

    broken_db = MagicMock()
    broken_db.get_content.side_effect = RuntimeError("DB corrupt")
    broken_db.session.close.return_value = None

    gig_tracks = [{"track_id": "tid1", "rekordbox_id": "1"}]

    with patch("pyrekordbox.Rekordbox6Database", return_value=broken_db):
        with pytest.raises(RuntimeError, match="DB corrupt"):
            fetch_gig_tracks(gig_tracks, db_path=fake_db_path)

    broken_db.session.close.assert_called_once()
