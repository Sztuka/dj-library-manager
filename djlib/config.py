from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Tuple, Dict, Any
import os
import sys
import yaml

# ---------------------------
# Lokalizacja pliku konfiga
# ---------------------------

def _repo_root() -> Path:
    if hasattr(sys, "_MEIPASS"):
        # PyInstaller bundle
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1]

_REPO = _repo_root()
_CANDIDATES = [
    _REPO / "config.local.yml",                 # preferowane w repo (lokalnie, w .gitignore)
    Path.home() / ".djlib_manager" / "config.yml",  # alternatywnie w HOME
]

def _first_existing(paths: list[Path]) -> Path | None:
    for p in paths:
        if p.exists():
            return p
    return None

# ---------------------------
# Model konfigu
# ---------------------------

@dataclass
class AppConfig:
    library_root: Path          # gdzie tworzymy strukturę (READY_TO_PLAY/…, REVIEW_QUEUE/…, LOGS/, library.csv)
    inbox_dir: Path             # skąd skanujemy nowe pliki (może być poza library_root)

def _expand(p: str | Path) -> Path:
    return Path(str(p)).expanduser().resolve()

def _defaults() -> AppConfig:
    lib = _expand("~/Music_DJ")
    inbox = lib / "INBOX_UNSORTED"
    return AppConfig(library_root=lib, inbox_dir=inbox)

# ---------------------------
# I/O YAML
# ---------------------------

def _read_yaml(p: Path) -> Dict[str, Any]:
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def _write_yaml(p: Path, data: Dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

def _to_dict(cfg: AppConfig, extras: Dict[str, Any] | None = None) -> Dict[str, Any]:
    base = {
        "library_root": str(cfg.library_root),
        "inbox_dir": str(cfg.inbox_dir),
    }
    if extras:
        base.update(extras)
    return base

def _from_dict(d: Dict[str, Any]) -> AppConfig:
    lib = _expand(d.get("library_root", "~/Music_DJ"))
    inbox = _expand(d.get("inbox_dir", lib / "INBOX_UNSORTED"))
    return AppConfig(library_root=lib, inbox_dir=inbox)

# ---------------------------
# Interaktywna konfiguracja
# ---------------------------

def _prompt_path(question: str, default: Path) -> Path:
    print(f"{question}")
    print(f"[Enter] dla domyślnej ścieżki: {default}")
    raw = input("> ").strip()
    if not raw:
        return default
    return _expand(raw)

def _interactive_setup() -> AppConfig:
    print("\n=== DJ Library Manager – konfiguracja ===")
    print("Podaj ścieżki. Zawsze możesz to później zmienić, uruchamiając ponownie konfigurator.\n")

    d = _defaults()
    library_root = _prompt_path("Gdzie stworzyć strukturę biblioteki (READY TO PLAY/…, REVIEW QUEUE/…, LOGS, library.csv)?", d.library_root)
    inbox_dir    = _prompt_path("Gdzie znajduje się folder z nieposortowaną muzyką (INBOX, skanowany przez 'Scan')?", d.inbox_dir)

    cfg = AppConfig(library_root=library_root, inbox_dir=inbox_dir)
    print("\nWybrane:")
    print(f" • library_root: {cfg.library_root}")
    print(f" • inbox_dir:    {cfg.inbox_dir}\n")
    return cfg

def _choose_config_path() -> Path:
    # zapisujemy preferencyjnie w repo jako config.local.yml
    return _CANDIDATES[0]

def _load_or_setup() -> Tuple[AppConfig, Path]:
    existing = _first_existing(_CANDIDATES)
    if existing:
        return _from_dict(_read_yaml(existing)), existing
    # brak konfiga – pytamy użytkownika
    cfg = _interactive_setup()
    dest = _choose_config_path()
    _write_yaml(dest, _to_dict(cfg))
    return cfg, dest

def reconfigure() -> Tuple[AppConfig, Path]:
    """Wymuś ponowną konfigurację (używane przez scripts/configure.py)."""
    cfg = _interactive_setup()
    dest = _choose_config_path()
    _write_yaml(dest, _to_dict(cfg))
    return cfg, dest

# ---------------------------
# Init + ścieżki z konfiga
# ---------------------------

_CONFIG, CONFIG_FILE = _load_or_setup()

LIB_ROOT = _CONFIG.library_root
INBOX_DIR = _CONFIG.inbox_dir
READY_TO_PLAY_DIR = LIB_ROOT / "READY TO PLAY"
REVIEW_QUEUE_DIR  = LIB_ROOT / "REVIEW QUEUE"

LOGS_DIR = LIB_ROOT / "LOGS"
CSV_PATH = LIB_ROOT / "library.csv"

AUDIO_EXTS = {
    ".mp3", ".wav", ".aiff", ".aif", ".flac", ".m4a", ".aac", ".ogg", ".alac", ".wv"
}

def ensure_base_dirs() -> None:
    """Utwórz katalogi bazowe według obecnego konfiga."""
    for p in [LIB_ROOT, INBOX_DIR, REVIEW_QUEUE_DIR, READY_TO_PLAY_DIR, LOGS_DIR]:
        p.mkdir(parents=True, exist_ok=True)

def load_config() -> Dict[str, Any]:
    """Wczytaj aktualną konfigurację i zwróć jako słownik z kluczami LIB_ROOT i INBOX_UNSORTED."""
    existing = _first_existing(_CANDIDATES)
    if existing:
        cfg = _from_dict(_read_yaml(existing))
    else:
        cfg = _defaults()
    return {
        "LIB_ROOT": str(cfg.library_root),
        "INBOX_UNSORTED": str(cfg.inbox_dir),
    }

def save_config_paths(lib_root: str, inbox: str) -> None:
    """Zapisz ścieżki konfiguracji do pliku."""
    cfg = AppConfig(library_root=_expand(lib_root), inbox_dir=_expand(inbox))
    dest = _choose_config_path()
    existing = {}
    if dest.exists():
        existing = _read_yaml(dest)
    extras = {}
    # zachowaj inne klucze (np. acoustid_api_key)
    for k in ("acoustid_api_key",):
        if k in existing:
            extras[k] = existing[k]
    _write_yaml(dest, _to_dict(cfg, extras))

# ---------------------------
# Dodatkowe ustawienia (API Keys)
# ---------------------------

def set_acoustid_api_key(key: str) -> None:
    dest = _choose_config_path()
    # scal z istniejącymi danymi
    d = _read_yaml(dest) if dest.exists() else {}
    d["acoustid_api_key"] = str(key)
    _write_yaml(dest, d)

# Domyślny klucz aplikacji AcoustID (Application API key) – używany do lookup,
# NIE jest to User API key. Można nadpisać w config.local.yml lub przez env.
DEFAULT_ACOUSTID_APP_KEY = "ZQGds2YbFx"

def get_acoustid_api_key() -> str:
    """Zwraca klucz aplikacji AcoustID z configu, a jeśli go brak – wartość domyślną.
    Uwaga: do lookup wymagany jest Application API key (client), nie User API key.
    """
    # Najpierw spróbuj z plików konfiguracyjnych
    existing = _first_existing(_CANDIDATES)
    if existing:
        d = _read_yaml(existing)
        val = str(d.get("acoustid_api_key", "") or "").strip()
        if val:
            return val
    # W przeciwnym razie – domyślny klucz aplikacji
    return DEFAULT_ACOUSTID_APP_KEY

# Last.fm API
def get_lastfm_api_key() -> str:
    # ENV first
    env = os.getenv("DJLIB_LASTFM_API_KEY")
    if env:
        return env.strip()
    existing = _first_existing(_CANDIDATES)
    if existing:
        d = _read_yaml(existing)
        val = str(d.get("lastfm_api_key", "") or "").strip()
        if val:
            return val
    return ""

# Spotify API (Client Credentials)
def get_spotify_credentials() -> tuple[str, str]:
    cid = (os.getenv("DJLIB_SPOTIFY_CLIENT_ID") or "").strip()
    secret = (os.getenv("DJLIB_SPOTIFY_CLIENT_SECRET") or "").strip()
    if cid and secret:
        return cid, secret
    existing = _first_existing(_CANDIDATES)
    if existing:
        d = _read_yaml(existing)
        cid = str(d.get("spotify_client_id", "") or "").strip()
        secret = str(d.get("spotify_client_secret", "") or "").strip()
    return cid, secret

# Discogs API token (optional, improves rate limits)
def get_discogs_token() -> str:
    tok = (os.getenv("DJLIB_DISCOGS_TOKEN") or "").strip()
    if tok:
        return tok
    existing = _first_existing(_CANDIDATES)
    if existing:
        d = _read_yaml(existing)
        val = str(d.get("discogs_token", "") or "").strip()
        if val:
            return val
    return ""
