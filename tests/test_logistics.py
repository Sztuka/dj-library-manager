"""Tests for the new logistics-only path building system."""
from pathlib import Path
import pytest
from djlib.logistics import (
    build_library_path, 
    build_reject_path, 
    build_archive_path, 
    build_mixes_path,
    sanitize_dir_segment
)


def test_build_library_path_basic():
    """Test library path construction: {LIB_ROOT}/{Artist}/{filename}"""
    path = build_library_path("Disclosure", "Disclosure - Latch (Original Mix).mp3")
    # Returns absolute path directly under LIB_ROOT (no LIBRARY/ subfolder)
    assert "Disclosure/Disclosure - Latch (Original Mix).mp3" in str(path)
    assert path.name == "Disclosure - Latch (Original Mix).mp3"
    assert path.parent.name == "Disclosure"


def test_build_library_path_special_chars():
    """Test library path handles special characters in artist names."""
    path = build_library_path("Beyoncé", "track.mp3")
    assert "Beyoncé/track.mp3" in str(path)
    assert path.parent.name == "Beyoncé"
    
    path = build_library_path("AC/DC", "track.mp3")
    assert "AC-DC" in str(path)
    assert path.parent.name == "AC-DC"
    
    # Other slashes should also be sanitized
    path = build_library_path("Artist / Name", "track.mp3")
    assert path.parent.name == "Artist - Name"


def test_build_library_path_normalization():
    """Test that artist names are stripped of leading/trailing whitespace."""
    path = build_library_path("  The   Weeknd  ", "track.mp3")
    # Leading/trailing whitespace is stripped, and multiple spaces are collapsed
    assert "The Weeknd" in str(path)
    assert path.parent.name == "The Weeknd"


def test_build_reject_path():
    """Test reject path construction: {REJECT_ROOT}/{filename} (flat)"""
    path = build_reject_path("duplicate_track.mp3")
    assert path.name == "duplicate_track.mp3"
    # Reject is now outside library (separate root)
    assert "Reject" in str(path)


def test_build_archive_path():
    """Test archive path construction: {ARCHIVE_ROOT}/{Artist}/{filename}"""
    path = build_archive_path("Old Artist", "old_track.mp3")
    assert path.name == "old_track.mp3"
    assert path.parent.name == "Old Artist"
    # Archive is now outside library (separate root)
    assert "Archive" in str(path)


def test_build_mixes_path():
    """Test mixes path construction: {MIXES_ROOT}/{filename} (flat)"""
    path = build_mixes_path("Solomun - Live at Diynamic Festival [125].mp3")
    assert path.name == "Solomun - Live at Diynamic Festival [125].mp3"
    assert "MIXES" in str(path)
    # Mixes have flat structure (no artist subfolders)


def test_library_path_no_subdirs():
    """Test that library paths don't include genre subdirectories."""
    # Old system: READY TO PLAY/CLUB/AFRO HOUSE/Artist - Track.mp3
    # New system: Music Library/Artist/Artist - Track.mp3
    path = build_library_path("Black Coffee", "track.mp3")
    assert "AFRO HOUSE" not in str(path)
    assert "CLUB" not in str(path)
    assert "Black Coffee/track.mp3" in str(path)
