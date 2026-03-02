import yaml
from pathlib import Path
from unittest.mock import patch

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


# ── AI model config getters ──────────────────────────────────────────


def test_get_ai_chat_model_default(tmp_path, monkeypatch):
    """Returns default gpt-4.1-mini when no config or env is set."""
    monkeypatch.delenv("DJLIB_AI_CHAT_MODEL", raising=False)
    # Point _CANDIDATES to non-existent paths so no yaml is loaded
    monkeypatch.setattr(config, "_CANDIDATES", [tmp_path / "nope.yml"])
    assert config.get_ai_chat_model() == "gpt-4.1-mini"


def test_get_ai_quick_model_default(tmp_path, monkeypatch):
    """Returns default gpt-4o-mini when no config or env is set."""
    monkeypatch.delenv("DJLIB_AI_QUICK_MODEL", raising=False)
    monkeypatch.setattr(config, "_CANDIDATES", [tmp_path / "nope.yml"])
    assert config.get_ai_quick_model() == "gpt-4o-mini"


def test_get_ai_chat_model_from_env(monkeypatch):
    """Env var DJLIB_AI_CHAT_MODEL takes priority."""
    monkeypatch.setenv("DJLIB_AI_CHAT_MODEL", "gpt-4o")
    assert config.get_ai_chat_model() == "gpt-4o"


def test_get_ai_quick_model_from_env(monkeypatch):
    """Env var DJLIB_AI_QUICK_MODEL takes priority."""
    monkeypatch.setenv("DJLIB_AI_QUICK_MODEL", "gpt-4o")
    assert config.get_ai_quick_model() == "gpt-4o"


def test_get_ai_chat_model_from_yaml(tmp_path, monkeypatch):
    """Reads ai_chat_model from config YAML file."""
    monkeypatch.delenv("DJLIB_AI_CHAT_MODEL", raising=False)
    cfg_file = tmp_path / "config.local.yml"
    cfg_file.write_text(yaml.dump({"ai_chat_model": "o3-mini"}))
    monkeypatch.setattr(config, "_CANDIDATES", [cfg_file])
    assert config.get_ai_chat_model() == "o3-mini"


def test_get_ai_quick_model_from_yaml(tmp_path, monkeypatch):
    """Reads ai_quick_model from config YAML file."""
    monkeypatch.delenv("DJLIB_AI_QUICK_MODEL", raising=False)
    cfg_file = tmp_path / "config.local.yml"
    cfg_file.write_text(yaml.dump({"ai_quick_model": "gpt-4.1-nano"}))
    monkeypatch.setattr(config, "_CANDIDATES", [cfg_file])
    assert config.get_ai_quick_model() == "gpt-4.1-nano"


def test_get_ai_model_env_overrides_yaml(tmp_path, monkeypatch):
    """Env var takes priority over YAML config for AI models."""
    cfg_file = tmp_path / "config.local.yml"
    cfg_file.write_text(yaml.dump({"ai_chat_model": "from-yaml"}))
    monkeypatch.setattr(config, "_CANDIDATES", [cfg_file])
    monkeypatch.setenv("DJLIB_AI_CHAT_MODEL", "from-env")
    assert config.get_ai_chat_model() == "from-env"
