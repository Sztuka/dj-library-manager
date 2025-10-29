from __future__ import annotations
import os
import platform
from pathlib import Path

APP_NAME = "dj-library-manager"

def _xdg_config_home() -> Path:
    if platform.system() == "Windows":
        return Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
    return Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))

def get_config_path() -> Path:
    """
    Lokalizacja config.yml:
    - jeśli ustawione DJLIB_CONFIG_FILE → użyj,
    - w przeciwnym razie XDG (~/.config/dj-library-manager/config.yml) lub APPDATA.
    """
    env = os.getenv("DJLIB_CONFIG_FILE")
    return Path(env).expanduser() if env else (_xdg_config_home() / APP_NAME / "config.yml")

def get_taxonomy_path() -> Path:
    """
    Lokalizacja taxonomy.yml:
    - jeśli ustawione DJLIB_TAXONOMY_FILE → użyj,
    - w przeciwnym razie obok config.yml (…/taxonomy.yml).
    """
    env = os.getenv("DJLIB_TAXONOMY_FILE")
    if env:
        return Path(env).expanduser()
    return get_config_path().with_name("taxonomy.yml")

def ensure_app_dir() -> Path:
    d = get_config_path().parent
    d.mkdir(parents=True, exist_ok=True)
    return d
