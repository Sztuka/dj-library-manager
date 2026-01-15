"""
DJLIB Custom ID3 Tags - Persistent Track IDs for DJ Software Integration

This module provides utilities for writing and reading custom TXXX (ID3v2.4) 
and Vorbis Comment tags that store persistent track identifiers.

Custom tags (namespace: DJLIB_*):
- DJLIB_TRACK_ID: Our internal UUID for cross-referencing
- DJLIB_REKORDBOX_ID: Rekordbox database primary key
- DJLIB_TRAKTOR_ID: Traktor AUDIO_ID (base64 hash)
- DJLIB_SNAPSHOT_DATE: ISO 8601 timestamp when track was tagged
- DJLIB_ORIGINAL_PATH: Original file location (for debugging)

These tags are:
- Invisible to users (not displayed in DJ software/iTunes)
- Persistent across file moves/renames
- Non-invasive (don't affect metadata cleanup)
- Format-agnostic (work with MP3, FLAC, M4A, etc.)
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from mutagen._file import File as MutFile
from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.id3._frames import TXXX
from mutagen.mp4 import MP4
from mutagen.wave import WAVE


def generate_track_id(filepath: Path, artist: str = "", title: str = "") -> str:
    """
    Generate a deterministic track ID based on file path and metadata.
    
    This creates a UUID5 from the combination of:
    - Absolute file path
    - Artist + Title (if available)
    
    The same file with same metadata will always get the same ID.
    """
    # Use file path as primary identifier
    path_str = str(filepath.resolve())
    
    # Add metadata for better uniqueness
    metadata_str = f"{artist}|{title}".lower().strip()
    
    # Combine
    combined = f"{path_str}|{metadata_str}"
    
    # Generate UUID5 (deterministic, namespace-based)
    namespace = uuid.UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8')  # DNS namespace
    track_uuid = uuid.uuid5(namespace, combined)
    
    return str(track_uuid)


def write_djlib_tags(
    filepath: Path,
    track_id: str,
    rekordbox_id: Optional[str] = None,
    traktor_id: Optional[str] = None,
    original_path: Optional[str] = None,
) -> None:
    """
    Write DJLIB custom tags to audio file (ID3 TXXX for MP3, Vorbis Comments for FLAC).
    
    Args:
        filepath: Path to audio file
        track_id: Our internal track UUID
        rekordbox_id: Optional Rekordbox database ID
        traktor_id: Optional Traktor AUDIO_ID
        original_path: Optional original file path (for reference)
    
    Raises:
        ValueError: If file format is not supported
    """
    if not filepath.exists():
        raise FileNotFoundError(f"File not found: {filepath}")
    
    snapshot_date = datetime.now(timezone.utc).isoformat()
    
    # Try to detect file type
    audio = MutFile(str(filepath))
    
    if audio is None:
        raise ValueError(f"Unsupported audio format: {filepath}")
    
    # Handle MP3 (ID3 tags)
    if filepath.suffix.lower() in ('.mp3', '.mp2', '.mp1'):
        _write_id3_tags(filepath, track_id, rekordbox_id, traktor_id, original_path, snapshot_date)
    
    # Handle FLAC (Vorbis Comments)
    elif filepath.suffix.lower() == '.flac':
        _write_flac_tags(filepath, track_id, rekordbox_id, traktor_id, original_path, snapshot_date)
    
    # Handle M4A/MP4 (iTunes-style)
    elif filepath.suffix.lower() in ('.m4a', '.m4p', '.mp4'):
        _write_mp4_tags(filepath, track_id, rekordbox_id, traktor_id, original_path, snapshot_date)
    
    # Handle WAV (ID3 tags in RIFF)
    elif filepath.suffix.lower() == '.wav':
        _write_wav_tags(filepath, track_id, rekordbox_id, traktor_id, original_path, snapshot_date)
    
    # Handle AIFF (ID3 tags)
    elif filepath.suffix.lower() in ('.aif', '.aiff'):
        _write_aiff_tags(filepath, track_id, rekordbox_id, traktor_id, original_path, snapshot_date)
    
    else:
        raise ValueError(f"Unsupported audio format: {filepath.suffix}")


def _write_id3_tags(
    filepath: Path,
    track_id: str,
    rekordbox_id: Optional[str],
    traktor_id: Optional[str],
    original_path: Optional[str],
    snapshot_date: str,
) -> None:
    """Write TXXX frames to ID3 tags (MP3)."""
    try:
        audio = ID3(str(filepath))
    except:
        # No ID3 tags yet, create them
        audio = ID3()
    
    # Write custom TXXX frames
    audio.add(TXXX(encoding=3, desc='DJLIB_TRACK_ID', text=track_id))
    audio.add(TXXX(encoding=3, desc='DJLIB_SNAPSHOT_DATE', text=snapshot_date))
    
    if rekordbox_id:
        audio.add(TXXX(encoding=3, desc='DJLIB_REKORDBOX_ID', text=rekordbox_id))
    
    if traktor_id:
        audio.add(TXXX(encoding=3, desc='DJLIB_TRAKTOR_ID', text=traktor_id))
    
    if original_path:
        audio.add(TXXX(encoding=3, desc='DJLIB_ORIGINAL_PATH', text=original_path))
    
    # Save as ID3v2.4
    audio.save(str(filepath), v2_version=4)


def _write_flac_tags(
    filepath: Path,
    track_id: str,
    rekordbox_id: Optional[str],
    traktor_id: Optional[str],
    original_path: Optional[str],
    snapshot_date: str,
) -> None:
    """Write Vorbis Comments to FLAC file."""
    audio = FLAC(str(filepath))
    
    # Vorbis Comments are just key=value pairs
    audio['DJLIB_TRACK_ID'] = track_id
    audio['DJLIB_SNAPSHOT_DATE'] = snapshot_date
    
    if rekordbox_id:
        audio['DJLIB_REKORDBOX_ID'] = rekordbox_id
    
    if traktor_id:
        audio['DJLIB_TRAKTOR_ID'] = traktor_id
    
    if original_path:
        audio['DJLIB_ORIGINAL_PATH'] = original_path
    
    audio.save()


def _write_mp4_tags(
    filepath: Path,
    track_id: str,
    rekordbox_id: Optional[str],
    traktor_id: Optional[str],
    original_path: Optional[str],
    snapshot_date: str,
) -> None:
    """Write custom tags to MP4/M4A file (iTunes-style freeform)."""
    audio = MP4(str(filepath))
    
    # MP4 uses freeform identifiers (----:com.apple.iTunes:NAME)
    audio['----:com.apple.iTunes:DJLIB_TRACK_ID'] = track_id.encode('utf-8')
    audio['----:com.apple.iTunes:DJLIB_SNAPSHOT_DATE'] = snapshot_date.encode('utf-8')
    
    if rekordbox_id:
        audio['----:com.apple.iTunes:DJLIB_REKORDBOX_ID'] = rekordbox_id.encode('utf-8')
    
    if traktor_id:
        audio['----:com.apple.iTunes:DJLIB_TRAKTOR_ID'] = traktor_id.encode('utf-8')
    
    if original_path:
        audio['----:com.apple.iTunes:DJLIB_ORIGINAL_PATH'] = original_path.encode('utf-8')
    
    audio.save()


def _write_wav_tags(
    filepath: Path,
    track_id: str,
    rekordbox_id: Optional[str],
    traktor_id: Optional[str],
    original_path: Optional[str],
    snapshot_date: str,
) -> None:
    """Write ID3 tags to WAV file (stored in RIFF INFO chunk)."""
    try:
        audio = WAVE(str(filepath))
        
        # Add ID3 tags if they don't exist
        if audio.tags is None:
            audio.add_tags()
        
        # Write custom TXXX frames (same as MP3)
        audio.tags.add(TXXX(encoding=3, desc='DJLIB_TRACK_ID', text=track_id))
        audio.tags.add(TXXX(encoding=3, desc='DJLIB_SNAPSHOT_DATE', text=snapshot_date))
        
        if rekordbox_id:
            audio.tags.add(TXXX(encoding=3, desc='DJLIB_REKORDBOX_ID', text=rekordbox_id))
        
        if traktor_id:
            audio.tags.add(TXXX(encoding=3, desc='DJLIB_TRAKTOR_ID', text=traktor_id))
        
        if original_path:
            audio.tags.add(TXXX(encoding=3, desc='DJLIB_ORIGINAL_PATH', text=original_path))
        
        audio.save()
    except Exception as e:
        raise ValueError(f"Failed to write WAV tags: {e}")


def _write_aiff_tags(
    filepath: Path,
    track_id: str,
    rekordbox_id: Optional[str],
    traktor_id: Optional[str],
    original_path: Optional[str],
    snapshot_date: str,
) -> None:
    """Write ID3 tags to AIFF file."""
    try:
        audio = AIFF(str(filepath))
        
        # Add ID3 tags if they don't exist
        if audio.tags is None:
            audio.add_tags()
        
        # Write custom TXXX frames (same as MP3/WAV)
        audio.tags.add(TXXX(encoding=3, desc='DJLIB_TRACK_ID', text=track_id))
        audio.tags.add(TXXX(encoding=3, desc='DJLIB_SNAPSHOT_DATE', text=snapshot_date))
        
        if rekordbox_id:
            audio.tags.add(TXXX(encoding=3, desc='DJLIB_REKORDBOX_ID', text=rekordbox_id))
        
        if traktor_id:
            audio.tags.add(TXXX(encoding=3, desc='DJLIB_TRAKTOR_ID', text=traktor_id))
        
        if original_path:
            audio.tags.add(TXXX(encoding=3, desc='DJLIB_ORIGINAL_PATH', text=original_path))
        
        audio.save()
    except Exception as e:
        raise ValueError(f"Failed to write AIFF tags: {e}")


def read_djlib_tags(filepath: Path) -> Dict[str, str]:
    """
    Read DJLIB custom tags from audio file.
    
    Returns:
        Dict with keys: track_id, rekordbox_id, traktor_id, snapshot_date, original_path
        Missing tags will have empty string values.
    """
    if not filepath.exists():
        return {}
    
    tags: Dict[str, str] = {
        'track_id': '',
        'rekordbox_id': '',
        'traktor_id': '',
        'snapshot_date': '',
        'original_path': '',
    }
    
    try:
        # Try MP3 (ID3)
        if filepath.suffix.lower() in ('.mp3', '.mp2', '.mp1'):
            audio = ID3(str(filepath))
            for frame in audio.getall('TXXX'):
                if frame.desc.startswith('DJLIB_'):
                    key = frame.desc[6:].lower()  # Remove 'DJLIB_' prefix
                    if key in tags:
                        tags[key] = frame.text[0] if frame.text else ''
        
        # Try FLAC (Vorbis Comments)
        elif filepath.suffix.lower() == '.flac':
            audio = FLAC(str(filepath))
            for key in tags.keys():
                vorbis_key = f'DJLIB_{key.upper()}'
                if vorbis_key in audio:
                    tags[key] = audio[vorbis_key][0] if audio[vorbis_key] else ''
        
        # Try MP4/M4A
        elif filepath.suffix.lower() in ('.m4a', '.m4p', '.mp4'):
            audio = MP4(str(filepath))
            for key in tags.keys():
                mp4_key = f'----:com.apple.iTunes:DJLIB_{key.upper()}'
                if mp4_key in audio:
                    value = audio[mp4_key]
                    if value:
                        tags[key] = value[0].decode('utf-8') if isinstance(value[0], bytes) else str(value[0])
        
        # Try WAV (ID3 in RIFF)
        elif filepath.suffix.lower() == '.wav':
            audio = WAVE(str(filepath))
            if audio.tags:
                for frame in audio.tags.getall('TXXX'):
                    if frame.desc.startswith('DJLIB_'):
                        key = frame.desc[6:].lower()
                        if key in tags:
                            tags[key] = frame.text[0] if frame.text else ''
        
        # Try AIFF (ID3)
        elif filepath.suffix.lower() in ('.aif', '.aiff'):
            audio = AIFF(str(filepath))
            if audio.tags:
                for frame in audio.tags.getall('TXXX'):
                    if frame.desc.startswith('DJLIB_'):
                        key = frame.desc[6:].lower()
                        if key in tags:
                            tags[key] = frame.text[0] if frame.text else ''
    
    except Exception:
        # If reading fails, return empty tags (file might not have custom tags yet)
        pass
    
    return tags


def has_djlib_tags(filepath: Path) -> bool:
    """
    Check if file has any DJLIB custom tags.
    
    Returns:
        True if track_id tag exists, False otherwise
    """
    tags = read_djlib_tags(filepath)
    return bool(tags.get('track_id'))


def remove_djlib_tags(filepath: Path) -> None:
    """
    Remove all DJLIB custom tags from audio file.
    
    Use this for cleanup or if you want to regenerate tags.
    """
    if not filepath.exists():
        return
    
    try:
        # MP3 (ID3)
        if filepath.suffix.lower() in ('.mp3', '.mp2', '.mp1'):
            audio = ID3(str(filepath))
            # Remove all TXXX frames with DJLIB_ prefix
            to_remove = [frame for frame in audio.getall('TXXX') if frame.desc.startswith('DJLIB_')]
            for frame in to_remove:
                audio.delall(frame.HashKey)
            if to_remove:
                audio.save(str(filepath), v2_version=4)
        
        # FLAC (Vorbis Comments)
        elif filepath.suffix.lower() == '.flac':
            audio = FLAC(str(filepath))
            keys_to_remove = [k for k in audio.keys() if k.startswith('DJLIB_')]
            for key in keys_to_remove:
                del audio[key]
            if keys_to_remove:
                audio.save()
        
        # MP4/M4A
        elif filepath.suffix.lower() in ('.m4a', '.m4p', '.mp4'):
            audio = MP4(str(filepath))
            keys_to_remove = [k for k in audio.keys() if 'DJLIB_' in k]
            for key in keys_to_remove:
                del audio[key]
            if keys_to_remove:
                audio.save()
    
    except Exception:
        pass  # Ignore errors (file might not have tags)
