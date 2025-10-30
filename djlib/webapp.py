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
    - w przeciwnym razie obok configu (czyli w CWD dla testów).
    """
    env = os.getenv("DJLIB_TAXONOMY_FILE")
    return Path(env).expanduser() if env else get_config_path().parent / TAXONOMY_FILENAME

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

    # Krok 2: zaczynamy z pustą listą subfolderów (użytkownik wpisze własne style)
    # Jeśli w przyszłości chcemy ładować zapisane, można dodać tryb "edit".
    tax = load_taxonomy()
    club: List[str] = []
    openf: List[str] = []
    prefs = load_preferences()
    sugg = load_suggestions()

    return templates.TemplateResponse(
        "wizard.html",
        {
            "request": request,
            "step": step,
            "cfg": cfg,
            "club": club,
            "openf": openf,
            "sugg_club": sugg.get("club", []),
            "sugg_open": sugg.get("open_format", []),
            "prefs": prefs,
            "msg": None,
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
    return RedirectResponse(url="/wizard?step=2", status_code=303)


@app.post("/wizard/step2")
def post_wizard_step2(
    club: List[str] = Form(default=[]), openf: List[str] = Form(default=[]), apply_style: str | None = Form(default=None)
):
    # Zbuduj i zapisz taksonomię READY
    prefs = load_preferences()
    style = prefs.get("label_style", "as_is")

    def maybe_style(x: str) -> str:
        return style_label(x, style) if apply_style else normalize_label(x)

    club_s = [maybe_style(x) for x in club or []]
    openf_s = [maybe_style(x) for x in openf or []]

    ready = build_ready_buckets(club_s, openf_s, mixes=True)
    tax_prev = load_taxonomy()
    save_taxonomy({
        "ready_buckets": ready,
        "review_buckets": tax_prev.get("review_buckets", []),
    })
    return RedirectResponse(url="/wizard?step=3", status_code=303)


@app.post("/wizard/step3")
def post_wizard_step3(request: Request, run_scan: str | None = Form(default=None)):
    # Utwórz foldery wg taksonomii; opcjonalnie uruchom skan w tle
    ensure_base_dirs()
    ensure_taxonomy_folders()

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
        },
    )

# --- Dashboard i akcje ---

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, msg: str | None = None):
    # status konfiga i proste statystyki CSV
    try:
        # Pobierz aktywny config z core
        from djlib.config import load_config as core_load
        from djlib.config import CSV_PATH
        from djlib.csvdb import load_records

        cfg = core_load()
        rows = []
        if CSV_PATH.exists():
            rows = load_records(CSV_PATH)
        stats = {
            "csv_rows": len(rows),
            "csv_path": str(CSV_PATH),
            "lib_root": cfg.get("LIB_ROOT", ""),
            "inbox": cfg.get("INBOX_UNSORTED", ""),
        }
    except Exception as e:
        cfg = {"LIB_ROOT": "", "INBOX_UNSORTED": ""}
        stats = {"csv_rows": 0, "csv_path": "(brak)", "lib_root": "", "inbox": ""}
        print("[dashboard] stats failed:", e)

    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "msg": msg, "cfg": cfg, "stats": stats},
    )


def _run_bg(target, *args, **kwargs):
    import threading
    threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True).start()


@app.post("/action/scan")
def action_scan():
    try:
        from djlib.cli import scan_command
        _run_bg(scan_command)
        return RedirectResponse("/?msg=Uruchomiono%20skan%20w%20tle", status_code=303)
    except Exception as e:
        print("[action] scan failed:", e)
        return RedirectResponse("/?msg=Nie%20udalo%20sie%20uruchomic%20skanu", status_code=303)


@app.post("/action/auto-decide")
def action_auto_decide():
    try:
        import argparse
        from djlib.cli import cmd_auto_decide
        args = argparse.Namespace(rules=str(BASE_DIR / "rules.yml"), only_empty=False)
        _run_bg(cmd_auto_decide, args)
        return RedirectResponse("/?msg=Uruchomiono%20auto-decide%20w%20tle", status_code=303)
    except Exception as e:
        print("[action] auto-decide failed:", e)
        return RedirectResponse("/?msg=Nie%20udalo%20sie%20auto-decide", status_code=303)


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
