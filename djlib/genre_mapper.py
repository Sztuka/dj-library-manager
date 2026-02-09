"""Genre mapper: maps genre_suggest to canonical genres from genres.yml"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import yaml
import re

# Cache for loaded genres
_GENRES_CACHE: Dict[str, Dict[str, List[str]]] | None = None

def _load_genres_yml() -> Dict[str, Dict[str, List[str]]]:
    """Load genres.yml and return dict of canonical_id -> {label, synonyms, boost, ...}"""
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
    2. For each part, find all matching synonym entries in genres.yml
    3. Return the match with highest boost (most specific genre wins)
    4. On tie, return first match (position priority)
    """
    if not genre_suggest or not genre_suggest.strip():
        return None
    
    genres_data = _load_genres_yml()
    if not genres_data:
        return None
    
    # Build reverse map: normalized_synonym -> (canonical_label, boost)
    synonym_map: Dict[str, Tuple[str, float]] = {}
    for genre_id, genre_info in genres_data.items():
        label = genre_info.get("label", "")
        if not isinstance(label, str):
            continue
        synonyms = genre_info.get("synonyms", [])
        if not isinstance(synonyms, list):
            continue
        boost = float(genre_info.get("boost", 1.0))
        
        # Add label itself as synonym
        if label:
            norm_label = _normalize_genre(label)
            # Only store if not already mapped with higher boost
            if norm_label not in synonym_map or boost > synonym_map[norm_label][1]:
                synonym_map[norm_label] = (label, boost)
        
        # Add all synonyms
        for syn in synonyms:
            if isinstance(syn, str):
                norm_syn = _normalize_genre(syn)
                if norm_syn not in synonym_map or boost > synonym_map[norm_syn][1]:
                    synonym_map[norm_syn] = (label, boost)
    
    # Try to match each part of genre_suggest, pick highest-boost match
    parts = [p.strip() for p in genre_suggest.split(",") if p.strip()]
    
    best_label: Optional[str] = None
    best_boost: float = -1.0
    
    for part in parts:
        normalized = _normalize_genre(part)
        if normalized in synonym_map:
            label, boost = synonym_map[normalized]
            if boost > best_boost:
                best_boost = boost
                best_label = label
    
    return best_label


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
