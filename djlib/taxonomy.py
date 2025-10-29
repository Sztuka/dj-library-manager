from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Tuple
import yaml

# lokalizacje główne bierzemy z config.py
from djlib.config import READY_TO_PLAY_DIR, REVIEW_QUEUE_DIR
from djlib.config import LIB_ROOT

# gdzie trzymamy definicję taksonomii
REPO_ROOT = Path(__file__).resolve().parents[1]
TAXONOMY_PATH = REPO_ROOT / "taxonomy.yml"

_DEFAULT_READY_BUCKETS: list[str] = [
    # CLUB
    "CLUB/HOUSE",
    "CLUB/TECH HOUSE",
    "CLUB/TECHNO",
    "CLUB/MELODIC TECHNO",
    "CLUB/AFRO HOUSE",
    "CLUB/ELECTRO SWING",
    "CLUB/ELECTRO",
    "CLUB/DNB",
    "CLUB/TRANCE",
    "CLUB/DEEP HOUSE",

    # OPEN FORMAT
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

    "MIXES",
]

_DEFAULT_REVIEW_BUCKETS: List[str] = [
    "UNDECIDED",
    "NEEDS EDIT",
]

def _read_taxonomy() -> Dict[str, List[str]]:
    if not TAXONOMY_PATH.exists():
        return {
            "ready_buckets": list(_DEFAULT_READY_BUCKETS),
            "review_buckets": list(_DEFAULT_REVIEW_BUCKETS),
        }
    with TAXONOMY_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    ready = data.get("ready_buckets") or []
    review = data.get("review_buckets") or []
    # sanity
    ready = [str(x).strip() for x in ready if str(x).strip()]
    review = [str(x).strip() for x in review if str(x).strip()]
    if not ready:
        ready = list(_DEFAULT_READY_BUCKETS)
    if not review:
        review = list(_DEFAULT_REVIEW_BUCKETS)
    return {"ready_buckets": ready, "review_buckets": review}

def _write_taxonomy(ready: List[str], review: List[str]) -> None:
    payload = {
        "ready_buckets": sorted(set(ready)),
        "review_buckets": sorted(set(review)),
    }
    with TAXONOMY_PATH.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)

def allowed_targets() -> list[str]:
    data = _read_taxonomy()
    ready  = [f"READY TO PLAY/{x}" for x in data["ready_buckets"]]
    review = [f"REVIEW QUEUE/{x}" for x in data["review_buckets"]]
    return ready + review

def is_valid_target(value: str) -> bool:
    return (value or "") in set(allowed_targets())

def target_to_path(target: str) -> Path | None:
    t = (target or "").strip().strip("/")
    if not t:
        return None
    if t.startswith("READY TO PLAY/"):
        rel = t.split("/", 1)[1] if "/" in t else ""
        return READY_TO_PLAY_DIR / rel
    if t.startswith("REVIEW QUEUE/"):
        rel = t.split("/", 1)[1] if "/" in t else ""
        return REVIEW_QUEUE_DIR / rel
    return None


def ensure_taxonomy_dirs() -> None:
    """Tworzy wszystkie katalogi z taksonomii."""
    for t in allowed_targets():
        p = target_to_path(t)
        if p:
            p.mkdir(parents=True, exist_ok=True)

def add_ready_bucket(rel_key: str) -> None:
    """
    Dodaj bucket pod READY TO PLAY. rel_key np. 'CLUB/DNB' albo 'OPEN FORMAT/TRANCE'.
    """
    data = _read_taxonomy()
    ready = data["ready_buckets"]
    if rel_key not in ready:
        ready.append(rel_key)
        _write_taxonomy(ready, data["review_buckets"])

def add_review_bucket(name: str) -> None:
    data = _read_taxonomy()
    review = data["review_buckets"]
    if name not in review:
        review.append(name)
        _write_taxonomy(data["ready_buckets"], review)

def load_taxonomy() -> Dict[str, List[str]]:
    """Wczytaj taksonomię i zwróć jako słownik z kluczami ready_buckets i review_buckets."""
    return _read_taxonomy()

def save_taxonomy(data: Dict[str, List[str]]) -> None:
    """Zapisz taksonomię z podanego słownika."""
    ready = data.get("ready_buckets", [])
    review = data.get("review_buckets", [])
    _write_taxonomy(ready, review)
