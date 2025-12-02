"""
Tests for featuring artist normalization in derive_local_metadata.

Ensures that:
- feat/ft/featuring info is moved from artist to title
- All variations are normalized to "feat." (with dot)
- & collaborations stay in artist
- Multiple sources are deduplicated
"""
from pathlib import Path
from djlib.enrich import derive_local_metadata, _normalize_features


def test_feat_in_artist_simple():
    """Simple feat in artist should move to title."""
    fake_path = Path("/tmp/test.mp3")
    
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Bob feat. Carol", "title": "Track"}
    )
    assert artist == "Bob", f"Expected 'Bob', got '{artist}'"
    assert title == "Track feat. Carol", f"Expected 'Track feat. Carol', got '{title}'"


def test_feat_variations_normalized():
    """All feat variations should normalize to 'feat.'"""
    fake_path = Path("/tmp/test.mp3")
    
    # feat.
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Bob feat. Carol", "title": "Track"}
    )
    assert "feat. Carol" in title
    
    # feat (no dot)
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Bob feat Carol", "title": "Track"}
    )
    assert "feat. Carol" in title
    
    # ft.
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Bob ft. Carol", "title": "Track"}
    )
    assert "feat. Carol" in title
    
    # ft (no dot)
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Bob ft Carol", "title": "Track"}
    )
    assert "feat. Carol" in title
    
    # featuring
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Bob featuring Carol", "title": "Track"}
    )
    assert "feat. Carol" in title


def test_ampersand_collaboration_preserved():
    """& collaborations should stay in artist."""
    fake_path = Path("/tmp/test.mp3")
    
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Bob & Alice", "title": "Track"}
    )
    assert artist == "Bob & Alice", f"Expected 'Bob & Alice', got '{artist}'"
    assert title == "Track", f"Expected 'Track', got '{title}'"


def test_ampersand_and_feat():
    """& collaboration + feat should split correctly."""
    fake_path = Path("/tmp/test.mp3")
    
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Bob & Alice feat. Carol", "title": "Track"}
    )
    assert artist == "Bob & Alice", f"Expected 'Bob & Alice', got '{artist}'"
    assert title == "Track feat. Carol", f"Expected 'Track feat. Carol', got '{title}'"


def test_feat_in_title_brackets():
    """Feat in title (bracketed) should be normalized."""
    fake_path = Path("/tmp/test.mp3")
    
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Bob", "title": "Track (feat. Carol)"}
    )
    assert artist == "Bob", f"Expected 'Bob', got '{artist}'"
    assert title == "Track feat. Carol", f"Expected 'Track feat. Carol', got '{title}'"
    
    # Test with ft.
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Bob", "title": "Track (ft. Carol)"}
    )
    assert title == "Track feat. Carol", f"Expected normalized to 'feat.', got '{title}'"


def test_feat_in_title_inline():
    """Feat in title (inline) should be normalized."""
    fake_path = Path("/tmp/test.mp3")
    
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Bob", "title": "Track feat Carol"}
    )
    assert artist == "Bob", f"Expected 'Bob', got '{artist}'"
    assert title == "Track feat. Carol", f"Expected 'Track feat. Carol', got '{title}'"


def test_mixed_sources_deduplicated():
    """Feat from both artist and title should be deduplicated."""
    fake_path = Path("/tmp/test.mp3")
    
    # Same artist in both
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Bob feat. Carol", "title": "Track (feat. Carol)"}
    )
    assert artist == "Bob", f"Expected 'Bob', got '{artist}'"
    # Should only have Carol once
    assert title.count("Carol") == 1, f"Carol should appear once, got '{title}'"
    assert title == "Track feat. Carol", f"Expected 'Track feat. Carol', got '{title}'"
    
    # Different artists
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Bob feat. Carol", "title": "Track (feat. Dave)"}
    )
    assert artist == "Bob", f"Expected 'Bob', got '{artist}'"
    # Should have both Carol and Dave
    assert "Carol" in title and "Dave" in title, f"Expected both artists, got '{title}'"
    # Order may vary, so just check structure
    assert title.startswith("Track feat. ")
    assert "Carol" in title and "Dave" in title


def test_no_feat_anywhere():
    """No feat info should result in no changes."""
    fake_path = Path("/tmp/test.mp3")
    
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Bob & Alice", "title": "Track"}
    )
    assert artist == "Bob & Alice", f"Expected 'Bob & Alice', got '{artist}'"
    assert title == "Track", f"Expected 'Track', got '{title}'"


def test_multiple_spaces_normalized():
    """Multiple spaces should be normalized."""
    fake_path = Path("/tmp/test.mp3")
    
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Bobby  FEAT   cAROL", "title": "Track"}
    )
    assert artist == "Bobby", f"Expected 'Bobby', got '{artist}'"
    # Should have featuring artist (casing preserved from input)
    assert "feat. cAROL" in title, f"Expected feat artist, got '{title}'"
    # Check that multiple spaces were normalized (no double spaces)
    assert "  " not in title, f"Expected no double spaces, got '{title}'"


def test_case_insensitive_deduplication():
    """Case variations of same artist should deduplicate."""
    fake_path = Path("/tmp/test.mp3")
    
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Bob feat. CAROL", "title": "Track (feat. carol)"}
    )
    assert artist == "Bob", f"Expected 'Bob', got '{artist}'"
    # Should only have Carol once (preserving first occurrence casing)
    assert title.count("CAROL") + title.count("carol") + title.count("Carol") == 1, \
        f"Carol should appear once (any casing), got '{title}'"


def test_multiple_feat_artists_in_single_field():
    """Multiple feat artists in one field should be parsed."""
    fake_path = Path("/tmp/test.mp3")
    
    # Using & separator
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Bob feat. Carol & Dave", "title": "Track"}
    )
    assert artist == "Bob", f"Expected 'Bob', got '{artist}'"
    assert "Carol" in title and "Dave" in title, f"Expected both artists, got '{title}'"
    
    # Using comma separator
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Bob feat. Carol, Dave", "title": "Track"}
    )
    assert artist == "Bob", f"Expected 'Bob', got '{artist}'"
    assert "Carol" in title and "Dave" in title, f"Expected both artists, got '{title}'"


def test_empty_inputs():
    """Empty artist/title should not crash."""
    fake_path = Path("/tmp/unknown_file_12345.mp3")
    
    # With empty dict, will try filename parsing
    artist, title, _ = derive_local_metadata(fake_path, {})
    assert isinstance(artist, str)
    assert isinstance(title, str)
    
    # With explicit empty strings, should stay empty (but filename may provide fallback)
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "", "title": ""}
    )
    assert isinstance(artist, str)
    assert isinstance(title, str)


def test_feat_with_version_parentheses():
    """Feat should not interfere with version info in parentheses."""
    fake_path = Path("/tmp/test.mp3")
    
    artist, title, version = derive_local_metadata(
        fake_path,
        {
            "artist": "Bob feat. Carol",
            "title": "Track (Original Mix)",
            "version_info": "Original Mix"
        }
    )
    assert artist == "Bob", f"Expected 'Bob', got '{artist}'"
    # Version should be extracted to version_info, feat should be in title
    assert version == "Original Mix" or "Original Mix" in title
    assert "feat. Carol" in title, f"Expected feat in title, got '{title}'"


def test_normalize_features_direct():
    """Test _normalize_features function directly."""
    
    # Simple feat in artist
    artist, title = _normalize_features("Bob feat. Carol", "Track")
    assert artist == "Bob"
    assert title == "Track feat. Carol"
    
    # & preserved
    artist, title = _normalize_features("Bob & Alice", "Track")
    assert artist == "Bob & Alice"
    assert title == "Track"
    
    # & with feat
    artist, title = _normalize_features("Bob & Alice feat. Carol", "Track")
    assert artist == "Bob & Alice"
    assert title == "Track feat. Carol"
    
    # Feat in title only
    artist, title = _normalize_features("Bob", "Track (feat. Carol)")
    assert artist == "Bob"
    assert title == "Track feat. Carol"
    
    # Mixed sources
    artist, title = _normalize_features("Bob feat. Carol", "Track (feat. Dave)")
    assert artist == "Bob"
    assert "Carol" in title and "Dave" in title
    
    # No feat
    artist, title = _normalize_features("Bob", "Track")
    assert artist == "Bob"
    assert title == "Track"
    
    # Empty
    artist, title = _normalize_features("", "")
    assert artist == ""
    assert title == ""


def test_real_world_examples():
    """Test with real-world artist/title combinations."""
    fake_path = Path("/tmp/test.mp3")
    
    # Example 1: Drake feat. Rihanna
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Drake ft. Rihanna", "title": "Work"}
    )
    assert artist == "Drake"
    assert title == "Work feat. Rihanna"
    
    # Example 2: Multiple artists with &
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Daft Punk & Pharrell Williams feat. Nile Rodgers", "title": "Get Lucky"}
    )
    assert artist == "Daft Punk & Pharrell Williams"
    assert title == "Get Lucky feat. Nile Rodgers"
    
    # Example 3: Title already has feat in brackets
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Major Lazer", "title": "Lean On (feat. MØ & DJ Snake)"}
    )
    assert artist == "Major Lazer"
    # MØ and DJ Snake should be preserved
    assert "MØ" in title and "DJ Snake" in title
    
    # Example 4: Multiple feat variations
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Bob ft Carol", "title": "Track featuring Dave"}
    )
    assert artist == "Bob"
    assert "Carol" in title and "Dave" in title


def test_trailing_punctuation_cleaned():
    """Trailing dashes/spaces should be cleaned from title."""
    fake_path = Path("/tmp/test.mp3")
    
    artist, title, _ = derive_local_metadata(
        fake_path,
        {"artist": "Bob feat. Carol", "title": "Track -"}
    )
    assert artist == "Bob"
    assert title == "Track feat. Carol", f"Expected no trailing dash, got '{title}'"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
