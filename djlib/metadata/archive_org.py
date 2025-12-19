"""
Archive.org Internet Archive client for live concert recordings.

Uses Archive.org Metadata API to:
- Search for live concert recordings by artist
- Get cover art for live albums
- Match recordings by duration
"""
from __future__ import annotations
import logging
from typing import Optional, Dict, Any, List
import requests
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ARCHIVE_API_BASE = "https://archive.org"
SEARCH_API = f"{ARCHIVE_API_BASE}/advancedsearch.php"
METADATA_API = f"{ARCHIVE_API_BASE}/metadata"


@dataclass
class ArchiveTrack:
    """Single track from Archive.org recording."""
    title: str
    artist: str
    album: str
    track_number: Optional[int]
    duration_seconds: float
    identifier: str  # Archive.org identifier
    file_name: str


@dataclass
class ArchiveRecording:
    """Live concert recording from Archive.org."""
    identifier: str
    title: str
    creator: str
    date: Optional[str]
    year: Optional[int]
    cover_url: Optional[str]
    tracks: List[ArchiveTrack]


def get_metadata(identifier: str) -> Optional[Dict[str, Any]]:
    """
    Get metadata for Archive.org item.
    
    Args:
        identifier: Archive.org identifier (e.g., 'brucespringsteenbornintheusalivelondon2013')
    
    Returns:
        Metadata dict or None if not found
    """
    try:
        url = f"{METADATA_API}/{identifier}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.debug(f"Failed to get Archive.org metadata for {identifier}: {e}")
        return None


def get_cover_url(identifier: str) -> Optional[str]:
    """
    Get cover art URL for Archive.org item.
    
    Args:
        identifier: Archive.org identifier
    
    Returns:
        Cover art URL or None if not found
    """
    metadata = get_metadata(identifier)
    if not metadata:
        return None
    
    # Check for cover image in files
    # Priority: Cover.jpg > *_thumb.jpg > __ia_thumb.jpg
    files = metadata.get('files', [])
    
    # First pass: look for exact "Cover.jpg" or "cover.jpg" (main album art)
    for file_info in files:
        name = file_info.get('name', '')
        fmt = file_info.get('format', '')
        # Exact match for cover art file (not audio files with "Cover" in title)
        if name.lower() in ('cover.jpg', 'cover.png', 'folder.jpg', 'folder.png'):
            return f"{ARCHIVE_API_BASE}/download/{identifier}/{name}"
    
    # Second pass: look for thumbnail versions
    for file_info in files:
        name = file_info.get('name', '')
        fmt = file_info.get('format', '')
        # Cover thumbnail (usually smaller but guaranteed to be album art)
        if name.lower() == 'cover_thumb.jpg' or fmt == 'JPEG Thumb':
            return f"{ARCHIVE_API_BASE}/download/{identifier}/{name}"
    
    # Third pass: Archive.org auto-generated thumbnail
    for file_info in files:
        name = file_info.get('name', '')
        if name == '__ia_thumb.jpg':
            return f"{ARCHIVE_API_BASE}/download/{identifier}/{name}"
    
    # Check metadata for image
    misc = metadata.get('misc', {})
    if 'image' in misc:
        return misc['image']
    
    # Fallback to services thumbnail URL
    server = metadata.get('server', '')
    dir_path = metadata.get('dir', '')
    if server and dir_path:
        # Archive.org services API for thumbnails
        return f"https://{server}{dir_path}/__ia_thumb.jpg"
    
    return None


def parse_recording(metadata: Dict[str, Any]) -> Optional[ArchiveRecording]:
    """
    Parse Archive.org metadata into ArchiveRecording.
    
    Args:
        metadata: Raw metadata from Archive.org API
    
    Returns:
        ArchiveRecording or None if parsing failed
    """
    try:
        identifier = metadata.get('metadata', {}).get('identifier', '')
        if not identifier:
            return None
        
        meta = metadata.get('metadata', {})
        title = meta.get('title', '')
        creator = meta.get('creator', '')
        date_str = meta.get('date', '')
        year = None
        if date_str and len(date_str) >= 4:
            try:
                year = int(date_str[:4])
            except ValueError:
                pass
        
        cover_url = get_cover_url(identifier)
        
        # Parse tracks from files
        tracks = []
        files = metadata.get('files', [])
        for file_info in files:
            name = file_info.get('name', '')
            # Only process audio files
            if not name.endswith(('.mp3', '.flac', '.ogg', '.m4a')):
                continue
            
            # Get track metadata
            track_title = file_info.get('title', '')
            track_artist = file_info.get('creator', creator)
            track_album = file_info.get('album', title)
            track_num = file_info.get('track')
            if track_num:
                try:
                    track_num = int(track_num.split('/')[0])  # Handle "1/12" format
                except (ValueError, AttributeError):
                    track_num = None
            
            # Get duration
            length_str = file_info.get('length', '0')
            try:
                duration = float(length_str)
            except (ValueError, TypeError):
                duration = 0.0
            
            if track_title and duration > 0:
                track = ArchiveTrack(
                    title=track_title,
                    artist=track_artist,
                    album=track_album,
                    track_number=track_num,
                    duration_seconds=duration,
                    identifier=identifier,
                    file_name=name
                )
                tracks.append(track)
        
        return ArchiveRecording(
            identifier=identifier,
            title=title,
            creator=creator,
            date=date_str,
            year=year,
            cover_url=cover_url,
            tracks=tracks
        )
    except Exception as e:
        logger.error(f"Failed to parse Archive.org recording: {e}")
        return None


def search_by_artist_title_duration(artist: str, title: str, duration_seconds: float, tolerance_seconds: float = 1.0) -> Optional[ArchiveRecording]:
    """
    Search Archive.org for live recording by matching artist + title + duration.
    
    Strategy:
    1. Search Archive.org by creator (artist)
    2. For each concert, check if any track matches title + duration (±tolerance)
    3. Prefer recordings with "live" or "concert" in title
    4. Return first match with cover art
    
    Args:
        artist: Artist name
        title: Track title
        duration_seconds: Track duration in seconds
        tolerance_seconds: Duration matching tolerance (default ±1s)
    
    Returns:
        Best matching ArchiveRecording or None
    """
    try:
        # Search Archive.org for concerts by this artist
        # Use Advanced Search API: creator + mediatype:audio
        params = {
            'q': f'creator:"{artist}" AND mediatype:audio',
            'fl[]': ['identifier', 'title', 'creator', 'date'],
            'rows': 50,  # Check first 50 concerts
            'output': 'json',
        }
        
        response = requests.get(SEARCH_API, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        docs = data.get('response', {}).get('docs', [])
        logger.debug(f"Found {len(docs)} Archive.org items for artist '{artist}'")
        
        # Track best match (prefer live recordings)
        best_match = None
        best_is_live = False
        
        # For each concert, check tracks for title + duration match
        for doc in docs:
            identifier = doc.get('identifier')
            if not identifier:
                continue
            
            doc_title = doc.get('title', '').lower()
            is_live = any(keyword in doc_title for keyword in ['live', 'concert', 'in concert', 'unplugged'])
            
            # Get full metadata with track list
            recording = get_recording_by_identifier(identifier)
            if not recording or not recording.tracks:
                continue
            
            # Check each track for title + duration match
            for track in recording.tracks:
                # Normalize titles for comparison (remove punctuation)
                import re
                track_title_norm = re.sub(r'[^\w\s]', '', track.title.lower().strip())
                search_title_norm = re.sub(r'[^\w\s]', '', title.lower().strip())
                
                # Check if titles match (contains or exact)
                title_match = (
                    search_title_norm in track_title_norm or 
                    track_title_norm in search_title_norm
                )
                
                # Check duration match (±tolerance)
                duration_match = abs(track.duration_seconds - duration_seconds) <= tolerance_seconds
                
                if title_match and duration_match:
                    logger.info(f"✓ Archive.org match: {recording.title} - {track.title} ({track.duration_seconds}s)")
                    
                    # If this is a live recording and we don't have one yet, use it
                    if is_live and not best_is_live:
                        best_match = recording
                        best_is_live = True
                        logger.debug(f"  → Preferring live recording: {recording.title}")
                    # If we don't have any match yet, use this
                    elif not best_match:
                        best_match = recording
                    
                    # Stop after finding a live match
                    if best_is_live:
                        return best_match
        
        if best_match:
            return best_match
        
        logger.debug(f"No Archive.org match found for {artist} - {title} ({duration_seconds}s)")
        return None
        
    except Exception as e:
        logger.debug(f"Archive.org search failed: {e}")
        return None


def get_recording_by_identifier(identifier: str) -> Optional[ArchiveRecording]:
    """
    Get Archive.org recording by identifier.
    
    Args:
        identifier: Archive.org identifier
    
    Returns:
        ArchiveRecording or None
    """
    metadata = get_metadata(identifier)
    if not metadata:
        return None
    
    return parse_recording(metadata)
