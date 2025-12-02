from __future__ import annotations

from pathlib import Path

import pytest

from djlib import external_sync


class _FakeContent:
    def __init__(self) -> None:
        self.ID = 1
        self.FolderPath = "/old/path/oldname.mp3"
        self.FileNameL = "oldname.mp3"


class _FakeRekordbox:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.content = _FakeContent()
        self.update_calls: list[str] = []
        self.commits = 0
        self.closed = False

    def get_content(self, ID: int):  # noqa: N802 (matching pyrekordbox API)
        return self.content if int(ID) == self.content.ID else None

    def update_content_path(self, content, new_path, **kwargs):  # noqa: ANN001
        self.update_calls.append(new_path)
        content.FolderPath = new_path

    def add_content(self, *_args, **_kwargs):  # noqa: D401, ANN002, ANN003
        """Unneeded in this test but part of API."""
        return None

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def fake_rekordbox_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    fake_home = tmp_path / "home"
    db_dir = fake_home / "Library" / "Pioneer" / "rekordbox"
    db_dir.mkdir(parents=True)
    (db_dir / "master.db").write_text("test")

    created_instances: list[_FakeRekordbox] = []

    class _Factory(_FakeRekordbox):
        def __init__(self, path: Path) -> None:  # noqa: D401, ANN101
            super().__init__(path)
            created_instances.append(self)

    monkeypatch.setattr(external_sync, "Rekordbox6Database", _Factory)
    monkeypatch.setattr(external_sync.Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(external_sync, "PYREKORDBOX_AVAILABLE", True)

    return created_instances


def test_add_tracks_updates_filename(tmp_path: Path, fake_rekordbox_env):
    new_file = tmp_path / "LIBRARY" / "New Name.mp3"
    new_file.parent.mkdir(parents=True, exist_ok=True)
    new_file.write_text("audio")

    result = external_sync.add_tracks_to_rekordbox(
        [
            {
                "file_path": str(new_file),
                "rekordbox_id": "1",
                "artist": "Artist",
                "title": "Title",
                "bpm": "120",
                "key": "8A",
            }
        ],
        dry_run=False,
        update_existing=True,
    )

    fake_db = fake_rekordbox_env[0]
    assert fake_db.content.FolderPath == str(new_file)
    assert fake_db.content.FileNameL == new_file.name
    assert result == (0, 1)
    assert fake_db.commits == 1
    assert fake_db.closed
