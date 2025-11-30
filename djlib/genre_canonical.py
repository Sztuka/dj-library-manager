"""Canonical genre resolver - maps raw genre strings to normalized canonical genres.

This module provides deterministic genre normalization without dependency on
taxonomy or bucket mappings. It's purely about genre classification, not folder structure.
"""

from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import yaml
import re

REPO_ROOT = Path(__file__).resolve().parents[1]
GENRES_FILE = REPO_ROOT / "genres.yml"


class GenreDefinition:
    """A canonical genre with its label and synonyms."""
    
    def __init__(self, key: str, label: str, synonyms: List[str], description: str = ""):
        self.key = key  # Canonical key (e.g., "AFRO_HOUSE")
        self.label = label  # Human-readable label (e.g., "Afro House")
        self.synonyms = [self._normalize(s) for s in synonyms]
        self.description = description
    
    @staticmethod
    def _normalize(text: str) -> str:
        """Normalize text for matching: lowercase, punctuation to spaces, single spacing."""
        cleaned = re.sub(r'[^a-z0-9\s]', ' ', (text or '').lower())
        return re.sub(r'\s+', ' ', cleaned).strip()
    
    def matches(self, raw_genre: str) -> bool:
        """Check if raw genre string matches this definition."""
        normalized = self._normalize(raw_genre)
        if not normalized:
            return False
        
        # Check exact match
        if normalized in self.synonyms:
            return True

        # Check if any synonym appears as a whole-word phrase inside the raw
        # genre (avoid partial substring matches like "dub" vs "dubstep").
        for syn in self.synonyms:
            if re.search(rf"\b{re.escape(syn)}\b", normalized):
                return True

        return False


class CanonicalGenreResolver:
    """Resolves raw genre strings to canonical genre keys and labels."""
    
    def __init__(self, genres_file: Path = GENRES_FILE):
        self.genres: Dict[str, GenreDefinition] = {}
        self._load_genres(genres_file)
    
    def _load_genres(self, path: Path) -> None:
        """Load genre definitions from YAML."""
        if not path.exists():
            return
        
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        
        for key, definition in data.items():
            if not isinstance(definition, dict):
                continue
            
            label = definition.get("label", key)
            synonyms = definition.get("synonyms", [])
            description = definition.get("description", "")
            
            if not isinstance(synonyms, list):
                synonyms = []
            
            self.genres[key] = GenreDefinition(key, label, synonyms, description)
    
    def resolve(self, raw_genre: str) -> Optional[Tuple[str, str]]:
        """Resolve raw genre to (canonical_key, label).
        
        Args:
            raw_genre: Raw genre string from tags/metadata
            
        Returns:
            Tuple of (canonical_key, label) or None if no match
            
        Example:
            >>> resolver = CanonicalGenreResolver()
            >>> resolver.resolve("afro-house")
            ("AFRO_HOUSE", "Afro House")
        """
        if not raw_genre or not raw_genre.strip():
            return None
        
        # Try direct match first
        for key, definition in self.genres.items():
            if definition.matches(raw_genre):
                return (key, definition.label)
        
        return None
    
    def resolve_multiple(self, raw_genres: List[str]) -> List[Tuple[str, str]]:
        """Resolve multiple raw genres, return unique canonical genres.
        
        Args:
            raw_genres: List of raw genre strings
            
        Returns:
            List of (canonical_key, label) tuples, deduplicated
        """
        seen_keys = set()
        results = []
        
        for raw in raw_genres:
            resolved = self.resolve(raw)
            if resolved and resolved[0] not in seen_keys:
                seen_keys.add(resolved[0])
                results.append(resolved)
        
        return results
    
    def get_all_labels(self) -> List[str]:
        """Get all available genre labels for UI dropdowns."""
        return sorted([g.label for g in self.genres.values()])
    
    def get_canonical_key(self, label: str) -> Optional[str]:
        """Reverse lookup: get canonical key from label."""
        for key, definition in self.genres.items():
            if definition.label.lower() == label.lower():
                return key
        return None


# Global resolver instance
_resolver: Optional[CanonicalGenreResolver] = None


def get_resolver() -> CanonicalGenreResolver:
    """Get global resolver instance (singleton)."""
    global _resolver
    if _resolver is None:
        _resolver = CanonicalGenreResolver()
    return _resolver


def resolve_genre(raw_genre: str) -> Optional[Tuple[str, str]]:
    """Convenience function: resolve raw genre to (canonical_key, label)."""
    return get_resolver().resolve(raw_genre)


def resolve_genres(raw_genres: List[str]) -> List[Tuple[str, str]]:
    """Convenience function: resolve multiple genres."""
    return get_resolver().resolve_multiple(raw_genres)


def get_genre_labels() -> List[str]:
    """Convenience function: get all available genre labels."""
    return get_resolver().get_all_labels()
