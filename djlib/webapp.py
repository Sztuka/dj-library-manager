from pathlib import Path
import os
import yaml
from typing import Dict, Any, List, Tuple

CONFIG_FILENAME = "config.yml"
TAXONOMY_FILENAME = "taxonomy.yml"
SUGGESTIONS_FILENAME = "taxonomy_suggestions.yml"

# --- Ścieżki do plików (bez IO przy imporcie) ---

def get_config_path() -> Path:
    """
    Skąd czytamy/zapisujemy config:
    - jeśli ustawione DJLIB_CONFIG_FILE -> tam,
    - w przeciwnym razie: ./config.yml w AKTUALNYM CWD (ważne dla testów).
    """
    env = os.getenv("DJLIB_CONFIG_FILE")
    return Path(env).expanduser() if env else Path.cwd() / CONFIG_FILENAME

def get_taxonomy_path() -> Path:
    """
    Skąd czytamy/zapisujemy taxonomy:
    - jeśli DJLIB_TAXONOMY_FILE -> tam,
    - w przeciwnym razie domyślnie w katalogu repo (obok tego projektu),
      aby być spójnym z djlib.taxonomy.TAXONOMY_PATH.
    """
    env = os.getenv("DJLIB_TAXONOMY_FILE")
    if env:
        return Path(env).expanduser()
    # domyślnie: obok repo (BASE_DIR wskazuje katalog główny projektu)
    try:
        return BASE_DIR / TAXONOMY_FILENAME
    except NameError:
        # jeśli BASE_DIR nie zainicjalizowany jeszcze na etapie importu, użyj CWD jako fallback
        return Path.cwd() / TAXONOMY_FILENAME

def get_suggestions_path() -> Path:
    """
    Lokalizacja pliku z podpowiedziami taksonomii.
    Jeśli nie ustawiono DJLIB_TAXONOMY_SUGGESTIONS, używamy pliku obok configu.
    """
    env = os.getenv("DJLIB_TAXONOMY_SUGGESTIONS")
    return Path(env).expanduser() if env else get_config_path().parent / SUGGESTIONS_FILENAME

# --- Normalizacja etykiet/bucketów ---

def normalize_label(label: str) -> str:
    """
    Delikatna normalizacja etykiet:
    - zachowuje wielkość liter i znaki (np. podkreślenia),
    - ucina białe znaki wokół segmentów i usuwa puste segmenty,
    - łączy pojedynczym '/',
    - wewnątrz segmentu redukuje nadmiarowe spacje ("  funk   soul" -> "funk soul").
    Przykłady: "tech_house" -> "tech_house", "Open  Format" -> "Open Format", "MIXES/" -> "MIXES".
    """
    if label is None:
        return ""
    s = str(label).replace("\\", "/")
    parts = [p.strip() for p in s.split("/") if p.strip()]
    parts = [" ".join(p.split()) for p in parts]
    return "/".join(parts)

def _canonical_key(label: str) -> str:
    """Klucz kanoniczny do deduplikacji (case-insensitive, spacje sklejone).
    Nie jest zapisywany – używany tylko do porównań.
    """
    s = normalize_label(label)
    # Na potrzeby klucza porównujemy bez rozróżniania wielkości liter
    return s.upper()

def _style_segment(seg: str, style: str) -> str:
    if style == "uppercase":
        return seg.upper()
    if style == "title":
        low = seg.lower()
        # specjalny przypadek dekad, zachowaj małe 's'
        if low in {"70s", "80s", "90s", "2000s", "2010s"}:
            return low
        return " ".join((w[:1].upper() + w[1:]) if w else w for w in seg.split(" "))
    return seg

def style_label(label: str, style: str) -> str:
    parts = [p for p in normalize_label(label).split("/") if p]
    if not parts:
        return ""
    return "/".join(_style_segment(p, style) for p in parts)

# --- Config ---

def save_config_paths(*, lib_root: str, inbox: str) -> None:
    cfg_path = get_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    # zachowaj istniejące preferencje jeśli są
    existing: Dict[str, Any] = {}
    if cfg_path.exists():
        try:
            with cfg_path.open("r", encoding="utf-8") as f:
                existing = yaml.safe_load(f) or {}
        except Exception:
            existing = {}
    data = {
        "LIB_ROOT": str(Path(lib_root).resolve()),
        "INBOX_UNSORTED": str(Path(inbox).resolve()),
        "preferences": existing.get("preferences", {
            "label_style": "as_is",
            "auto_format_new_labels": False,
        }),
    }
    tmp = cfg_path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)
    tmp.replace(cfg_path)

def load_config() -> Dict[str, Any]:
    cfg_path = get_config_path()
    if not cfg_path.exists():
        return {}
    with cfg_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def load_preferences() -> Dict[str, Any]:
    cfg = load_config() or {}
    p = cfg.get("preferences") or {}
    return {
        "label_style": p.get("label_style", "as_is"),
        "auto_format_new_labels": bool(p.get("auto_format_new_labels", False)),
    }

def save_preferences(label_style: str | None = None, auto_format_new_labels: bool | None = None) -> None:
    cfg_path = get_config_path()
    data = load_config() if cfg_path.exists() else {}
    prefs = data.get("preferences", {})
    if label_style is not None:
        prefs["label_style"] = label_style
    if auto_format_new_labels is not None:
        prefs["auto_format_new_labels"] = bool(auto_format_new_labels)
    data["preferences"] = prefs
    tmp = cfg_path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)
    tmp.replace(cfg_path)

# --- Taxonomy ---

def _normalize(items: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for raw in items or []:
        s = normalize_label(raw)
        key = _canonical_key(s)
        if s and key not in seen:
            seen.add(key)
            out.append(s)
    return out

def save_taxonomy(taxonomy: Dict[str, List[str]]) -> None:
    """
    Zapisuje DOKŁADNIE przekazaną taksonomię do pliku obok configu.
    (Nie scala, nie “ucina” – UI decyduje co zapisuje. Testy też.)
    """
    tax_path = get_taxonomy_path()
    tax_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "ready_buckets": _normalize(taxonomy.get("ready_buckets", [])),
        "review_buckets": _normalize(taxonomy.get("review_buckets", [])),
    }
    tmp = tax_path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)
    tmp.replace(tax_path)

def load_taxonomy() -> Dict[str, List[str]]:
    tax_path = get_taxonomy_path()
    if not tax_path.exists():
        return {"ready_buckets": [], "review_buckets": []}
    with tax_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {
        "ready_buckets": data.get("ready_buckets", []) or [],
        "review_buckets": data.get("review_buckets", []) or [],
    }

def _detect_taxonomy_from_fs() -> Dict[str, List[str]]:
    """
    Spróbuj wykryć istniejącą taksonomię na podstawie struktury folderów w LIB_ROOT.
    Zwraca dict z kluczami ready_buckets (np. "CLUB/HOUSE", "OPEN FORMAT/90s", "MIXES")
    oraz review_buckets (np. "UNDECIDED", "NEEDS EDIT").

    Zasady wykrywania:
    - READY TO PLAY/CLUB/<...>  -> dodaj jako "CLUB/<...>" (zachowujemy ścieżkę względną, wielopoziomową)
    - READY TO PLAY/OPEN FORMAT/<...> -> "OPEN FORMAT/<...>"
    - READY TO PLAY/<INNE_TOP_LEVEL> (np. MIXES) -> dodaj jako top-level (np. "MIXES")
    - REVIEW QUEUE/<...> -> dodaj jako review bucket w postaci nazwy względnej
    Jeśli którejś gałęzi nie ma – zwracamy pustą listę dla niej.
    """
    cfg = load_config() or {}
    lib_root = Path(cfg.get("LIB_ROOT", "")) if cfg.get("LIB_ROOT") else None
    if not lib_root or not lib_root.exists():
        return {"ready_buckets": [], "review_buckets": []}

    ready_root = lib_root / "READY TO PLAY"
    review_root = lib_root / "REVIEW QUEUE"

    ready: List[str] = []
    review: List[str] = []

    try:
        if ready_root.exists():
            # sekcje znane: CLUB, OPEN FORMAT – zbierz podkatalogi (rekurencyjnie), zachowując rel ścieżkę
            club_root = ready_root / "CLUB"
            if club_root.exists():
                for p in club_root.rglob("*"):
                    if p.is_dir():
                        rel = p.relative_to(club_root)
                        if str(rel) != ".":
                            bucket = normalize_label(f"CLUB/{rel.as_posix()}")
                            if bucket and _canonical_key(bucket) not in {_canonical_key(x) for x in ready}:
                                ready.append(bucket)
            openf_root = ready_root / "OPEN FORMAT"
            if openf_root.exists():
                for p in openf_root.rglob("*"):
                    if p.is_dir():
                        rel = p.relative_to(openf_root)
                        if str(rel) != ".":
                            bucket = normalize_label(f"OPEN FORMAT/{rel.as_posix()}")
                            if bucket and _canonical_key(bucket) not in {_canonical_key(x) for x in ready}:
                                ready.append(bucket)
            # inne top-level (np. MIXES) – bez podkatalogów jako pojedyncze buckety
            for p in ready_root.iterdir() if ready_root.exists() else []:
                if p.is_dir() and p.name not in {"CLUB", "OPEN FORMAT"}:
                    top = normalize_label(p.name)
                    if top and _canonical_key(top) not in {_canonical_key(x) for x in ready}:
                        ready.append(top)
        if review_root.exists():
            for p in review_root.rglob("*"):
                if p.is_dir():
                    rel = p.relative_to(review_root)
                    name = normalize_label(rel.as_posix())
                    if name and _canonical_key(name) not in {_canonical_key(x) for x in review}:
                        review.append(name)
    except Exception as e:
        print("[wizard] taxonomy FS detection failed:", e)

    return {"ready_buckets": ready, "review_buckets": review}

def load_suggestions() -> Dict[str, List[str]]:
    p = get_suggestions_path()
    if p.exists():
        try:
            with p.open("r", encoding="utf-8") as f:
                d = yaml.safe_load(f) or {}
            return {
                "club": d.get("club", []) or [],
                "open_format": d.get("open_format", []) or [],
                "top_level": d.get("top_level", []) or [],
            }
        except Exception as e:
            print("[taxonomy] suggestions load failed:", e)
    # fallback, przynajmniej MIXES jako top-level
    return {"club": [], "open_format": [], "top_level": ["MIXES"]}

# --- Budowanie listy bucketów READY (na potrzeby testów/IO) ---

def build_ready_buckets(taxonomy: Dict[str, List[str]] | List[str] | None = None, *more: List[str], mixes: bool = False) -> List[str]:
    """
    Zwraca listę znormalizowanych ścieżek bucketów READY w postaci stringów:
    - jeśli podano `taxonomy` jako dict -> bierze `ready_buckets` z dicty
    - jeśli podano listy pozycyjnie (np. club, openf) -> traktuje je jako kategorie
      i dodaje prefiksy: pierwsza lista -> "CLUB/<label>", druga -> "OPEN FORMAT/<label>"
    - usuwa duplikaty po normalizacji, zachowuje kolejność

    Funkcja ma elastyczną sygnaturę aby dopasować testy i UI.
    """
    # Jeśli pierwszy argument jest dict - zachowanie backward-compatible ale zwracamy stringi
    buckets_src: List[str] = []
    if isinstance(taxonomy, dict):
        buckets_src = taxonomy.get("ready_buckets", []) or []
    elif taxonomy is None:
        buckets_src = []
    elif isinstance(taxonomy, list):
        # Jeśli przekazano listę jako jedyny argument traktujemy ją jako gotowe buckety
        buckets_src = taxonomy
    else:
        # Nieznany typ -> pusty
        buckets_src = []

    result: List[str] = []
    seen: set[str] = set()

    # If additional positional lists are provided, interpret them as category buckets
    if more:
        # Expect pattern: build_ready_buckets(club_list, openf_list, mixes=True)
        club = taxonomy if isinstance(taxonomy, list) else (more[0] if more else [])
        openf = more[0] if isinstance(taxonomy, list) and more else (more[1] if len(more) > 1 else [])

        def _add_prefixed(items, prefix):
            for raw in items or []:
                norm = normalize_label(raw)
                if not norm:
                    continue
                # jeśli item zawiera '/', pozostawiamy znormalizowaną formę
                out = norm if "/" in norm else f"{prefix}/{norm}"
                key = _canonical_key(out)
                if key not in seen:
                    seen.add(key)
                    result.append(out)

        _add_prefixed(club, "CLUB")
        _add_prefixed(openf, "OPEN FORMAT")

        if mixes:
            if "MIXES" not in seen:
                result.append("MIXES")

        return result

    # Normal path: taxonomy list/dict converted to normalized strings (no prefixes)
    for raw in buckets_src:
        norm = normalize_label(raw)
        if not norm:
            continue
        key = _canonical_key(norm)
        if key in seen:
            continue
        seen.add(key)
        result.append(norm)

    return result

# --- Tworzenie folderów ---

def ensure_base_dirs() -> None:
    cfg = load_config()
    lib = Path(cfg["LIB_ROOT"])
    (lib / "READY TO PLAY").mkdir(parents=True, exist_ok=True)
    (lib / "REVIEW QUEUE").mkdir(parents=True, exist_ok=True)

def ensure_taxonomy_folders() -> None:
    """
    Tworzy strukturę folderów na podstawie taxonomy.yml:
    - ready_buckets  -> LIB_ROOT/READY TO PLAY/<...>
    - review_buckets -> LIB_ROOT/REVIEW QUEUE/<...>
    Wspiera ścieżki wielopoziomowe rozdzielone '/'.
    """
    cfg = load_config()
    lib = Path(cfg["LIB_ROOT"])
    tax = load_taxonomy()

    ready_root = lib / "READY TO PLAY"
    review_root = lib / "REVIEW QUEUE"

    def _mk(root: Path, bucket: str) -> None:
        parts = [p.strip() for p in normalize_label(bucket).split("/") if p.strip()]
        path = root.joinpath(*parts) if parts else root
        path.mkdir(parents=True, exist_ok=True)

    for b in tax.get("ready_buckets", []):
        _mk(ready_root, b)

    for b in tax.get("review_buckets", []):
        _mk(review_root, b)

# --- Web app (FastAPI) ---

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import threading
import platform
import subprocess
import shlex
import json
import shutil

# ASGI app
app = FastAPI(title="DJ Library Manager")

# Static and templates
BASE_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = BASE_DIR / "webui" / "templates"
STATIC_DIR = BASE_DIR / "webui" / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _group_ready_buckets(buckets: List[str]) -> Dict[str, List[str]]:
    grouped: Dict[str, List[str]] = {}
    for b in buckets:
        norm = normalize_label(b)
        if not norm:
            continue
        parts = norm.split("/")
        if len(parts) == 1:
            # top-level (np. MIXES) – pomijamy w grupowaniu sekcji
            continue
        sect, name = parts[0], "/".join(parts[1:])
        grouped.setdefault(sect, []).append(name)
    # zachowaj kolejność bez duplikatów; już zapewnione przez _normalize/build_ready_buckets
    return grouped


@app.get("/wizard", response_class=HTMLResponse)
def get_wizard(request: Request, step: int = 1):
    step = max(1, min(3, int(step or 1)))
    cfg = load_config()
    msg: str | None = None

    # Krok 2: wybór źródła listy bucketów: preferuj LIB ROOT jeśli niepusty, ale pozwól przełączyć na plik taxonomy.yml
    tax = load_taxonomy()
    ready_saved = tax.get("ready_buckets", []) or []
    review_saved = tax.get("review_buckets", []) or []

    detected = _detect_taxonomy_from_fs()
    fs_ready = detected.get("ready_buckets", []) or []
    fs_review = detected.get("review_buckets", []) or []
    fs_nonempty = len(fs_ready) > 0

    src = (request.query_params.get("src") or ("fs" if fs_nonempty else "file")).lower()
    if src not in {"fs", "file"}:
        src = "fs" if fs_nonempty else "file"

    if src == "fs" and fs_nonempty:
        buckets_ready = list(fs_ready)
        buckets_review = list(review_saved) or list(fs_review)
        msg = "Wyświetlamy strukturę wykrytą w LIB ROOT (możesz przełączyć na taxonomy.yml)."
    else:
        # użyj zapisanej taksonomii z pliku
        buckets_ready = list(ready_saved)
        buckets_review = list(review_saved)
        if fs_nonempty:
            msg = "Wykryto strukturę w LIB ROOT, ale używamy taxonomy.yml (możesz przełączyć na LIB ROOT)."
        else:
            msg = None

    # Wyprowadź do pól krok 2 (lista bez prefiksów sekcji)
    grouped = _group_ready_buckets(buckets_ready)
    club: List[str] = grouped.get("CLUB", [])
    openf: List[str] = grouped.get("OPEN FORMAT", [])
    # top-level (bez prefiksu), np. MIXES
    top_level: List[str] = [b for b in buckets_ready if "/" not in normalize_label(b)]
    prefs = load_preferences()
    sugg = load_suggestions()
    # fallback dla usuniętych kubełków – można zmienić na kroku 2; odczytaj z query jeśli wracamy
    fb = str(request.query_params.get("fallback", "")) or "UNDECIDED"

    return templates.TemplateResponse(
        "wizard.html",
        {
            "request": request,
            "step": step,
            "cfg": cfg,
            "club": club,
            "openf": openf,
            "top_level": top_level,
            "sugg_club": sugg.get("club", []),
            "sugg_open": sugg.get("open_format", []),
            "prefs": prefs,
            "msg": msg,
            "review": review_saved,
            "removal_fallback": fb,
            "src": src,
            "fs_count": len(fs_ready),
            "file_count": len(ready_saved),
        },
    )


@app.post("/wizard/step1")
def post_wizard_step1(
    lib_root: str = Form(""), inbox_unsorted: str = Form("")
):
    # Zapisz w lokalnym YAML (na potrzeby testów/UI)
    save_config_paths(lib_root=lib_root, inbox=inbox_unsorted)
    # Upewnij się, że główny moduł konfiguracyjny (djlib.config) też jest zaktualizowany,
    # tak aby CLI korzystało z tych samych ścieżek.
    try:
        from djlib import config as core_cfg
        core_cfg.save_config_paths(lib_root=lib_root, inbox=inbox_unsorted)
    except Exception as e:
        print("[wizard] core config save failed:", e)
    # Po wskazaniu LIB_ROOT spróbuj automatycznie zaciągnąć istniejącą strukturę folderów
    # do taxonomy.yml, jeśli ready_buckets są puste.
    try:
        current = load_taxonomy()
        ready_now = current.get("ready_buckets", []) or []
        review_now = current.get("review_buckets", []) or []
        if not ready_now:
            detected = _detect_taxonomy_from_fs()
            det_ready = detected.get("ready_buckets", [])
            if det_ready:
                save_taxonomy({
                    "ready_buckets": det_ready,
                    "review_buckets": review_now or detected.get("review_buckets", []),
                })
    except Exception as e:
        print("[wizard] taxonomy auto-detect after step1 failed:", e)
    return RedirectResponse(url="/wizard?step=2", status_code=303)


@app.post("/wizard/step2")
def post_wizard_step2(
    club: List[str] = Form(default=[]),
    openf: List[str] = Form(default=[]),
    top_level: List[str] = Form(default=[]),
    removal_fallback: str = Form(default="UNDECIDED"),
    apply_style: str | None = Form(default=None),
):
    # Zbuduj i zapisz taksonomię READY
    prefs = load_preferences()
    style = prefs.get("label_style", "as_is")

    def maybe_style(x: str) -> str:
        return style_label(x, style) if apply_style else normalize_label(x)

    club_s = [maybe_style(x) for x in club or []]
    openf_s = [maybe_style(x) for x in openf or []]
    top_s = [maybe_style(x) for x in top_level or []]

    ready = build_ready_buckets(club_s, openf_s, mixes=True)
    # dołóż top-level (bez prefiksów)
    seen = {_canonical_key(x) for x in ready}
    for t in top_s:
        nt = normalize_label(t)
        if nt and "/" not in nt:
            k = _canonical_key(nt)
            if k not in seen:
                seen.add(k)
                ready.append(nt)
    tax_prev = load_taxonomy()
    save_taxonomy({
        "ready_buckets": ready,
        "review_buckets": tax_prev.get("review_buckets", []),
    })
    # przekaż wybrany fallback do kroku 3 (operacje porządkowe mogą go użyć)
    from urllib.parse import quote
    return RedirectResponse(url=f"/wizard?step=3&fallback={quote(removal_fallback)}", status_code=303)


@app.post("/wizard/step3")
def post_wizard_step3(request: Request, run_scan: str | None = Form(default=None), fallback: str | None = Form(default=None)):
    # Utwórz foldery wg taksonomii; opcjonalnie uruchom skan w tle
    ensure_base_dirs()
    ensure_taxonomy_folders()

    # Jeśli użytkownik usunął kubełki w kroku 2 – przenieś ich zawartość do REVIEW QUEUE/<fallback>
    moved_files = 0
    moved_dirs = 0
    try:
        if fallback:
            moved_dirs, moved_files = _relocate_removed_buckets_to_review(normalize_label(fallback))
    except Exception as e:
        print("[wizard] relocate removed buckets failed:", e)

    scan_started = False
    if run_scan:
        try:
            from djlib.cli import scan_command
            threading.Thread(target=scan_command, daemon=True).start()
            scan_started = True
        except Exception as e:
            print("[wizard] scan start failed:", e)

    # Pokaż stronę potwierdzenia z linkami do aplikacji i CSV
    return templates.TemplateResponse(
        "done.html",
        {
            "request": request,
            "scan_started": scan_started,
            "moved_dirs": moved_dirs,
            "moved_files": moved_files,
        },
    )

def _relocate_removed_buckets_to_review(fallback_review: str) -> tuple[int, int]:
    """
    Wykryj kubełki, które istnieją na dysku pod READY TO PLAY, ale nie ma ich już w taxonomy.yml.
    Przenieś ich zawartość do REVIEW QUEUE/<fallback_review>/<rel> (zachowując relatywną ścieżkę),
    rozwiązując kolizje nazw przy pomocy move_with_rename. Zaktualizuj CSV tam, gdzie to możliwe.

    Zwraca: (liczba_przeniesionych_katalogów, liczba_przeniesionych_plików)
    """
    # ścieżki
    try:
        from djlib.config import load_config as core_load, CSV_PATH, READY_TO_PLAY_DIR, REVIEW_QUEUE_DIR
        from djlib.csvdb import load_records, save_records
        from djlib.mover import move_with_rename
    except Exception as e:
        print("[wizard] relocate import failed:", e)
        return (0, 0)

    cfg = core_load()
    ready_root = READY_TO_PLAY_DIR
    review_root = REVIEW_QUEUE_DIR

    # docelowy root
    dest_base = review_root / fallback_review
    dest_base.mkdir(parents=True, exist_ok=True)

    # nowa taksonomia – których kubełków NIE przenosimy
    tax = load_taxonomy()
    new_ready = {_canonical_key(normalize_label(x)) for x in (tax.get("ready_buckets") or [])}

    # aktualny stan FS – tylko poziom 1 dla CLUB/OPEN FORMAT, oraz inne top-level
    existing: list[str] = []
    club_root = ready_root / "CLUB"
    if club_root.exists():
        for d in sorted([p for p in club_root.iterdir() if p.is_dir()]):
            existing.append(f"CLUB/{d.name}")
    open_root = ready_root / "OPEN FORMAT"
    if open_root.exists():
        for d in sorted([p for p in open_root.iterdir() if p.is_dir()]):
            existing.append(f"OPEN FORMAT/{d.name}")
    if ready_root.exists():
        for d in sorted([p for p in ready_root.iterdir() if p.is_dir()]):
            if d.name not in {"CLUB", "OPEN FORMAT"}:
                existing.append(d.name)

    removed = [b for b in existing if _canonical_key(b) not in new_ready]
    if not removed:
        return (0, 0)

    # podobnie jak w rename: zbierz mapę przesunięć do aktualizacji CSV
    moves: list[tuple[str, str]] = []
    moved_dirs = 0
    moved_files = 0

    for rel in removed:
        src_dir = ready_root / rel
        if not src_dir.exists():
            continue
        # docelowo zachowujemy rel ścieżkę pod fallbackiem, by uniknąć kolizji i zachować kontekst
        dst_dir = dest_base / rel
        dst_dir.mkdir(parents=True, exist_ok=True)

        # Jeśli dst_dir jest puste – spróbuj prostego przeniesienia całego katalogu
        try:
            if not any(dst_dir.iterdir()):
                # przenieś cały folder
                shutil.move(str(src_dir), str(dst_dir))
                moved_dirs += 1
                # zmapuj przeniesione pliki (po przeniesieniu src_dir już zmienił miejsce)
                for sub in dst_dir.rglob("*"):
                    if sub.is_file():
                        # spróbuj wyznaczyć starą ścieżkę względem dawnego src_dir (teraz nieistniejącego)
                        # nie mamy łatwego rel, więc CSV update zrobimy drugą pętlą na bazie relacji katalogów niżej
                        moved_files += 1
                # nic więcej dla tego bucketu
                continue
        except Exception:
            # w razie problemów, przejdź do trybu per-plik
            pass

        # Merge: przenieś per-plik z obsługą kolizji
        for sub in src_dir.rglob("*"):
            if sub.is_file():
                rel_path = sub.relative_to(src_dir)
                dest_file = dst_dir / rel_path
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                final = move_with_rename(sub, dest_file.parent, dest_file.name)
                moves.append((str(sub), str(final)))
                moved_files += 1
        # spróbuj posprzątać źródło
        try:
            shutil.rmtree(src_dir, ignore_errors=True)
        except Exception:
            pass

    # Aktualizacja CSV: final_path i target_subfolder dla plików z przeniesionych kubełków
    try:
        rows = load_records(CSV_PATH)
        updated = 0
        # zbuduj szybki indeks katalogów źródłowych -> docelowych
        mapping_dirs: list[tuple[Path, Path]] = []
        for rel in removed:
            src_dir = ready_root / rel
            dst_dir = dest_base / rel
            mapping_dirs.append((src_dir, dst_dir))

        for r in rows:
            fpath = r.get("final_path") or ""
            if not fpath:
                continue
            try:
                p = Path(fpath)
                for src_dir, dst_dir in mapping_dirs:
                    try:
                        relp = p.resolve().relative_to(src_dir.resolve())
                    except Exception:
                        relp = None
                    if relp is not None:
                        cand = dst_dir / relp
                        if cand.exists():
                            r["final_path"] = str(cand)
                            updated += 1
                            break
            except Exception:
                pass

            # zaktualizuj target_subfolder, jeśli był skierowany do READY TO PLAY/<...>
            ts = r.get("target_subfolder") or ""
            if ts.startswith("READY TO PLAY/"):
                r["target_subfolder"] = f"REVIEW QUEUE/{fallback_review}"
                updated += 1

        if updated:
            save_records(CSV_PATH, rows)
    except Exception as e:
        print("[wizard] CSV update after relocation failed:", e)

    return (moved_dirs, moved_files)

# --- Dashboard i akcje ---

@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, msg: str | None = None):
    # status konfiga i proste statystyki CSV
    try:
        # Pobierz aktywny config z core
        from djlib.config import load_config as core_load
        from djlib.config import CSV_PATH, LOGS_DIR
        from djlib.csvdb import load_records

        cfg = core_load()
        # First-run detection: jeśli podstawowa konfiguracja/struktura nie istnieje,
        # przekieruj na kreator (z wyjątkiem jawnego wejścia na /dashboard).
        try:
            lib_ok = bool(cfg.get("LIB_ROOT")) and Path(cfg.get("LIB_ROOT", "")).exists()
            inbox_ok = bool(cfg.get("INBOX_UNSORTED")) and Path(cfg.get("INBOX_UNSORTED", "")).exists()
            base_dirs_ok = lib_ok and (Path(cfg.get("LIB_ROOT", "")) / "READY TO PLAY").exists() and (Path(cfg.get("LIB_ROOT", "")) / "REVIEW QUEUE").exists()
            first_run = not (lib_ok and inbox_ok and base_dirs_ok)
            if first_run and request.url.path != "/dashboard":
                return RedirectResponse(url="/wizard", status_code=303)
        except Exception:
            if request.url.path != "/dashboard":
                return RedirectResponse(url="/wizard", status_code=303)
        rows = []
        if CSV_PATH.exists():
            rows = load_records(CSV_PATH)
        stats = {
            "csv_rows": len(rows),
            "csv_path": str(CSV_PATH),
            "lib_root": cfg.get("LIB_ROOT", ""),
            "inbox": cfg.get("INBOX_UNSORTED", ""),
        }
        # wczytaj statusy jeśli istnieją
        scan_status = {}
        enrich_status = {}
        fp_status = {}
        try:
            sp = LOGS_DIR / "scan_status.json"
            if sp.exists():
                with sp.open("r", encoding="utf-8") as f:
                    scan_status = json.load(f)
            ep = LOGS_DIR / "enrich_status.json"
            if ep.exists():
                with ep.open("r", encoding="utf-8") as f:
                    enrich_status = json.load(f)
            fp = LOGS_DIR / "fingerprint_status.json"
            if fp.exists():
                with fp.open("r", encoding="utf-8") as f:
                    fp_status = json.load(f)
        except Exception as e:
            print("[dashboard] scan status read failed:", e)
    except Exception as e:
        cfg = {"LIB_ROOT": "", "INBOX_UNSORTED": ""}
        stats = {"csv_rows": 0, "csv_path": "(brak)", "lib_root": "", "inbox": ""}
        scan_status = {}
        enrich_status = {}
        fp_status = {}
        print("[dashboard] stats failed:", e)

    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "msg": msg, "cfg": cfg, "stats": stats, "scan": scan_status, "enrich": enrich_status, "fp": fp_status},
    )


def _run_bg(target, *args, **kwargs):
    import threading
    threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True).start()


@app.post("/action/scan")
def action_scan():
    try:
        from djlib.cli import scan_command
        from djlib.config import LOGS_DIR
        # Wyczyść stare statusy, by UI nie wyświetlało poprzednich wyników jako bieżących
        try:
            for name in ("enrich_status.json", "fingerprint_status.json"):
                p = LOGS_DIR / name
                if p.exists():
                    p.unlink()
        except Exception:
            pass
        _run_bg(scan_command)
        return RedirectResponse("/?msg=Uruchomiono%20skan%20w%20tle", status_code=303)
    except Exception as e:
        print("[action] scan failed:", e)
        return RedirectResponse("/?msg=Nie%20udalo%20sie%20uruchomic%20skanu", status_code=303)


@app.post("/action/auto-decide")
def action_auto_decide():
    try:
        import argparse
        from djlib.cli import cmd_auto_decide_smart
        args = argparse.Namespace()
        _run_bg(cmd_auto_decide_smart, args)
        return RedirectResponse("/?msg=Uruchomiono%20auto-decide%20(smart)%20w%20tle", status_code=303)
    except Exception as e:
        print("[action] auto-decide failed:", e)
        return RedirectResponse("/?msg=Nie%20udalo%20sie%20auto-decide", status_code=303)


@app.get("/api/pending-tracks")
def api_pending_tracks():
    try:
        from djlib.config import CSV_PATH
        from djlib.csvdb import load_records
        rows = load_records(CSV_PATH)
        pending = [
            {
                "track_id": r.get("track_id",""),
                "file_name": Path(r.get("file_path","" )).name,
                "artist": r.get("artist_suggest",""),
                "title": r.get("title_suggest",""),
                "version": r.get("version_suggest",""),
                "genre": r.get("genre_suggest",""),
                "album": r.get("album_suggest",""),
                "year": r.get("year_suggest",""),
                "duration": r.get("duration_suggest",""),
                "bpm": r.get("bpm",""),
                "key": r.get("key_camelot",""),
                "source": r.get("meta_source",""),
            }
            for r in rows
            if (r.get("review_status") or "").lower() != "accepted"
        ]
        return JSONResponse({"items": pending})
    except Exception as e:
        print("[api] pending-tracks failed:", e)
        return JSONResponse({"items": []})


@app.post("/review/accept")
def review_accept(track_id: str = Form("")):
    try:
        from djlib.config import CSV_PATH
        from djlib.csvdb import load_records, save_records
        import argparse
        from djlib.cli import cmd_apply
        rows = load_records(CSV_PATH)
        changed = False
        for r in rows:
            if r.get("track_id") == track_id:
                # kopiuj suggest_* do głównych pól i oznacz jako zaakceptowane
                r["artist"] = r.get("artist_suggest","")
                r["title"] = r.get("title_suggest","")
                r["version_info"] = r.get("version_suggest","")
                # Możemy też w przyszłości przenieść genre/album/year/duration do stałych pól kiedy je dodamy
                r["review_status"] = "accepted"
                changed = True
                break
        if changed:
            save_records(CSV_PATH, rows)
            # od razu przenieś wg decyzji
            _run_bg(cmd_apply, argparse.Namespace(dry_run=False))
            return RedirectResponse("/?msg=Zaakceptowano%20%E2%80%94%20przenoszenie%20w%20toku", status_code=303)
        return RedirectResponse("/?msg=Nie%20znaleziono%20rekordu", status_code=303)
    except Exception as e:
        print("[review] accept failed:", e)
        return RedirectResponse("/?msg=Blad%20zapisu", status_code=303)


@app.post("/action/enrich-online")
def action_enrich_online():
    try:
        import argparse
        from djlib.cli import cmd_enrich_online
        args = argparse.Namespace()
        _run_bg(cmd_enrich_online, args)
        return RedirectResponse("/?msg=Enrich%20online%20w%20tle", status_code=303)
    except Exception as e:
        print("[action] enrich-online failed:", e)
        return RedirectResponse("/?msg=Nie%20udalo%20sie%20enrich%20online", status_code=303)


@app.post("/action/fix-fingerprints")
def action_fix_fingerprints():
    try:
        import argparse
        from djlib.cli import cmd_fix_fingerprints
        args = argparse.Namespace()
        _run_bg(cmd_fix_fingerprints, args)
        return RedirectResponse("/?msg=Uzupełnianie%20fingerprintow%20w%20tle", status_code=303)
    except Exception as e:
        print("[action] fix-fingerprints failed:", e)
        return RedirectResponse("/?msg=Nie%20udalo%20sie%20uzupelnic%20fingerprintow", status_code=303)


@app.post("/review/accept-batch")
async def review_accept_batch(request: Request):
    try:
        from djlib.config import CSV_PATH
        from djlib.csvdb import load_records, save_records
        import argparse
        from djlib.cli import cmd_apply
        form = await request.form()
        ids = form.getlist('ids') if hasattr(form, 'getlist') else [v for k,v in form.items() if k=='ids']
        if not ids:
            return RedirectResponse("/?msg=Nic%20nie%20zaznaczono", status_code=303)
        rows = load_records(CSV_PATH)
        updated = 0
        for r in rows:
            tid = r.get("track_id","")
            if tid in ids:
                # odczytaj edytowane pola
                r["artist"] = str(form.get(f"artist_{tid}", r.get("artist_suggest","")))
                r["title"] = str(form.get(f"title_{tid}", r.get("title_suggest","")))
                r["version_info"] = str(form.get(f"version_{tid}", r.get("version_suggest","")))
                # przenieś też źródła jeśli chcesz w przyszłości
                r["review_status"] = "accepted"
                updated += 1
        if updated:
            save_records(CSV_PATH, rows)
            # od razu przenoszenie w tle
            _run_bg(cmd_apply, argparse.Namespace(dry_run=False))
            return RedirectResponse(f"/?msg=Zaakceptowano%20{updated}%20%E2%80%94%20przenoszenie%20w%20toku", status_code=303)
        return RedirectResponse("/?msg=Brak%20dopasowanych%20ID", status_code=303)
    except Exception as e:
        print("[review] accept-batch failed:", e)
        return RedirectResponse("/?msg=Blad%20zapisu", status_code=303)


@app.post("/action/apply-dry")
def action_apply_dry():
    try:
        import argparse
        from djlib.cli import cmd_apply
        args = argparse.Namespace(dry_run=True)
        _run_bg(cmd_apply, args)
        return RedirectResponse("/?msg=Uruchomiono%20apply%20(dry-run)", status_code=303)
    except Exception as e:
        print("[action] apply dry failed:", e)
        return RedirectResponse("/?msg=Nie%20udalo%20sie%20apply%20dry-run", status_code=303)


@app.post("/action/apply")
def action_apply():
    try:
        import argparse
        from djlib.cli import cmd_apply
        args = argparse.Namespace(dry_run=False)
        _run_bg(cmd_apply, args)
        return RedirectResponse("/?msg=Uruchomiono%20apply", status_code=303)
    except Exception as e:
        print("[action] apply failed:", e)
        return RedirectResponse("/?msg=Nie%20udalo%20sie%20apply", status_code=303)


@app.post("/action/undo")
def action_undo():
    try:
        import argparse
        from djlib.cli import cmd_undo
        args = argparse.Namespace()
        _run_bg(cmd_undo, args)
        return RedirectResponse("/?msg=Cofanie%20ostatnich%20ruchow", status_code=303)
    except Exception as e:
        print("[action] undo failed:", e)
        return RedirectResponse("/?msg=Nie%20udalo%20sie%20cofnac", status_code=303)


@app.post("/action/dupes")
def action_dupes():
    try:
        import argparse
        from djlib.cli import cmd_dupes
        args = argparse.Namespace()
        _run_bg(cmd_dupes, args)
        return RedirectResponse("/?msg=Generowanie%20raportu%20duplikatow", status_code=303)
    except Exception as e:
        print("[action] dupes failed:", e)
        return RedirectResponse("/?msg=Nie%20udalo%20sie%20wygenerowac%20raportu", status_code=303)

@app.get("/api/scan-status")
def api_scan_status():
    try:
        from djlib.config import LOGS_DIR
        sp = LOGS_DIR / "scan_status.json"
        if sp.exists():
            with sp.open("r", encoding="utf-8") as f:
                return JSONResponse(json.load(f))
    except Exception as e:
        print("[api] scan-status failed:", e)
    return JSONResponse({"state": "idle"})

@app.get("/api/enrich-status")
def api_enrich_status():
    try:
        from djlib.config import LOGS_DIR
        sp = LOGS_DIR / "enrich_status.json"
        if sp.exists():
            with sp.open("r", encoding="utf-8") as f:
                return JSONResponse(json.load(f))
    except Exception as e:
        print("[api] enrich-status failed:", e)
    return JSONResponse({"state": "idle"})

@app.get("/api/fingerprint-status")
def api_fingerprint_status():
    try:
        from djlib.config import LOGS_DIR
        sp = LOGS_DIR / "fingerprint_status.json"
        if sp.exists():
            with sp.open("r", encoding="utf-8") as f:
                return JSONResponse(json.load(f))
    except Exception as e:
        print("[api] fingerprint-status failed:", e)
    return JSONResponse({"state": "idle"})


# (Usunięto UI i endpoint ustawień klucza AcoustID — klucz aplikacji przechowujemy w kodzie/konfigu)

@app.get("/api/fpcalc-status")
def api_fpcalc_status():
    """Zwróć informacje o fpcalc: wykryta ścieżka i wersja/wyjście narzędzia."""
    try:
        from djlib.fingerprint import ensure_fpcalc_in_env
        p = ensure_fpcalc_in_env()
        # Spróbuj uzyskać wersję
        res = subprocess.run([str(p), "-version"], capture_output=True, text=True, timeout=10)
        if res.returncode != 0 or (not res.stdout and not res.stderr):
            res = subprocess.run([str(p)], capture_output=True, text=True, timeout=10)
        version = ""
        out_all = (res.stdout or "") + ("\n" + res.stderr if res.stderr else "")
        # wyłuskaj pierwszą linię z numerem
        for line in out_all.splitlines():
            if "Chromaprint" in line or "fpcalc" in line or "version" in line.lower():
                version = line.strip()
                break
        return JSONResponse({
            "ok": True,
            "path": str(p),
            "version": version,
            "stdout": res.stdout,
            "stderr": res.stderr,
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})

@app.get("/csv", response_class=HTMLResponse)
def csv_view(request: Request):
    # Prosty podgląd CSV w przeglądarce
    try:
        from djlib.config import CSV_PATH
        rows = []
        if CSV_PATH.exists():
            import csv
            with CSV_PATH.open("r", encoding="utf-8") as f:
                r = csv.reader(f)
                rows = list(r)
        return templates.TemplateResponse(
            "csv.html",
            {"request": request, "rows": rows},
        )
    except Exception as e:
        return HTMLResponse(f"<pre>CSV error: {e}</pre>", status_code=500)


@app.get("/taxonomy", response_class=HTMLResponse)
def taxonomy_page(request: Request):
    tax = load_taxonomy()
    ready = tax.get("ready_buckets", [])
    grouped = _group_ready_buckets(ready)
    prefs = load_preferences()
    sugg = load_suggestions()

    club_current = grouped.get("CLUB", [])
    open_current = grouped.get("OPEN FORMAT", [])
    review = tax.get("review_buckets", [])

    return templates.TemplateResponse(
        "taxonomy.html",
        {
            "request": request,
            "grouped": grouped,
            "club_current": club_current,
            "open_current": open_current,
            "sugg_club": sugg.get("club", []),
            "sugg_open": sugg.get("open_format", []),
            "sugg_top": sugg.get("top_level", []),
            "prefs": prefs,
            "review": review,
        },
    )


@app.post("/taxonomy/save")
def taxonomy_save(
    club: List[str] = Form(default=[]),
    openf: List[str] = Form(default=[]),
    top_level: List[str] = Form(default=[]),
    apply_style: str | None = Form(default=None),
):
    prefs = load_preferences()
    style = prefs.get("label_style", "as_is")
    def maybe_style(x: str) -> str:
        return style_label(x, style) if apply_style else normalize_label(x)

    seen: set[str] = set()
    out: List[str] = []

    def add_prefixed(items: List[str], prefix: str | None = None):
        for raw in items or []:
            lab = maybe_style(raw)
            if not lab:
                continue
            full = lab if (prefix is None or "/" in lab) else f"{prefix}/{lab}"
            key = _canonical_key(full)
            if key not in seen:
                seen.add(key)
                out.append(full)

    add_prefixed(club, "CLUB")
    add_prefixed(openf, "OPEN FORMAT")
    add_prefixed(top_level, None)

    prev = load_taxonomy()
    save_taxonomy({
        "ready_buckets": out,
        "review_buckets": prev.get("review_buckets", []),
    })
    return RedirectResponse(url="/taxonomy", status_code=303)


@app.post("/taxonomy/add")
def taxonomy_add(section: str = Form(""), name: str = Form("")):
    bucket = normalize_label(f"{section}/{name}")
    tax = load_taxonomy()
    ready = tax.get("ready_buckets", [])
    if bucket and bucket not in ready:
        ready.append(bucket)
    save_taxonomy({
        "ready_buckets": ready,
        "review_buckets": tax.get("review_buckets", []),
    })
    return RedirectResponse(url="/taxonomy", status_code=303)


@app.get("/api/pick")
def api_pick(target: str | None = None):
    """
    macOS-only folder picker using AppleScript. Returns {ok: bool, path: str}.
    For other OS-es or on error, returns ok: False so UI can fallback.
    """
    if platform.system() != "Darwin":
        return JSONResponse({"ok": False, "path": ""})

    # Build AppleScript prompt and scripts
    prompt_raw = f"Wybierz folder dla: {target or ''}"
    prompt = prompt_raw.replace("\\", "\\\\").replace('"', '\\"')

    primary_script = f'POSIX path of (choose folder with prompt "{prompt}")'
    fallback_script = (
        'tell application "System Events" to POSIX path of '
        f'(choose folder with prompt "{prompt}")'
    )
    try:
        # Try without System Events (fewer permissions needed)
        res = subprocess.run(
            ["osascript", "-e", primary_script],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if res.returncode == 0 and (res.stdout or "").strip():
            return JSONResponse({"ok": True, "path": res.stdout.strip()})

        # Fallback via System Events
        res2 = subprocess.run(
            ["osascript", "-e", fallback_script],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if res2.returncode == 0 and (res2.stdout or "").strip():
            return JSONResponse({"ok": True, "path": res2.stdout.strip()})

        # Log stderr to server console for troubleshooting; return generic failure to UI
        if res.stderr:
            print("[picker] stderr:", res.stderr.strip())
        if res2.stderr:
            print("[picker] fallback stderr:", res2.stderr.strip())
        return JSONResponse({"ok": False, "path": ""})
    except subprocess.TimeoutExpired:
        return JSONResponse({"ok": False, "path": ""})
    except Exception as e:
        print("[picker] exception:", e)
        return JSONResponse({"ok": False, "path": ""})
