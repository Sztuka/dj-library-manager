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

def _to_dict(cfg: AppConfig) -> Dict[str, Any]:
    return {
        "library_root": str(cfg.library_root),
        "inbox_dir": str(cfg.inbox_dir),
    }

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
    _write_yaml(dest, _to_dict(cfg))
    # Aktualizuj globalne zmienne
    global _CONFIG, LIB_ROOT, INBOX_DIR, READY_TO_PLAY_DIR, REVIEW_QUEUE_DIR, LOGS_DIR, CSV_PATH
    _CONFIG = cfg
    LIB_ROOT = cfg.library_root
    INBOX_DIR = cfg.inbox_dir
    READY_TO_PLAY_DIR = LIB_ROOT / "READY TO PLAY"
    REVIEW_QUEUE_DIR = LIB_ROOT / "REVIEW QUEUE"
    LOGS_DIR = LIB_ROOT / "LOGS"
    CSV_PATH = LIB_ROOT / "library.csv"
