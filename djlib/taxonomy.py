from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Tuple
import yaml

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

# gdzie trzymamy definicję taksonomii
REPO_ROOT = Path(__file__).resolve().parents[1]
TAXONOMY_PATH = REPO_ROOT / "taxonomy.yml"
TAXONOMY_LOCAL_PATH = REPO_ROOT / "taxonomy.local.yml"

_DEFAULT_READY_BUCKETS: list[str] = [
    "MIXES",
]

_DEFAULT_REVIEW_BUCKETS: List[str] = [
    "UNDECIDED",
    "NEEDS EDIT",
]

def _read_taxonomy() -> Dict[str, List[str]]:
    # Najpierw sprawdź lokalny plik taxonomy.local.yml (aktualne buckety użytkownika)
    if TAXONOMY_LOCAL_PATH.exists():
        try:
            with TAXONOMY_LOCAL_PATH.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            ready = data.get("ready_buckets") or []
            review = data.get("review_buckets") or []
            # sanity
            ready = [str(x).strip() for x in ready if str(x).strip()]
            review = [str(x).strip() for x in review if str(x).strip()]
            if ready or review:  # jeśli ma jakąś zawartość, użyj
                return {"ready_buckets": ready, "review_buckets": review}
        except Exception as e:
            print("[taxonomy] local load failed:", e)
    
    # Fallback do głównego taxonomy.yml (domyślne buckety)
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
    with TAXONOMY_LOCAL_PATH.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)

def allowed_targets() -> list[str]:
    data = _read_taxonomy()
    ready  = [f"READY TO PLAY/{x}" for x in data["ready_buckets"]]
    review = [f"REVIEW QUEUE/{x}" for x in data["review_buckets"]]
    return ready + review

def is_valid_target(value: str) -> bool:
    return (value or "") in set(allowed_targets())

def target_to_path(target: str) -> Path | None:
    from djlib.config import load_config
    cfg = load_config()
    lib = Path(cfg["LIB_ROOT"])
    ready = lib / "READY TO PLAY"
    review = lib / "REVIEW QUEUE"
    t = (target or "").strip().strip("/")
    if not t:
        return None
    if t.startswith("READY TO PLAY/"):
        rel = t.split("/", 1)[1] if "/" in t else ""
        return ready / rel
    if t.startswith("REVIEW QUEUE/"):
        rel = t.split("/", 1)[1] if "/" in t else ""
        return review / rel
    return None


def ensure_taxonomy_folders() -> None:
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

def detect_taxonomy_from_fs(lib_root: Path) -> Dict[str, List[str]]:
    """
    Wykrywa istniejącą strukturę folderów w LIB_ROOT i zwraca taksonomię.
    Skanuje READY TO PLAY i REVIEW QUEUE w poszukiwaniu podfolderów.
    Zawsze dodaje domyślne buckety jeśli nie zostały wykryte.
    """
    ready_buckets = []
    review_buckets = []
    
    # Skanuj READY TO PLAY
    ready_root = lib_root / "READY TO PLAY"
    if ready_root.exists():
        # Najpierw CLUB
        club_root = ready_root / "CLUB"
        if club_root.exists():
            club_items = [item for item in sorted(club_root.iterdir()) if item.is_dir()]
            for item in club_items:
                ready_buckets.append(f"CLUB/{item.name}")
        
        # Potem OPEN FORMAT
        openf_root = ready_root / "OPEN FORMAT"
        if openf_root.exists():
            openf_items = [item for item in sorted(openf_root.iterdir()) if item.is_dir()]
            for item in openf_items:
                ready_buckets.append(f"OPEN FORMAT/{item.name}")
        
        # Na koniec top-level (bez prefiksu)
        toplevel_items = [item for item in sorted(ready_root.iterdir()) if item.is_dir() and item.name not in {"CLUB", "OPEN FORMAT"}]
        for item in toplevel_items:
            ready_buckets.append(item.name)
    
    # Skanuj REVIEW QUEUE
    review_root = lib_root / "REVIEW QUEUE"
    if review_root.exists():
        review_items = [item for item in sorted(review_root.iterdir()) if item.is_dir()]
        for item in review_items:
            review_buckets.append(item.name)
    
    # Dodaj domyślne buckety jeśli nie zostały wykryte
    ready_set = set(ready_buckets)
    review_set = set(review_buckets)
    
    # Zawsze dodaj MIXES jeśli nie ma
    if "MIXES" not in ready_set:
        ready_buckets.append("MIXES")
    
    # Dodaj domyślne review buckety jeśli nie zostały wykryte
    for default_review in _DEFAULT_REVIEW_BUCKETS:
        if default_review not in review_set:
            review_buckets.append(default_review)
    
    return {
        "ready_buckets": ready_buckets,
        "review_buckets": review_buckets,
    }
