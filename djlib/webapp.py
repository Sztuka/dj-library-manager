# djlib/webapp.py
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

import platform
import subprocess

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# wewnętrzne moduły (muszą istnieć)
from .config import load_config, save_config_paths, ensure_base_dirs
from .taxonomy import load_taxonomy, save_taxonomy
from .ops import ensure_taxonomy_folders

app = FastAPI()

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "webui" / "templates"
STATIC = ROOT / "webui" / "static"

templates = Jinja2Templates(directory=str(TEMPLATES))
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


# ----------------------------
# Helpers
# ----------------------------
def flash_redirect(url: str, msg: str) -> RedirectResponse:
    return RedirectResponse(f"{url}?msg={msg}", status_code=303)


def normalize_label(s: str) -> str:
    """UPPERCASE + spacje; usuń podkreślniki, zbędne ukośniki końcowe i nadmiarowe spacje."""
    s = s.replace("_", " ").replace("\\", "/").strip().rstrip("/")
    # zbicie wielu spacji do jednej
    s = " ".join(s.split())
    return s.upper()


def split_ready_buckets(ready_paths: List[str]) -> Tuple[List[str], List[str], bool]:
    club, openf, mixes = [], [], False
    for p in ready_paths:
        p = p.strip()
        if p == "MIXES":
            mixes = True
            continue
        if p.startswith("CLUB/"):
            club.append(p.split("/", 1)[1])
        elif p.startswith("OPEN FORMAT/"):
            openf.append(p.split("/", 1)[1])
    return club, openf, mixes


def build_ready_buckets(club: List[str], openf: List[str], mixes: bool = True) -> List[str]:
    out: List[str] = []
    out.extend([f"CLUB/{normalize_label(x)}" for x in club if str(x).strip()])
    out.extend([f"OPEN FORMAT/{normalize_label(x)}" for x in openf if str(x).strip()])
    if mixes:
        out.append("MIXES")
    # deduplikacja przy zachowaniu kolejności
    seen = set()
    dedup = []
    for s in out:
        if s not in seen:
            dedup.append(s)
            seen.add(s)
    return dedup


# ----------------------------
# Routes
# ----------------------------
@app.get("/")
def index():
    return RedirectResponse("/wizard", status_code=302)


@app.get("/wizard")
async def wizard_get(request: Request, step: int = 1, msg: str | None = None):
    cfg = load_config()  # powinien mieć klucze: LIB_ROOT, INBOX_UNSORTED
    tax = load_taxonomy()  # {"ready_buckets":[...], "review_buckets":[...]}
    club, openf, mixes = split_ready_buckets(tax.get("ready_buckets", []))
    ctx = {
        "request": request,
        "step": step,
        "msg": msg,
        "cfg": cfg,
        "club": club,
        "openf": openf,
        "mixes": mixes,
        "review": tax.get("review_buckets", ["UNDECIDED", "NEEDS EDIT"]),
    }
    return templates.TemplateResponse("wizard.html", ctx)


@app.get("/api/pick")
def api_pick(target: str = "path"):
    """macOS: AppleScript 'choose folder' → POSIX path. Inne OS: wpis ręczny w polu."""
    if platform.system() == "Darwin":
        osa = 'POSIX path of (choose folder with prompt "Wybierz folder")'
        try:
            out = subprocess.check_output(["osascript", "-e", osa]).decode("utf-8").strip()
            return JSONResponse({"ok": True, "path": out})
        except Exception as e:
            return JSONResponse({"ok": False, "err": str(e)})
    return JSONResponse({"ok": False, "err": "picker not available on this OS"})


@app.post("/wizard/step1")
async def wizard_step1(
    lib_root: str = Form(...),
    inbox_unsorted: str = Form(...),
):
    save_config_paths(lib_root=lib_root.strip(), inbox=inbox_unsorted.strip())
    return flash_redirect("/wizard?step=2", "Zapisano lokalizacje (LIB_ROOT / INBOX_UNSORTED).")


@app.post("/wizard/step2")
async def wizard_step2(
    club: List[str] = Form(default=[]),
    openf: List[str] = Form(default=[]),
):
    club_clean = [normalize_label(s) for s in club if s and str(s).strip()]
    openf_clean = [normalize_label(s) for s in openf if s and str(s).strip()]

    ready = build_ready_buckets(club_clean, openf_clean, mixes=True)
    review = ["UNDECIDED", "NEEDS EDIT"]  # twardo, spójnie z CSV/ops

    save_taxonomy({"ready_buckets": ready, "review_buckets": review})
    return flash_redirect("/wizard?step=3", "Zapisano taksonomię.")


@app.post("/wizard/step3")
async def wizard_step3(run_scan: str | None = Form(None)):
    ensure_base_dirs()
    ensure_taxonomy_folders()
    if run_scan:
        from .cli import scan_command
        scan_command()
        return flash_redirect("/wizard?step=3", "Utworzono foldery i zeskanowano INBOX_UNSORTED.")
    return flash_redirect("/wizard?step=3", "Utworzono foldery.")