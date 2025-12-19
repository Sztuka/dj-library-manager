"""Genre mapper: maps genre_suggest to canonical genres from genres.yml"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import yaml
import re

# Cache for loaded genres
_GENRES_CACHE: Dict[str, Dict[str, List[str]]] | None = None

def _load_genres_yml() -> Dict[str, Dict[str, List[str]]]:
    """Load genres.yml and return dict of canonical_id -> {label, synonyms}"""
    global _GENRES_CACHE
    if _GENRES_CACHE is not None:
        return _GENRES_CACHE
    
    genres_path = Path(__file__).parent.parent / "genres.yml"
    if not genres_path.exists():
        return {}
    
    with genres_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    
    _GENRES_CACHE = data
    return data


def _normalize_genre(g: str) -> str:
    """Normalize genre string for comparison: lowercase, no spaces/punctuation"""
    g = g.lower()
    g = re.sub(r"[^\w\s]", "", g)  # remove punctuation
    g = re.sub(r"\s+", "", g)  # remove spaces
    return g


def map_genre(genre_suggest: str) -> Optional[str]:
    """Map genre_suggest to canonical genre label from genres.yml.
    
    Returns canonical label (e.g., "Afro House") if match found, None otherwise.
    
    Matching strategy:
    1. Split genre_suggest by comma (e.g., "afro house, tech house, melodic")
    2. For each part, try to find exact synonym match in genres.yml
    3. Return first match (prioritize first genre in genre_suggest)
    """
    if not genre_suggest or not genre_suggest.strip():
        return None
    
    genres_data = _load_genres_yml()
    if not genres_data:
        return None
    
    # Build reverse map: normalized_synonym -> canonical_label
    synonym_map: Dict[str, str] = {}
    for genre_id, genre_info in genres_data.items():
        label = genre_info.get("label", "")
        if not isinstance(label, str):
            continue
        synonyms = genre_info.get("synonyms", [])
        if not isinstance(synonyms, list):
            continue
        
        # Add label itself as synonym
        if label:
            synonym_map[_normalize_genre(label)] = label
        
        # Add all synonyms
        for syn in synonyms:
            if isinstance(syn, str):
                synonym_map[_normalize_genre(syn)] = label
    
    # Try to match each part of genre_suggest
    parts = [p.strip() for p in genre_suggest.split(",") if p.strip()]
    
    # HEURISTIC: Prioritize "rockabilly" if present (more specific than "classic rock")
    # Example: "classic rock, rockabilly, 60s" → should map to "Rock 'n' Roll" not "Rock"
    for part in parts:
        normalized = _normalize_genre(part)
        if normalized == "rockabilly" and normalized in synonym_map:
            return synonym_map[normalized]
    
    # Normal matching: first match wins
    for part in parts:
        normalized = _normalize_genre(part)
        if normalized in synonym_map:
            return synonym_map[normalized]
    
    return None


def map_genres_batch(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    """Map genre_suggest to genre for batch of rows.
    
    Returns dict with:
    - "mapped": list of (file_path, genre_suggest, mapped_genre)
    - "unmapped": list of (file_path, genre_suggest) for rows without match
    """
    mapped: List[Tuple[str, str, str]] = []
    unmapped: List[Tuple[str, str]] = []
    
    for row in rows:
        file_path = row.get("file_path", "")
        genre_suggest = (row.get("genre_suggest") or "").strip()
        
        if not genre_suggest:
            continue
        
        mapped_genre = map_genre(genre_suggest)
        if mapped_genre:
            mapped.append((file_path, genre_suggest, mapped_genre))
        else:
            unmapped.append((file_path, genre_suggest))
    
    return {
        "mapped": mapped,
        "unmapped": unmapped
    }
