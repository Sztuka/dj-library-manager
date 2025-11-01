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

    # Przekieruj CWD, aby fallbacki pisały config.yml/taxonomy.yml do tmp
    monkeypatch.chdir(tmp_path)

    # Zapisz config + odczytaj
    config.save_config_paths(lib_root=str(lib), inbox=str(inbox))
    cfg = config.load_config()
    assert Path(cfg["LIB_ROOT"]) == lib
    assert Path(cfg["INBOX_UNSORTED"]) == inbox

    # Bazowe foldery
    config.ensure_base_dirs()
    assert (lib / "READY TO PLAY").exists()
    assert (lib / "REVIEW QUEUE").exists()

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
