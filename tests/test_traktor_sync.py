from __future__ import annotations

from pathlib import Path

from traktor_nml_utils import TraktorCollection
from traktor_nml_utils.models.collection import (
    Collectiontype,
    Entrytype,
    Headtype,
    Infotype,
    Locationtype,
    ModificationInfotype,
    Nml,
    Nodetype,
    Playliststype,
    Setstype,
)
from xsdata.formats.dataclass.serializers import XmlSerializer

from djlib import external_sync


def _write_collection(path: Path, entries: list[Entrytype]) -> None:
    nml = Nml(
        head=Headtype(company="Native Instruments", program="Traktor"),
        musicfolders=None,
        collection=Collectiontype(entry=entries, entries=len(entries)),
        sets=Setstype(set=[], entries=0),
        playlists=Playliststype(node=Nodetype(type="FOLDER", name="Root")),
        indexing=None,
        sorting_order=[],
        version=19,
    )
    payload = XmlSerializer().render(nml)
    payload = "\n".join(line.lstrip() for line in payload.splitlines())
    path.write_text(payload, encoding="utf-8")


def _entry(audio_id: str, dir_attr: str, file_name: str) -> Entrytype:
    return Entrytype(
        location=Locationtype(dir=dir_attr, file=file_name, volume="Macintosh HD", volumeid=""),
        modification_info=ModificationInfotype(author_type="user"),
        info=Infotype(import_date="2024/01/01"),
        audio_id=audio_id,
        title="Old",
        artist="Artist",
        modified_date="2024/01/01",
        modified_time=0,
    )


def test_add_tracks_to_traktor_updates_existing(tmp_path: Path) -> None:
    collection_path = tmp_path / "collection.nml"
    existing = _entry("track-1", "/:Users/:old/:", "old.mp3")
    _write_collection(collection_path, [existing])

    new_file = tmp_path / "LIBRARY" / "updated.mp3"
    new_file.parent.mkdir(parents=True, exist_ok=True)
    new_file.write_text("audio", encoding="utf-8")

    added, updated = external_sync.add_tracks_to_traktor(
        collection_path,
        [
            {
                "file_path": str(new_file),
                "traktor_id": "track-1",
                "title": "Updated",
                "artist": "Artist",
            }
        ],
        dry_run=False,
        update_existing=True,
    )

    assert (added, updated) == (0, 1)

    parsed = TraktorCollection(collection_path)
    entry = parsed.nml.collection.entry[0]
    assert entry.location.file == new_file.name
    assert entry.location.dir == external_sync._format_traktor_dir(new_file.parent)

    backups = list(collection_path.parent.glob("collection.nml.djlib-backup-*"))
    assert backups, "backup copy should be created before saving"


def test_add_tracks_to_traktor_appends_new_entry(tmp_path: Path) -> None:
    collection_path = tmp_path / "collection.nml"
    _write_collection(collection_path, [])

    fresh_file = tmp_path / "LIBRARY" / "fresh.mp3"
    fresh_file.parent.mkdir(parents=True, exist_ok=True)
    fresh_file.write_text("audio", encoding="utf-8")

    added, updated = external_sync.add_tracks_to_traktor(
        collection_path,
        [
            {
                "file_path": str(fresh_file),
                "traktor_id": "track-new",
                "title": "Fresh",
                "artist": "Artist",
                "bpm": "125",
            }
        ],
        dry_run=False,
        update_existing=True,
    )

    assert (added, updated) == (1, 0)

    parsed = TraktorCollection(collection_path)
    assert parsed.nml.collection.entries == 1
    entry = parsed.nml.collection.entry[0]
    # audio_id is intentionally None for new entries — Traktor generates fingerprint on analysis
    assert entry.audio_id is None
    assert entry.title == "Fresh"
    assert entry.location.dir == external_sync._format_traktor_dir(fresh_file.parent)


def test_add_tracks_to_traktor_dry_run_leaves_file_untouched(tmp_path: Path) -> None:
    collection_path = tmp_path / "collection.nml"
    entry = _entry("track-1", "/:Users/:old/:", "old.mp3")
    _write_collection(collection_path, [entry])

    moved_file = tmp_path / "LIBRARY" / "moved.mp3"
    moved_file.parent.mkdir(parents=True, exist_ok=True)
    moved_file.write_text("audio", encoding="utf-8")

    original = collection_path.read_text(encoding="utf-8")

    added, updated = external_sync.add_tracks_to_traktor(
        collection_path,
        [
            {
                "file_path": str(moved_file),
                "traktor_id": "track-1",
            }
        ],
        dry_run=True,
        update_existing=True,
    )

    assert (added, updated) == (0, 1)
    assert collection_path.read_text(encoding="utf-8") == original
    assert not list(collection_path.parent.glob("collection.nml.djlib-backup-*"))
