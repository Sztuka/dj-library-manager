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


def _fake_content(rb_id, rating=204, play_count=5, bpm=12800, cues=None):
    return SimpleNamespace(
        ID=rb_id,
        Rating=rating,
        DJPlayCount=play_count,
        BPM=bpm,
        Cues=cues or [],
    )


def _make_db(contents):
    """Build a mock Rekordbox6Database that returns `contents` from session.query().filter().all()."""
    db = MagicMock()
    db.session.query.return_value.filter.return_value.all.return_value = contents
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

    with patch("pyrekordbox.Rekordbox6Database") as mock_cls:
        result = fetch_gig_tracks(gig_tracks, db_path=fake_db_path)

    assert result == {}
    mock_cls.assert_not_called()  # no DB open if no valid rekordbox_id


def test_fetch_skips_track_not_in_db(tmp_path):
    fake_db_path = tmp_path / "master6.db"
    fake_db_path.touch()

    # DB returns content with ID=99, but we requested ID=42
    content = _fake_content(rb_id=99)
    gig_tracks = [{"track_id": "tid1", "rekordbox_id": "42"}]

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

    # DB returns only requested tracks (IN filter) — simulate that behaviour
    contents = [
        _fake_content(rb_id=1, rating=51,  play_count=3),
        _fake_content(rb_id=2, rating=255, play_count=20),
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


def test_fetch_uses_targeted_query_not_full_scan(tmp_path):
    """fetch_gig_tracks must use session.query().filter().all(), not get_content()."""
    fake_db_path = tmp_path / "master6.db"
    fake_db_path.touch()

    content = _fake_content(rb_id=1)
    gig_tracks = [{"track_id": "tid1", "rekordbox_id": "1"}]
    mock_db = _make_db([content])

    with patch("pyrekordbox.Rekordbox6Database", return_value=mock_db):
        fetch_gig_tracks(gig_tracks, db_path=fake_db_path)

    mock_db.session.query.assert_called_once()
    mock_db.session.query.return_value.filter.assert_called_once()
    mock_db.get_content.assert_not_called()


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


def test_fetch_bpm_none_returns_empty(tmp_path):
    fake_db_path = tmp_path / "master6.db"
    fake_db_path.touch()

    content = _fake_content(rb_id=1, bpm=None)
    gig_tracks = [{"track_id": "tid1", "rekordbox_id": "1"}]

    with patch("pyrekordbox.Rekordbox6Database", return_value=_make_db([content])):
        result = fetch_gig_tracks(gig_tracks, db_path=fake_db_path)

    assert result["tid1"]["bpm"] == ""


# ── last_played not present ────────────────────────────────────────────────────


def test_fetch_result_has_no_last_played():
    """last_played must not appear in results — DjmdContent has no LastPlayed column."""
    # This is a static check — verify the returned dict keys
    from djlib.rekordbox_reader import _content_to_dict
    content = SimpleNamespace(ID=1, Rating=204, DJPlayCount=5, BPM=12800, Cues=[])
    result = _content_to_dict(content)
    assert "last_played" not in result


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
    broken_db.session.query.return_value.filter.return_value.all.side_effect = RuntimeError("DB corrupt")
    broken_db.session.close.return_value = None

    gig_tracks = [{"track_id": "tid1", "rekordbox_id": "1"}]

    with patch("pyrekordbox.Rekordbox6Database", return_value=broken_db):
        with pytest.raises(RuntimeError, match="DB corrupt"):
            fetch_gig_tracks(gig_tracks, db_path=fake_db_path)

    broken_db.session.close.assert_called_once()


def test_fetch_invalid_rekordbox_id_skipped(tmp_path):
    """Non-integer rekordbox_id must be skipped with a warning, not crash."""
    fake_db_path = tmp_path / "master6.db"
    fake_db_path.touch()

    gig_tracks = [
        {"track_id": "tid1", "rekordbox_id": "not-an-int"},
        {"track_id": "tid2", "rekordbox_id": "42"},
    ]
    content = _fake_content(rb_id=42)

    with patch("pyrekordbox.Rekordbox6Database", return_value=_make_db([content])):
        result = fetch_gig_tracks(gig_tracks, db_path=fake_db_path)

    assert "tid1" not in result
    assert "tid2" in result
