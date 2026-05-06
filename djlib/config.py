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
    library_root: Path          # Main library folder (~/Music Library)
    inbox_dir: Path             # Inbox for new files (~/Music Unsorted)
    # Removed: library_subdir, reject_subdir, archive_subdir
    # New model: separate root paths (REJECT_ROOT, ARCHIVE_ROOT) in config.yml

def _expand(p: str | Path) -> Path:
    return Path(str(p)).expanduser().resolve()

def _defaults() -> AppConfig:
    lib = _expand("~/Music Library")
    inbox = _expand("~/Music Unsorted")
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
        "LIB_ROOT": str(cfg.library_root),
        "INBOX_UNSORTED": str(cfg.inbox_dir),
    }
    if extras:
        base.update(extras)
    return base

def _from_dict(d: Dict[str, Any]) -> AppConfig:
    lib = _expand(d.get("LIB_ROOT") or d.get("library_root", "~/Music Library"))
    inbox = _expand(d.get("INBOX_UNSORTED") or d.get("inbox_dir", "~/Music Unsorted"))
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
    print("Struktura folderów:")
    print("  • ~/Music Library/      — główna biblioteka (zatwierdzone utwory, Artist/file.ext)")
    print("  • ~/Music Unsorted/     — folder wejściowy (nowe pliki do skanowania)")
    print("  • ~/Music Rejected/     — odrzucone (osobny folder, flat)")
    print("  • ~/Music Archive/      — archiwum (osobny folder, Artist/file.ext)")
    print()

    d = _defaults()
    library_root = _prompt_path("Gdzie jest główna biblioteka (Music Library)?", d.library_root)
    inbox_dir    = _prompt_path("Gdzie jest folder wejściowy (UNSORTED)?", d.inbox_dir)

    cfg = AppConfig(library_root=library_root, inbox_dir=inbox_dir)
    print("\nWybrane:")
    print(f" • library_root: {cfg.library_root}")
    print(f" • inbox_dir:    {cfg.inbox_dir}\n")
    return cfg

def _choose_config_path() -> Path:
    # zapisujemy preferencyjnie w repo jako config.local.yml
    return _CANDIDATES[0]

def _create_marker_files(cfg: AppConfig) -> None:
    """Utwórz ukryte pliki znaczników w folderach biblioteki i inbox."""
    import datetime
    
    marker_data = {
        "app": "DJLibraryManager",
        "version": "0.1",
        "created": datetime.datetime.now().isoformat(),
        "library_root": str(cfg.library_root),
        "inbox_dir": str(cfg.inbox_dir),
    }
    
    # Plik znacznika dla library root
    lib_marker = cfg.library_root / ".djlib_root"
    try:
        with lib_marker.open("w", encoding="utf-8") as f:
            yaml.safe_dump(marker_data, f, allow_unicode=True, sort_keys=False)
    except Exception as e:
        print(f"⚠ Nie udało się utworzyć pliku znacznika w library root: {e}")
    
    # Plik znacznika dla inbox (tylko jeśli to inny folder)
    if cfg.inbox_dir != cfg.library_root:
        inbox_marker = cfg.inbox_dir / ".djlib_inbox"
        try:
            with inbox_marker.open("w", encoding="utf-8") as f:
                yaml.safe_dump(marker_data, f, allow_unicode=True, sort_keys=False)
        except Exception as e:
            print(f"⚠ Nie udało się utworzyć pliku znacznika w inbox: {e}")

def _detect_from_markers() -> AppConfig | None:
    """Spróbuj wykryć konfigurację na podstawie plików znaczników."""
    
    def _check_marker_file(marker_path: Path) -> AppConfig | None:
        """Sprawdź pojedynczy plik znacznika."""
        if marker_path.exists():
            try:
                with marker_path.open("r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                lib_root = Path(data.get("library_root", ""))
                inbox_dir = Path(data.get("inbox_dir", ""))
                
                # Sprawdź czy ścieżki nadal istnieją
                if lib_root.exists() and inbox_dir.exists():
                    return AppConfig(library_root=lib_root, inbox_dir=inbox_dir)
            except Exception:
                pass
        return None
    
    # Najpierw sprawdź domyślne lokalizacje
    defaults = _defaults()
    default_root_marker = defaults.library_root / ".djlib_root"
    default_inbox_marker = defaults.inbox_dir / ".djlib_inbox"
    
    # Sprawdź .djlib_root w domyślnej library_root
    cfg = _check_marker_file(default_root_marker)
    if cfg:
        return cfg
    
    # Sprawdź .djlib_inbox w domyślnej inbox_dir
    cfg = _check_marker_file(default_inbox_marker)
    if cfg:
        return cfg
    
    # Potem sprawdź w bieżącym katalogu roboczym i jego rodzicach
    def _find_marker_in_path(path: Path, marker_name: str) -> Path | None:
        """Znajdź plik znacznika w podanej ścieżce lub jej rodzicach."""
        current = path
        for _ in range(5):  # maksymalnie 5 poziomów w górę
            marker = current / marker_name
            if marker.exists():
                return marker
            if current.parent == current:  # dotarliśmy do root
                break
            current = current.parent
        return None
    
    cwd = Path.cwd()
    
    # Najpierw szukaj .djlib_root
    root_marker = _find_marker_in_path(cwd, ".djlib_root")
    if root_marker:
        cfg = _check_marker_file(root_marker)
        if cfg:
            return cfg
    
    # Jeśli nie znaleziono .djlib_root, spróbuj .djlib_inbox
    inbox_marker = _find_marker_in_path(cwd, ".djlib_inbox")
    if inbox_marker:
        cfg = _check_marker_file(inbox_marker)
        if cfg:
            return cfg
    
    return None

def _load_or_setup() -> Tuple[AppConfig, Path]:
    existing = _first_existing(_CANDIDATES)
    if existing:
        return _from_dict(_read_yaml(existing)), existing
    
    # Spróbuj wykryć konfigurację z plików znaczników
    detected = _detect_from_markers()
    if detected:
        print("✓ Wykryto istniejącą konfigurację z plików znaczników:")
        print(f" • library_root: {detected.library_root}")
        print(f" • inbox_dir:    {detected.inbox_dir}")
        
        # Potwierdź z użytkownikiem
        response = input("Czy chcesz użyć tej konfiguracji? (Y/n): ").strip().lower()
        if response in ("", "y", "yes"):
            # Zapisz wykrytą konfigurację
            dest = _choose_config_path()
            _write_yaml(dest, _to_dict(detected))
            _create_marker_files(detected)
            return detected, dest
    
    # Brak wykrytej konfiguracji – pytaj użytkownika
    cfg = _interactive_setup()
    dest = _choose_config_path()
    _write_yaml(dest, _to_dict(cfg))
    _create_marker_files(cfg)  # Utwórz pliki znaczników
    return cfg, dest

def reconfigure() -> Tuple[AppConfig, Path]:
    """Wymuś ponowną konfigurację (używane przez cmd_configure)."""
    
    # 1. Najpierw sprawdź czy istnieje config.local.yml
    existing_config_file = _first_existing(_CANDIDATES)
    if existing_config_file:
        print("✓ Znaleziono istniejący plik konfiguracyjny:")
        print(f"  {existing_config_file}")
        existing_cfg = _from_dict(_read_yaml(existing_config_file))
        print(f" • library_root: {existing_cfg.library_root}")
        print(f" • inbox_dir:    {existing_cfg.inbox_dir}")
        print()
        
        try:
            choice = input("Czy chcesz użyć tej konfiguracji? [Y/n/edit]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "y"
        
        if choice in ("", "y", "yes"):
            print("✓ Używam istniejącej konfiguracji")
            return existing_cfg, existing_config_file
        elif choice in ("e", "edit"):
            print("\n→ Edycja konfiguracji...")
            cfg = _interactive_setup()
            dest = _choose_config_path()
            _write_yaml(dest, _to_dict(cfg))
            _create_marker_files(cfg)
            return cfg, dest
        # else: będzie poniżej próba detekcji
    
    # 2. Jeśli nie ma config.local.yml lub użytkownik nie chce go użyć,
    #    spróbuj wykryć strukturę z plików znaczników
    detected = _detect_from_markers()
    if detected:
        print("✓ Wykryto istniejącą strukturę biblioteki:")
        print(f" • library_root: {detected.library_root}")
        print(f" • inbox_dir:    {detected.inbox_dir}")
        print()
        
        try:
            choice = input("Czy chcesz użyć tej struktury? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "y"
        
        if choice in ("", "y", "yes"):
            print("✓ Używam wykrytej struktury")
            dest = _choose_config_path()
            _write_yaml(dest, _to_dict(detected))
            _create_marker_files(detected)
            return detected, dest
    
    # 3. Brak wykrytej struktury – nowa konfiguracja
    print("\n→ Tworzenie nowej konfiguracji...")
    cfg = _interactive_setup()
    dest = _choose_config_path()
    _write_yaml(dest, _to_dict(cfg))
    _create_marker_files(cfg)
    
    return cfg, dest

# ---------------------------
# Init + ścieżki z konfiga
# ---------------------------

_CONFIG, CONFIG_FILE = _load_or_setup()

LIB_ROOT = _CONFIG.library_root
INBOX_DIR = _CONFIG.inbox_dir

# Logistics directories are now separate root paths (configured in config.yml)
# No longer subfolders of LIB_ROOT

# LOGS_DIR - computed from config file at runtime, default is ./LOGS in repo
def _get_logs_dir() -> Path:
    """Get LOGS_DIR from config file, avoiding circular dependency."""
    existing = _first_existing(_CANDIDATES)
    if existing:
        try:
            data = _read_yaml(existing)
            logs_path = data.get("LOGS_DIR", str(_REPO / "LOGS"))
            return Path(logs_path).resolve()
        except Exception:
            pass
    return (_REPO / "LOGS").resolve()

LOGS_DIR = _get_logs_dir()

# library.csv stored in repo data/ folder (not in Music Library)
CSV_PATH = _REPO / "data" / "library.csv"

# library-archive.csv for archived tracks (separate from active library)
ARCHIVE_CSV_PATH = _REPO / "data" / "library-archive.csv"

# library-rejected.csv — persistent registry of rejected files (hash/fp)
# Used by cmd_scan to skip files that were previously rejected
REJECTED_CSV_PATH = _REPO / "data" / "library-rejected.csv"

# UNSORTED_CSV — staging file for unsorted tracks (migrated from .xlsx to .csv)
def _get_unsorted_csv() -> Path:
    """Get UNSORTED_CSV path from config file, avoiding circular dependency.

    Reads UNSORTED_CSV (preferred) or legacy UNSORTED_XLSX from config, then
    ensures the path ends with .csv.  If only an .xlsx path is configured,
    the extension is swapped to .csv — the actual migration of data happens
    lazily in unsorted.load_unsorted_rows().
    """
    existing = _first_existing(_CANDIDATES)
    if existing:
        try:
            data = _read_yaml(existing)
            # Try new key first, fall back to legacy
            raw = data.get("UNSORTED_CSV") or data.get("UNSORTED_XLSX", str(_REPO / "data" / "unsorted.csv"))
            p = Path(raw).resolve()
            # Force .csv extension regardless of config value
            if p.suffix in (".xlsx", ".xls"):
                p = p.with_suffix(".csv")
            return p
        except Exception:
            pass
    return (_REPO / "data" / "unsorted.csv").resolve()

UNSORTED_CSV = _get_unsorted_csv()

# Backward-compat alias — modules that still import UNSORTED_XLSX get the CSV path
UNSORTED_XLSX = UNSORTED_CSV

AUDIO_EXTS = {
    ".mp3", ".wav", ".aiff", ".aif", ".flac", ".m4a", ".aac", ".ogg", ".alac", ".wv"
}

def ensure_base_dirs() -> None:
    """Utwórz katalogi bazowe według obecnego konfiga."""
    cfg = load_config()
    lib = Path(cfg["LIB_ROOT"])
    inbox = Path(cfg["INBOX_UNSORTED"])
    library_dir = Path(cfg.get("LIBRARY_DIR", str(lib / "LIBRARY")))
    reject = Path(cfg.get("REJECT_ROOT", str(lib / "REJECT")))
    archive = Path(cfg.get("ARCHIVE_ROOT", str(lib / "ARCHIVE")))
    mixes = Path(cfg.get("MIXES_ROOT", str(lib / "MIXES")))
    logs = Path(cfg.get("LOGS_DIR", "./LOGS")).resolve()
    
    # Ensure top-level roots first
    for p in [lib, inbox, logs]:
        p.mkdir(parents=True, exist_ok=True)

    # New logistics layout – create subfolders under LIB_ROOT
    for p in [library_dir, reject, archive, mixes]:
        p.mkdir(parents=True, exist_ok=True)
    
    # Legacy bucket directories (create if they exist for backward compatibility)
    ready = lib / "READY TO PLAY"
    review = lib / "REVIEW QUEUE"
    if ready.exists() or review.exists():
        for p in [ready, review]:
            p.mkdir(parents=True, exist_ok=True)
    
    # Upewnij się, że pliki znaczników istnieją
    app_cfg = AppConfig(library_root=lib, inbox_dir=inbox)
    _create_marker_files(app_cfg)

def load_config() -> Dict[str, Any]:
    """Wczytaj aktualną konfigurację i zwróć jako słownik z kluczami LIB_ROOT i INBOX_UNSORTED."""
    existing = _first_existing(_CANDIDATES)
    if existing:
        cfg = _from_dict(_read_yaml(existing))
    else:
        cfg = _defaults()
    # Read paths from config file (new model: separate root folders)
    config_dict = _read_yaml(_first_existing(_CANDIDATES) or _CANDIDATES[0])
    return {
        "LIB_ROOT": str(cfg.library_root),
        "INBOX_UNSORTED": str(cfg.inbox_dir),
        "REJECT_ROOT": config_dict.get("REJECT_ROOT", str(cfg.library_root.parent / "Music Rejected")),
        "ARCHIVE_ROOT": config_dict.get("ARCHIVE_ROOT", str(cfg.library_root.parent / "Music Archive")),
        "MIXES_ROOT": config_dict.get("MIXES_ROOT", str(cfg.library_root / "MIXES")),
        "LOGS_DIR": config_dict.get("LOGS_DIR", "./LOGS"),
        "UNSORTED_CSV": config_dict.get("UNSORTED_CSV", config_dict.get("UNSORTED_XLSX", "./data/unsorted.csv")),
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
    _create_marker_files(cfg)  # Aktualizuj pliki znaczników

def set_acoustid_api_key(key: str) -> None:
    dest = _choose_config_path()
    # scal z istniejącymi danymi
    d = _read_yaml(dest) if dest.exists() else {}
    d["acoustid_api_key"] = str(key)
    _write_yaml(dest, d)

# AcoustID Application API key — set in config.local.yml or env DJLIB_ACOUSTID_API_KEY.
DEFAULT_ACOUSTID_APP_KEY = ""

def get_acoustid_api_key() -> str:
    """Return AcoustID Application API key from config or env."""
    env = os.getenv("DJLIB_ACOUSTID_API_KEY") or os.getenv("ACOUSTID_API_KEY")
    if env:
        return env.strip()
    existing = _first_existing(_CANDIDATES)
    if existing:
        d = _read_yaml(existing)
        val = str(d.get("acoustid_api_key", "") or "").strip()
        if val:
            return val
    return DEFAULT_ACOUSTID_APP_KEY

# MusicBrainz settings
def get_musicbrainz_settings() -> dict[str, str]:
    """Zwraca ustawienia MusicBrainz User-Agent."""
    # Najpierw config.local.yml, potem config.yml
    existing = _first_existing(_CANDIDATES)
    if existing:
        d = _read_yaml(existing)
        mb = d.get("musicbrainz", {})
        if mb and all(k in mb for k in ["app_name", "app_version", "contact"]):
            return {
                "app_name": str(mb["app_name"]),
                "app_version": str(mb["app_version"]),
                "contact": str(mb["contact"])
            }
    
    # Domyślne wartości z config.yml
    return {
        "app_name": "DJLibraryManager",
        "app_version": "0.1", 
        "contact": "https://github.com/Sztuka/dj-library-manager"
    }

# Last.fm API
def get_lastfm_api_key() -> str:
    # ENV first (.env file or system env)
    env = os.getenv("DJLIB_LASTFM_API_KEY") or os.getenv("LASTFM_API_KEY")
    if env:
        return env.strip()
    # Then config files
    existing = _first_existing(_CANDIDATES)
    if existing:
        d = _read_yaml(existing)
        val = str(d.get("lastfm_api_key", "") or "").strip()
        if val:
            return val
    # Fallback: repository-level config.yml (supports both top-level and nested under 'musicbrainz')
    repo_cfg = _REPO / "config.yml"
    if repo_cfg.exists():
        try:
            d = _read_yaml(repo_cfg)
            val = str(d.get("lastfm_api_key", "") or "").strip()
            if not val:
                val = str(((d.get("musicbrainz") or {}) or {}).get("lastfm_api_key", "") or "").strip()
            if val:
                return val
        except Exception:
            pass
    return ""

# Discogs (optional token — anonymous works at 25 req/min, with token 60 req/min)
def get_discogs_token() -> str:
    env = os.getenv("DJLIB_DISCOGS_TOKEN") or os.getenv("DISCOGS_TOKEN")
    if env:
        return env.strip()
    existing = _first_existing(_CANDIDATES)
    if existing:
        d = _read_yaml(existing)
        val = str(d.get("discogs_token", "") or "").strip()
        if val:
            return val
    repo_cfg = _REPO / "config.yml"
    if repo_cfg.exists():
        try:
            d = _read_yaml(repo_cfg)
            val = str(d.get("discogs_token", "") or "").strip()
            if val:
                return val
        except Exception:
            pass
    return ""


# SoundCloud (public, client_id-based)
def get_soundcloud_client_id() -> str:
    # ENV first
    env = os.getenv("DJLIB_SOUNDCLOUD_CLIENT_ID") or os.getenv("SOUNDCLOUD_CLIENT_ID")
    if env:
        return env.strip()
    # config.local.yml
    existing = _first_existing(_CANDIDATES)
    if existing:
        d = _read_yaml(existing)
        val = str(d.get("soundcloud_client_id", "") or "").strip()
        if val:
            return val
    # repo config.yml (top-level or nested under 'musicbrainz' by mistake)
    repo_cfg = _REPO / "config.yml"
    if repo_cfg.exists():
        try:
            d = _read_yaml(repo_cfg)
            val = str(d.get("soundcloud_client_id", "") or "").strip()
            if not val:
                val = str(((d.get("musicbrainz") or {}) or {}).get("soundcloud_client_id", "") or "").strip()
            if val:
                return val
        except Exception:
            pass
    return ""

# Discogs support removed

# OpenAI (for AI genre suggestions in Review UI)
def get_openai_api_key() -> str:
    """Return OpenAI API key from env or config files."""
    env = os.getenv("DJLIB_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if env:
        return env.strip()
    existing = _first_existing(_CANDIDATES)
    if existing:
        d = _read_yaml(existing)
        val = str(d.get("openai_api_key", "") or "").strip()
        if val:
            return val
    repo_cfg = _REPO / "config.yml"
    if repo_cfg.exists():
        try:
            d = _read_yaml(repo_cfg)
            val = str(d.get("openai_api_key", "") or "").strip()
            if val:
                return val
        except Exception:
            pass
    return ""


_DEFAULT_AI_CHAT_MODEL = "gpt-4.1-mini"
_DEFAULT_AI_QUICK_MODEL = "gpt-5-nano"


def get_ai_chat_model() -> str:
    """Return AI model for chat / web-search (configurable, default gpt-4.1-mini)."""
    env = os.getenv("DJLIB_AI_CHAT_MODEL")
    if env:
        return env.strip()
    existing = _first_existing(_CANDIDATES)
    if existing:
        d = _read_yaml(existing)
        val = str(d.get("ai_chat_model", "") or "").strip()
        if val:
            return val
    return _DEFAULT_AI_CHAT_MODEL


def get_ai_quick_model() -> str:
    """Return AI model for one-shot tasks like identify / suggest-genre (default gpt-5-nano)."""
    env = os.getenv("DJLIB_AI_QUICK_MODEL")
    if env:
        return env.strip()
    existing = _first_existing(_CANDIDATES)
    if existing:
        d = _read_yaml(existing)
        val = str(d.get("ai_quick_model", "") or "").strip()
        if val:
            return val
    return _DEFAULT_AI_QUICK_MODEL
