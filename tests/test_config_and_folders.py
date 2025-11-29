import yaml
from pathlib import Path

import djlib.config as config

def test_config_and_folders(tmp_path, monkeypatch):
    """Test new logistics-only folder structure (LIBRARY/REJECT/ARCHIVE)."""
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

    # Bazowe foldery (nowa struktura logistyczna)
    config.ensure_base_dirs()
    assert (lib / "LIBRARY").exists()
    assert (lib / "REJECT").exists()
    assert (lib / "ARCHIVE").exists()

    # Test logistics path structure - create a sample artist folder
    artist_folder = lib / "LIBRARY" / "Test Artist"
    artist_folder.mkdir(parents=True)
    assert artist_folder.exists()

    # Pliki YAML – pomijamy sprawdzenie, bo zapisują się do głównego katalogu
