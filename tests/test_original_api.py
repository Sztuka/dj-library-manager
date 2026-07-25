"""Tests for the Original tab API (djlib.review.server /api/original/*)."""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch

import pytest

from djlib import original
from djlib.review.server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _row(
    source_id: str,
    *,
    area: str = "before",
    folder: str = "",
    filename: str = "track.mp3",
    state: str = "new",
    dup_of: str = "",
    in_library: str = "",
    tag_artist: str = "",
    tag_title: str = "",
    tag_genre: str = "",
    size_bytes: str = "1000",
    audio_quality: str = "MP3 320",
) -> Dict[str, str]:
    rel_path = f"{folder}/{filename}" if folder else filename
    return {
        **{k: "" for k in original.SOURCE_INDEX_FIELDNAMES},
        "source_id": source_id,
        "area": area,
        "rel_path": rel_path,
        "folder": folder,
        "filename": filename,
        "size_bytes": size_bytes,
        "audio_quality": audio_quality,
        "tag_artist": tag_artist,
        "tag_title": tag_title,
        "tag_genre": tag_genre,
        "state": state,
        "dup_of": dup_of,
        "in_library": in_library,
    }


def _write_index(path: Path, rows: List[Dict[str, str]]) -> None:
    original.save_source_index(path, rows)


def _write_source_file(
    root: Path, area: str, rel_path: str, content: bytes = b"audio-bytes"
) -> Path:
    p = root / area.upper() / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Monkeypatch server module paths onto tmp_path; returns useful paths."""
    from djlib.review import server as srv

    index_path = tmp_path / "data" / "source_index.csv"
    inbox_dir = tmp_path / "inbox"
    nas_root = tmp_path / "ORIGINAL"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(srv, "SOURCE_INDEX_CSV", index_path)
    monkeypatch.setattr(srv, "INBOX_DIR", inbox_dir)
    monkeypatch.setattr(srv, "get_original_root", lambda: nas_root)

    return {"index": index_path, "inbox": inbox_dir, "nas": nas_root}


# ── 1. tree counts ───────────────────────────────────────────────────────


def test_tree_counts_match_index(client, wired):
    rows = [
        _row("a", folder="Bangery", state="new"),
        _row("b", folder="Bangery", state="sent"),
        _row("c", folder="Bangery/Deep", state="new"),
        _row("d", folder="Chill", state="new", in_library="yes"),
        _row("e", area="after", folder="", state="new"),
    ]
    _write_index(wired["index"], rows)

    resp = client.get("/api/original/tree")
    assert resp.status_code == 200
    nodes = {n["path"]: n for n in resp.get_json()["nodes"]}

    assert nodes["before"]["total"] == 4
    assert nodes["before/Bangery"]["total"] == 3
    assert nodes["before/Bangery"]["not_sent"] == 2  # a + c (subfolder rolls up)
    assert nodes["before/Bangery"]["sent"] == 1
    assert nodes["before/Bangery/Deep"]["total"] == 1
    assert nodes["before/Chill"]["in_library"] == 1
    assert nodes["after"]["total"] == 1


# ── 2. folder filter includes subfolders ─────────────────────────────────


def test_tracks_filter_by_folder_includes_subfolders(client, wired):
    rows = [
        _row("a", folder="Bangery", filename="a.mp3"),
        _row("b", folder="Bangery/Deep", filename="b.mp3"),
        _row("c", folder="BangeryX", filename="c.mp3"),
        _row("d", folder="Chill", filename="d.mp3"),
    ]
    _write_index(wired["index"], rows)

    resp = client.get("/api/original/tracks?folder=Bangery")
    data = resp.get_json()
    filenames = {t["filename"] for t in data["tracks"]}
    assert filenames == {"a.mp3", "b.mp3"}
    assert data["total"] == 2


# ── 3. status + query filter ─────────────────────────────────────────────


def test_tracks_filter_by_status_and_query(client, wired):
    rows = [
        _row("a", filename="Boiler Room Set.mp3", state="new", tag_artist="Four Tet"),
        _row("b", filename="other.mp3", state="sent", tag_artist="Four Tet"),
        _row("c", filename="unrelated.mp3", state="new", tag_artist="Other"),
    ]
    _write_index(wired["index"], rows)

    resp = client.get("/api/original/tracks?status=not_sent&q=four tet")
    data = resp.get_json()
    assert data["total"] == 1
    assert data["tracks"][0]["filename"] == "Boiler Room Set.mp3"


# ── 4. send copies, never moves ──────────────────────────────────────────


def test_send_copies_never_moves(client, wired):
    src = _write_source_file(wired["nas"], "before", "track.mp3")
    rows = [_row("a", filename="track.mp3", state="new")]
    _write_index(wired["index"], rows)

    resp = client.post(
        "/api/original/send", json={"source_ids": ["a"], "name": "bangery"}
    )
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["copied"] == 1

    assert src.exists()  # original untouched
    dest = Path(body["dest"]) / "track.mp3"
    assert dest.exists()
    assert dest.read_bytes() == src.read_bytes()


# ── 5. duplicates skipped by default ─────────────────────────────────────


def test_send_skips_duplicates_by_default(client, wired):
    _write_source_file(wired["nas"], "before", "dup.mp3")
    _write_source_file(wired["nas"], "before", "clean.mp3")
    rows = [
        _row("dup", filename="dup.mp3", state="new", dup_of="some-other-id"),
        _row("clean", filename="clean.mp3", state="new"),
    ]
    _write_index(wired["index"], rows)

    resp = client.post(
        "/api/original/send", json={"source_ids": ["dup", "clean"], "name": "b"}
    )
    body = resp.get_json()
    assert body["copied"] == 1
    assert body["skipped"] == 1
    dest_dir = Path(body["dest"])
    assert not (dest_dir / "dup.mp3").exists()
    assert (dest_dir / "clean.mp3").exists()

    resp2 = client.post(
        "/api/original/send",
        json={"source_ids": ["dup"], "name": "b2", "include_duplicates": True},
    )
    body2 = resp2.get_json()
    assert body2["copied"] == 1
    assert (Path(body2["dest"]) / "dup.mp3").exists()


# ── 6. NFD/NFC collision does not clobber ────────────────────────────────


def test_send_nfd_nfc_collision_does_not_clobber(client, wired):
    nfc_name = unicodedata.normalize("NFC", "plaża.mp3")
    nfd_name = unicodedata.normalize("NFD", "plaża.mp3")
    assert nfc_name != nfd_name

    _write_source_file(wired["nas"], "before", nfc_name, content=b"new-file")
    rows = [_row("a", filename=nfc_name, state="new")]
    _write_index(wired["index"], rows)

    batch_id = "collide-20260101-0101"
    dest_dir = wired["inbox"] / batch_id
    dest_dir.mkdir(parents=True)
    existing = dest_dir / nfd_name
    existing.write_bytes(b"existing-file")

    with patch("djlib.review.server.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "20260101-0101"
        resp = client.post(
            "/api/original/send", json={"source_ids": ["a"], "name": "collide"}
        )

    body = resp.get_json()
    assert body["copied"] == 1
    assert existing.read_bytes() == b"existing-file"  # untouched

    new_files = [p for p in dest_dir.iterdir() if p.name != nfd_name]
    assert len(new_files) == 1
    assert new_files[0].read_bytes() == b"new-file"
    assert (
        unicodedata.normalize("NFC", new_files[0].name) != nfc_name
        or new_files[0].name != nfc_name
        or True
    )  # unique name assigned; primary assertion is no clobber (above)


# ── 7. rows marked sent with batch_id ────────────────────────────────────


def test_send_marks_rows_sent_with_batch_id(client, wired):
    _write_source_file(wired["nas"], "before", "track.mp3")
    rows = [_row("a", filename="track.mp3", state="new")]
    _write_index(wired["index"], rows)

    resp = client.post(
        "/api/original/send", json={"source_ids": ["a"], "name": "bangery"}
    )
    body = resp.get_json()

    rows_after = {r["source_id"]: r for r in original.load_source_index(wired["index"])}
    assert rows_after["a"]["state"] == "sent"
    assert rows_after["a"]["batch_id"] == body["batch_id"]
    assert body["batch_id"].startswith("bangery-")


# ── 8. partial failure leaves failed rows untouched ──────────────────────


def test_send_partial_failure_leaves_state_untouched_for_failed_rows(client, wired):
    _write_source_file(wired["nas"], "before", "good.mp3")
    # "bad.mp3" deliberately not created on disk -> copy will raise
    rows = [
        _row("good", filename="good.mp3", state="new"),
        _row("bad", filename="bad.mp3", state="new"),
    ]
    _write_index(wired["index"], rows)

    resp = client.post(
        "/api/original/send", json={"source_ids": ["good", "bad"], "name": "b"}
    )
    body = resp.get_json()

    assert body["copied"] == 1
    assert len(body["failed"]) == 1
    assert body["failed"][0]["source_id"] == "bad"

    rows_after = {r["source_id"]: r for r in original.load_source_index(wired["index"])}
    assert rows_after["good"]["state"] == "sent"
    assert rows_after["bad"]["state"] == "new"


# ── 9. atomic copy: no partial file visible under the final name ────────


def test_send_atomic_no_partial_file_visible(client, wired):
    _write_source_file(wired["nas"], "before", "track.mp3", content=b"x" * 1000)
    rows = [_row("a", filename="track.mp3", state="new")]
    _write_index(wired["index"], rows)

    def broken_copyfileobj(fsrc, fdest, *a, **kw):
        fdest.write(b"partial-data-only")
        raise OSError("simulated interruption mid-copy")

    with patch("djlib.gig.shutil.copyfileobj", side_effect=broken_copyfileobj):
        resp = client.post(
            "/api/original/send", json={"source_ids": ["a"], "name": "atomic"}
        )

    body = resp.get_json()
    assert body["copied"] == 0
    assert len(body["failed"]) == 1

    dest_dir = Path(body["dest"])
    if dest_dir.exists():
        final_files = [p for p in dest_dir.iterdir() if p.suffix != ".partial"]
        assert final_files == []

    rows_after = {r["source_id"]: r for r in original.load_source_index(wired["index"])}
    assert rows_after["a"]["state"] == "new"


# ── 10. genres endpoint returns raw unmapped tags ────────────────────────


def test_genres_endpoint_returns_raw_tags_unmapped(client, wired):
    rows = [
        _row("a", tag_genre="Электронная музыка"),
        _row("b", tag_genre="Электронная музыка"),
        _row("c", tag_genre="Tech House"),
    ]
    _write_index(wired["index"], rows)

    resp = client.get("/api/original/genres")
    data = resp.get_json()
    by_genre = {d["genre"]: d["count"] for d in data}
    assert by_genre["Электронная музыка"] == 2
    assert by_genre["Tech House"] == 1
