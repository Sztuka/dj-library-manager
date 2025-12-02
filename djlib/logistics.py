"""Simple logistics-only path building (no taxonomy/buckets).

This module provides deterministic path construction for the library cleaner workflow.
Folders are purely organizational, not expressions of musical "sets" or "vibes".
"""

from __future__ import annotations
from pathlib import Path
from typing import Literal
import re
from djlib.config import load_config

# Valid destination types
DestinationType = Literal["library", "reject", "archive", "mixes"]


def sanitize_dir_segment(name: str) -> str:
    """Sanitize a string for use as a directory name on disk.
    
    This function ensures that directory names don't contain characters that
    would be interpreted as path separators or cause filesystem issues,
    while preserving the original name in metadata/tags/XLSX.
    
    Rules:
    - Special case: "AC/DC" variants → "AC-DC"
    - Replace invalid path chars '<>:"/\\|?*' with "-"
    - Collapse multiple whitespace to single space
    - Strip leading/trailing whitespace
    - Return "_" if result is empty
    
    Args:
        name: Artist name or other directory segment
        
    Returns:
        Safe directory name for filesystem use
        
    Examples:
        >>> sanitize_dir_segment("AC/DC")
        'AC-DC'
        >>> sanitize_dir_segment("Artist / Name")
        'Artist - Name'
        >>> sanitize_dir_segment("Normal Artist")
        'Normal Artist'
    """
    if not name:
        return "_"
    
    # Special case: AC/DC variants (case-insensitive, spaces ignored)
    normalized = re.sub(r'[\s]+', '', name.lower())
    if normalized in ('ac/dc', 'ac-dc', 'acdc'):
        return "AC-DC"
    
    # Replace invalid path characters with dash
    # Invalid chars: < > : " / \ | ? *
    result = re.sub(r'[<>:"/\\|?*]+', '-', name)
    
    # Collapse multiple whitespace (spaces, tabs, newlines) into single space
    result = re.sub(r'\s+', ' ', result)
    
    # Strip leading/trailing whitespace
    result = result.strip()
    
    # Return fallback if empty
    return result if result else "_"


def get_destination_path(dest_type: DestinationType) -> Path:
    """Get base path for a destination type.
    
    Args:
        dest_type: One of "library", "reject", "archive", "mixes"
        
    Returns:
        Path to the destination directory
        
    Example:
        >>> get_destination_path("library")
        Path("/Users/sztuka/Music Library")
        >>> get_destination_path("reject")
        Path("/Users/sztuka/DJ Reject")
    """
    cfg = load_config()
    
    if dest_type == "library":
        # Main library: directly under LIB_ROOT (no LIBRARY/ subfolder)
        return Path(cfg["LIB_ROOT"])
    elif dest_type == "reject":
        # Rejected files: separate folder outside library
        return Path(cfg.get("REJECT_ROOT", str(Path(cfg["LIB_ROOT"]).parent / "DJ Reject")))
    elif dest_type == "archive":
        # Archived files: separate folder outside library
        return Path(cfg.get("ARCHIVE_ROOT", str(Path(cfg["LIB_ROOT"]).parent / "DJ Archive")))
    elif dest_type == "mixes":
        # DJ mixes: flat structure in MIXES subfolder
        return Path(cfg.get("MIXES_ROOT", str(Path(cfg["LIB_ROOT"]) / "MIXES")))
    else:
        raise ValueError(f"Invalid dest_type: {dest_type}")


def build_library_path(artist: str, filename: str) -> Path:
    """Build final path for accepted track in main library.
    
    Structure: {LIB_ROOT}/{Artist}/{filename}
    Example: ~/Music Library/Âme/Âme - Rej (Solomun Remix) [1A 123].flac
    
    Note: Artist name is sanitized for filesystem safety (e.g., "AC/DC" → "AC-DC")
    but the original artist name is preserved in tags and metadata.
    
    Args:
        artist: Artist name (used for folder grouping)
        filename: Final filename (built by filename.build_final_filename)
        
    Returns:
        Full path to destination file
    """
    library_dir = get_destination_path("library")
    artist_clean = artist.strip() or "Unknown Artist"
    artist_folder = sanitize_dir_segment(artist_clean)
    return library_dir / artist_folder / filename


def build_reject_path(filename: str) -> Path:
    """Build path for rejected track.
    
    Structure: {REJECT_ROOT}/{filename} (flat, no artist subfolders)
    Example: ~/DJ Reject/bad_quality.mp3
    
    Args:
        filename: Final filename
        
    Returns:
        Full path to destination file
    """
    reject_dir = get_destination_path("reject")
    return reject_dir / filename


def build_archive_path(artist: str, filename: str) -> Path:
    """Build path for archived track.
    
    Structure: {ARCHIVE_ROOT}/{Artist}/{filename}
    Example: ~/DJ Archive/Old Artist/track.flac
    
    Note: Artist name is sanitized for filesystem safety (e.g., "AC/DC" → "AC-DC")
    but the original artist name is preserved in tags and metadata.
    
    Args:
        artist: Artist name
        filename: Final filename
        
    Returns:
        Full path to destination file
    """
    archive_dir = get_destination_path("archive")
    artist_clean = artist.strip() or "Unknown Artist"
    artist_folder = sanitize_dir_segment(artist_clean)
    return archive_dir / artist_folder / filename


def build_mixes_path(filename: str) -> Path:
    """Build path for DJ mix file.
    
    Structure: {MIXES_ROOT}/{filename} (flat, no artist subfolders)
    Example: ~/Music Library/MIXES/Artist - Mix Name [120].mp3
    
    Mixes typically don't have traditional artist/title/genre metadata,
    so they get special flat-file treatment.
    
    Args:
        filename: Final filename
        
    Returns:
        Full path to destination file
    """
    mixes_dir = get_destination_path("mixes")
    return mixes_dir / filename


def ensure_logistics_dirs() -> None:
    """Create all logistics directories (library, reject, archive, mixes)."""
    for dest_type in ["library", "reject", "archive", "mixes"]:
        path = get_destination_path(dest_type)  # type: ignore[arg-type]
        path.mkdir(parents=True, exist_ok=True)
