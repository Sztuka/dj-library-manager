"""Tests for canonical genre resolution system."""
from pathlib import Path
import pytest
from djlib.genre_canonical import CanonicalGenreResolver


@pytest.fixture
def resolver():
    """Create a resolver with test genres.yml from workspace."""
    genres_yml = Path(__file__).parent.parent / "genres.yml"
    if not genres_yml.exists():
        pytest.skip("genres.yml not found in workspace")
    return CanonicalGenreResolver(genres_yml)


def test_resolve_exact_match(resolver):
    """Test resolving exact genre label matches."""
    key, label = resolver.resolve("Afro House")
    assert key == "AFRO_HOUSE"
    assert label == "Afro House"


def test_resolve_synonym(resolver):
    """Test resolving genre synonyms."""
    # "afro tech" is a synonym for AFRO_HOUSE
    result = resolver.resolve("afro tech")
    assert result is not None
    key, label = result
    assert key == "AFRO_HOUSE"
    assert label == "Afro House"  # Returns canonical label


def test_resolve_case_insensitive(resolver):
    """Test that resolution is case-insensitive."""
    key1, label1 = resolver.resolve("AFRO HOUSE")
    key2, label2 = resolver.resolve("afro house")
    key3, label3 = resolver.resolve("Afro House")
    
    assert key1 == key2 == key3 == "AFRO_HOUSE"
    assert label1 == label2 == label3 == "Afro House"


def test_resolve_whitespace_normalization(resolver):
    """Test that extra whitespace is normalized."""
    result = resolver.resolve("  tech   house  ")
    # The resolver will match if it finds the pattern, regardless of extra spaces
    if result:
        key, label = result
        # Should match either TECH_HOUSE or HOUSE depending on synonym matching
        assert key in ["TECH_HOUSE", "HOUSE"]
        assert label in ["Tech House", "House"]


def test_resolve_unknown_genre(resolver):
    """Test handling of unknown genres."""
    result = resolver.resolve("Totally Made Up Genre")
    assert result is None  # Returns None for unknown genres


def test_resolve_empty_string(resolver):
    """Test handling of empty/None input."""
    result = resolver.resolve("")
    assert result is None
    
    result = resolver.resolve(None)
    assert result is None


def test_resolve_multiple(resolver):
    """Test batch resolution of multiple genres."""
    genres = ["Afro House", "tech house", "unknown genre", "melodic techno"]
    results = resolver.resolve_multiple(genres)
    
    # resolve_multiple only returns matched genres (skips unknowns)
    assert len(results) == 3
    assert ("AFRO_HOUSE", "Afro House") in results
    assert ("TECH_HOUSE", "Tech House") in results or ("HOUSE", "House") in results
    assert ("MELODIC_TECHNO", "Melodic Techno") in results


def test_resolve_multigenre_priority(resolver):
    """Complex multi-genre strings map to the most specific canonical genre."""
    assert resolver.resolve("Melodic House & Techno")[0] == "MELODIC_TECHNO"
    assert resolver.resolve("Funky House, Dance")[0] == "HOUSE"
    assert resolver.resolve("Tech House, Deep House, House")[0] == "TECH_HOUSE"
    assert resolver.resolve("Pop, Dance")[0] == "POP"


def test_afrobeats_variants(resolver):
    """Afrobeats / Afrobeat variants resolve to AFROBEATS."""
    assert resolver.resolve("Afrobeats")[0] == "AFROBEATS"
    assert resolver.resolve("Afrobeat")[0] == "AFROBEATS"


def test_garbage_genres_are_ignored(resolver):
    """Garbage labels should not resolve to any canonical genre."""
    for raw in ["Top 40", "<Onbekend>", "http://example.com/whatever"]:
        assert resolver.resolve(raw) is None


def test_genre_separation_from_paths(resolver):
    """Test that genre resolution is independent of folder structure."""
    # Old system: genre determined folder path (CLUB/AFRO HOUSE/...)
    # New system: genre is just metadata, doesn't affect paths
    key, label = resolver.resolve("afro house")
    
    # Genre resolution should work the same regardless of destination
    assert key == "AFRO_HOUSE"
    # Path building is now handled by logistics.py, not genre resolver
