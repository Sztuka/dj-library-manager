from __future__ import annotations
from pathlib import Path
from typing import List, Dict
from collections import Counter
import re

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from djlib.taxonomy import (
    _read_taxonomy, _write_taxonomy, ensure_taxonomy_dirs,
)
from djlib.csvdb import load_records
from djlib.config import CSV_PATH

REPO = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO / "webui" / "templates"
STATIC_DIR = REPO / "webui" / "static"

app = FastAPI(title="DJ Library Manager – Taxonomy Wizard")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Gotowe propozycje (jak w CLI-wizardzie)
CANDIDATES_CLUB = [
    "CLUB/DNB", "CLUB/TRANCE", "CLUB/PROGRESSIVE_HOUSE", "CLUB/INDIE_DANCE",
    "CLUB/BREAKS", "CLUB/UK_GARAGE", "CLUB/HARD_TECHNO", "CLUB/ELECTRO",
    "CLUB/EDM_MAINSTAGE", "CLUB/DISCO_NUDISCO", "CLUB/FUNK_SOUL",
]
CANDIDATES_OPEN = [
    "OPEN FORMAT/PARTY DANCE",
    "OPEN FORMAT/RNB",
    "OPEN FORMAT/HIP-HOP",
    "OPEN FORMAT/LATIN REGGAETON",
    "OPEN FORMAT/POLISH SINGALONG",
    "OPEN FORMAT/ROCK CLASSICS",
    "OPEN FORMAT/ROCKNROLL",
    "OPEN FORMAT/FUNK SOUL",
    "OPEN FORMAT/70s",
    "OPEN FORMAT/80s",
    "OPEN FORMAT/90s",
    "OPEN FORMAT/2000s",
    "OPEN FORMAT/2010s",
]

def _group_ready(ready: List[str]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for r in ready:
        sect, name = (r.split("/", 1) + [""])[:2] if "/" in r else (r, "")
        out.setdefault(sect, []).append(name)
    for k in out:
        out[k] = sorted(out[k])
    return dict(sorted(out.items()))

def _normalize_genre(g: str) -> str:
    s = (g or "").strip().lower()
    s = s.replace("-", " ").replace("_", " ")
    s = s.replace("drum and bass", "dnb").replace("drum & bass", "dnb")
    s = s.replace("nu disco", "disco nudisco")
    return " ".join(s.split())

def _suggest_from_csv(threshold: int = 25) -> List[str]:
    if not CSV_PATH.exists():
        return []
    rows = load_records(CSV_PATH)
    genres = [_normalize_genre(r.get("genre", "")) for r in rows if r.get("genre")]
    ctr = Counter([g for g in genres if g])

    suggestions: List[str] = []
    for genre, cnt in ctr.most_common():
        key = None
        if "dnb" in genre: key = "CLUB/DNB"
        elif "trance" in genre: key = "CLUB/TRANCE"
        elif "progressive" in genre: key = "CLUB/PROGRESSIVE HOUSE"
        elif "tech house" in genre: key = "CLUB/TECH HOUSE"
        elif "hard techno" in genre or ("techno" in genre and "hard" in genre): key = "CLUB/HARD_TECHNO"
        elif "nudisco" in genre or "nu disco" in genre or "disco" in genre: key = "CLUB/DISCO_NUDISCO"
        elif "uk garage" in genre or "garage" in genre: key = "CLUB/UK_GARAGE"
        elif "breaks" in genre or "breakbeat" in genre: key = "CLUB/BREAKS"
        elif "indie" in genre: key = "CLUB/INDIE_DANCE"
        elif "electro" in genre: key = "CLUB/ELECTRO"
        if key and cnt >= threshold:
            suggestions.append(key)
    return sorted(set(suggestions))

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return RedirectResponse(url="/taxonomy")

@app.get("/taxonomy", response_class=HTMLResponse)
async def taxonomy_get(request: Request):
    data = _read_taxonomy()
    ready = data["ready_buckets"]
    review = data["review_buckets"]
    grouped = _group_ready(ready)
    # filtruj kandydatów, żeby nie duplikować
    cand_club = [c for c in CANDIDATES_CLUB if c not in ready]
    cand_open = [c for c in CANDIDATES_OPEN if c not in ready]
    suggestions = [s for s in _suggest_from_csv() if s not in ready]
    return templates.TemplateResponse(
        "taxonomy.html",
        {
            "request": request,
            "grouped": grouped,
            "review": sorted(review),
            "cand_club": cand_club,
            "cand_open": cand_open,
            "suggestions": suggestions,
        },
    )

@app.post("/taxonomy/add", response_class=HTMLResponse)
async def taxonomy_add(request: Request,
                       section: str = Form(...),
                       name: str = Form(...)):
    section = section.strip().upper().strip("/")
    name = name.strip().upper().strip("/")
    if not section or not name or "/" in section or "/" in name:
        return RedirectResponse(url="/taxonomy", status_code=303)
    data = _read_taxonomy()
    ready = set(data["ready_buckets"])
    ready.add(f"{section}/{name}")
    _write_taxonomy(sorted(ready), sorted(set(data["review_buckets"])))
    ensure_taxonomy_dirs()
    return RedirectResponse(url="/taxonomy", status_code=303)

@app.post("/taxonomy/save", response_class=HTMLResponse)
async def taxonomy_save(request: Request,
                        ready_selected: List[str] = Form(default=[]),
                        review_selected: List[str] = Form(default=[]),
                        add_candidates: List[str] = Form(default=[]),
                        add_suggestions: List[str] = Form(default=[])):
    data = _read_taxonomy()
    ready = set(ready_selected) | set(add_candidates) | set(add_suggestions)
    review = set(review_selected) | set(data["review_buckets"])  # zwykle review zostawiamy jak jest
    _write_taxonomy(sorted(ready), sorted(review))
    ensure_taxonomy_dirs()
    return RedirectResponse(url="/taxonomy", status_code=303)
