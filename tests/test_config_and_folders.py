import yaml
from pathlib import Path

import djlib.webapp as webapp  # testujemy publiczne API webapp

def test_config_and_folders(tmp_path, monkeypatch):
    # Tymczasowe ścieżki
    lib = tmp_path / "LIB"
    inbox = tmp_path / "INBOX"
    lib.mkdir()
    inbox.mkdir()

    # Przekieruj CWD, aby fallbacki pisały config.yml/taxonomy.yml do tmp
    monkeypatch.chdir(tmp_path)

    # Zapisz config + odczytaj
    webapp.save_config_paths(lib_root=str(lib), inbox=str(inbox))
    cfg = webapp.load_config()
    assert Path(cfg["LIB_ROOT"]) == lib
    assert Path(cfg["INBOX_UNSORTED"]) == inbox

    # Bazowe foldery
    webapp.ensure_base_dirs()
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
    webapp.save_taxonomy(tax)
    webapp.ensure_taxonomy_folders()

    # Struktura READY TO PLAY
    assert (lib / "READY TO PLAY" / "CLUB" / "HOUSE").exists()
    assert (lib / "READY TO PLAY" / "CLUB" / "AFRO HOUSE").exists()
    assert (lib / "READY TO PLAY" / "OPEN FORMAT" / "PARTY DANCE").exists()
    assert (lib / "READY TO PLAY" / "MIXES").exists()

    # Struktura REVIEW QUEUE
    assert (lib / "REVIEW QUEUE" / "UNDECIDED").exists()
    assert (lib / "REVIEW QUEUE" / "NEEDS EDIT").exists()

    # Pliki YAML powinny istnieć i być poprawne
    assert (tmp_path / "config.yml").exists()
    assert (tmp_path / "taxonomy.yml").exists()
    yaml.safe_load((tmp_path / "taxonomy.yml").read_text("utf-8"))
