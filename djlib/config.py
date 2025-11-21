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
    lib = _expand("~/Music Library")
    inbox = _expand("~/Unsorted")
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
    lib = _expand(d.get("library_root", "~/Music Library"))
    inbox = _expand(d.get("inbox_dir", "~/Unsorted"))
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
            
            # Po wykryciu konfiguracji automatycznie wykryj taksonomię z istniejącej struktury
            try:
                from djlib.taxonomy import detect_taxonomy_from_fs, save_taxonomy
                detected_tax = detect_taxonomy_from_fs(detected.library_root)
                if detected_tax["ready_buckets"] or detected_tax["review_buckets"]:
                    save_taxonomy(detected_tax)
                    print(f"✓ Wykryto taksonomię: {len(detected_tax['ready_buckets'])} ready buckets, {len(detected_tax['review_buckets'])} review buckets")
            except Exception as e:
                print(f"⚠ Nie udało się wykryć taksonomii: {e}")
            
            return detected, dest
    
    # Brak wykrytej konfiguracji – pytaj użytkownika
    cfg = _interactive_setup()
    dest = _choose_config_path()
    _write_yaml(dest, _to_dict(cfg))
    _create_marker_files(cfg)  # Utwórz pliki znaczników
    
    # Po konfiguracji automatycznie wykryj taksonomię z istniejącej struktury
    try:
        from djlib.taxonomy import detect_taxonomy_from_fs, save_taxonomy
        detected_tax = detect_taxonomy_from_fs(cfg.library_root)
        if detected_tax["ready_buckets"] or detected_tax["review_buckets"]:
            save_taxonomy(detected_tax)
            print(f"✓ Wykryto taksonomię: {len(detected_tax['ready_buckets'])} ready buckets, {len(detected_tax['review_buckets'])} review buckets")
    except Exception as e:
        print(f"⚠ Nie udało się wykryć taksonomii: {e}")
    
    return cfg, dest

def reconfigure() -> Tuple[AppConfig, Path]:
    """Wymuś ponowną konfigurację (używane przez scripts/configure.py)."""
    cfg = _interactive_setup()
    dest = _choose_config_path()
    _write_yaml(dest, _to_dict(cfg))
    _create_marker_files(cfg)  # Utwórz pliki znaczników
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
    cfg = load_config()
    lib = Path(cfg["LIB_ROOT"])
    inbox = Path(cfg["INBOX_UNSORTED"])
    ready = lib / "READY TO PLAY"
    review = lib / "REVIEW QUEUE"
    logs = lib / "LOGS"
    for p in [lib, inbox, ready, review, logs]:
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
    _create_marker_files(cfg)  # Aktualizuj pliki znaczników

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
