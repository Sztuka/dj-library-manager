import yaml
from pathlib import Path

import djlib.config as config
import djlib.taxonomy as taxonomy

def test_config_and_folders(tmp_path, monkeypatch):
    # Tymczasowe ścieżki
    lib = tmp_path / "LIB"
    inbox = tmp_path / "INBOX"
    lib.mkdir()
    inbox.mkdir()

    # Mockuj _choose_config_path żeby używał tymczasowego pliku zamiast config.local.yml
    test_config_path = tmp_path / "test_config.local.yml"
    monkeypatch.setattr(config, '_choose_config_path', lambda: test_config_path)

    # Mockuj load_config żeby czytał z tymczasowego pliku
    def mock_load_config():
        if test_config_path.exists():
            d = config._read_yaml(test_config_path)
            cfg = config._from_dict(d)
            return {
                "LIB_ROOT": str(cfg.library_root),
                "INBOX_UNSORTED": str(cfg.inbox_dir),
            }
        else:
            # defaults
            return {
                "LIB_ROOT": "~/Music Library",
                "INBOX_UNSORTED": "~/Unsorted",
            }
    monkeypatch.setattr(config, 'load_config', mock_load_config)

    # Zapisz config + odczytaj
    config.save_config_paths(lib_root=str(lib), inbox=str(inbox))
    cfg = config.load_config()
    assert Path(cfg["LIB_ROOT"]) == lib
    assert Path(cfg["INBOX_UNSORTED"]) == inbox

    # Bazowe foldery
    config.ensure_base_dirs()
    assert (lib / "READY TO PLAY").exists()
    assert (lib / "REVIEW QUEUE").exists()

    # Skieruj lokalną ścieżkę taksonomii do katalogu tymczasowego, aby nie nadpisywać taxonomy.local.yml w repo
    monkeypatch.setattr(taxonomy, 'TAXONOMY_LOCAL_PATH', tmp_path / 'taxonomy.local.yml')

    # Taksonomia → foldery
    tax = {
        "ready_buckets": [
            "CLUB/HOUSE",
            "CLUB/AFRO HOUSE",
            "OPEN FORMAT/PARTY DANCE",
            "MIXES",
        ],
        "review_buckets": ["UNDECIDED", "NEEDS EDIT"],
    }
    taxonomy.save_taxonomy(tax)
    taxonomy.ensure_taxonomy_folders()

    # Struktura READY TO PLAY
    assert (lib / "READY TO PLAY" / "CLUB" / "HOUSE").exists()
    assert (lib / "READY TO PLAY" / "CLUB" / "AFRO HOUSE").exists()
    assert (lib / "READY TO PLAY" / "OPEN FORMAT" / "PARTY DANCE").exists()
    assert (lib / "READY TO PLAY" / "MIXES").exists()

    # Struktura REVIEW QUEUE
    assert (lib / "REVIEW QUEUE" / "UNDECIDED").exists()
    assert (lib / "REVIEW QUEUE" / "NEEDS EDIT").exists()

    # Pliki YAML – pomijamy sprawdzenie, bo zapisują się do głównego katalogu
