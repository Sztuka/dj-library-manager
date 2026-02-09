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
    
    def __init__(self, key: str, label: str, synonyms: List[str], description: str = "", boost: float = 1.0):
        self.key = key  # Canonical key (e.g., "AFRO_HOUSE")
        self.label = label  # Human-readable label (e.g., "Afro House")
        self.synonyms = [self._normalize(s) for s in synonyms]
        self.description = description
        self.boost = boost  # Specificity multiplier from genres.yml (1.0 = generic parent)
    
    @staticmethod
    def _normalize(text: str) -> str:
        """Normalize text for matching: lowercase, punctuation to spaces, single spacing."""
        cleaned = re.sub(r'[^a-z0-9\s]', ' ', (text or '').lower())
        return re.sub(r'\s+', ' ', cleaned).strip()
    
    def matches(self, raw_genre: str) -> bool:
        """Check if raw genre string matches this definition."""
        return self.best_match_length(raw_genre) > 0

    def best_match_length(self, raw_genre: str) -> int:
        """Return length of the longest matching synonym, or 0 if no match.
        
        Used by CanonicalGenreResolver to prefer the most specific genre
        when multiple genres match a raw string (e.g. "Melodic House & Techno"
        matches both "house" and "melodic house" — the longer match wins).
        """
        normalized = self._normalize(raw_genre)
        if not normalized:
            return 0
        
        best = 0
        
        # Check exact match (highest specificity)
        if normalized in self.synonyms:
            return len(normalized)

        # Check substring matches, track longest
        for syn in self.synonyms:
            if re.search(rf"\b{re.escape(syn)}\b", normalized):
                if len(syn) > best:
                    best = len(syn)

        return best


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
            boost = float(definition.get("boost", 1.0))
            
            if not isinstance(synonyms, list):
                synonyms = []
            
            self.genres[key] = GenreDefinition(key, label, synonyms, description, boost=boost)
    
    def _resolve_single(self, raw_genre: str) -> Optional[Tuple[str, str, int, float]]:
        """Resolve a single genre phrase to (key, label, match_length, boost).
        
        Prefers longest synonym match; on tie, highest boost wins.
        """
        best_key: Optional[str] = None
        best_label: Optional[str] = None
        best_length: int = 0
        best_boost: float = 0.0
        
        for key, definition in self.genres.items():
            match_len = definition.best_match_length(raw_genre)
            if match_len > best_length or (match_len == best_length and match_len > 0 and definition.boost > best_boost):
                best_length = match_len
                best_boost = definition.boost
                best_key = key
                best_label = definition.label
        
        if best_key and best_label and best_length > 0:
            return (best_key, best_label, best_length, best_boost)
        return None

    def resolve(self, raw_genre: str) -> Optional[Tuple[str, str]]:
        """Resolve raw genre to (canonical_key, label).
        
        Handles both single genre strings ("Melodic House & Techno") and
        comma-separated lists ("Pop, Dance").  For comma-separated inputs,
        each part is resolved independently and the best match wins
        (longest synonym, then highest boost, then first-listed).
        
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
        
        # Comma-separated inputs ("Pop, Dance") → resolve each part independently
        parts = [p.strip() for p in raw_genre.split(",") if p.strip()]
        
        if len(parts) > 1:
            best_part: Optional[Tuple[str, str, int, float]] = None
            best_part_ratio: float = 0.0
            
            for part in parts:
                result = self._resolve_single(part)
                if result is None:
                    continue
                _, _, match_len, boost = result
                # Normalize match length by part length for fair cross-part comparison
                part_norm_len = len(GenreDefinition._normalize(part)) or 1
                ratio = match_len / part_norm_len
                if best_part is None or ratio > best_part_ratio or (
                    ratio == best_part_ratio and boost > best_part[3]
                ):
                    best_part = result
                    best_part_ratio = ratio
            
            if best_part:
                return (best_part[0], best_part[1])
        
        # Single phrase (or comma-split found nothing): try whole string
        # Handles compound names like "Melodic House & Techno"
        whole_result = self._resolve_single(raw_genre)
        if whole_result:
            return (whole_result[0], whole_result[1])
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
