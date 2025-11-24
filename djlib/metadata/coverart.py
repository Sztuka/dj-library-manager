"""Album artwork fetching with multi-source fallback.

Priority chain:
1. MusicBrainz Cover Art Archive (highest quality, 500px front cover)
2. Last.fm album.getInfo (medium quality, 300x300)
3. SoundCloud track artwork (for niche/unreleased tracks, up to 500x500)

Only adds artwork if APIC frame is missing - never overwrites existing covers.
"""
from __future__ import annotations
from typing import Optional, Tuple
import requests
import time
from pathlib import Path
from mutagen.id3 import ID3
from mutagen.id3._frames import APIC

# Rate limiting
_LAST_CAA_REQUEST = 0.0
_LAST_LASTFM_REQUEST = 0.0
_LAST_SOUNDCLOUD_REQUEST = 0.0
CAA_MIN_INTERVAL = 1.0  # MusicBrainz: 1 req/s
LASTFM_MIN_INTERVAL = 0.2  # Last.fm: 5 req/s
SOUNDCLOUD_MIN_INTERVAL = 0.5  # SoundCloud: 2 req/s

def has_artwork(filepath: str) -> bool:
    """Check if MP3 file already has APIC frame (album artwork)."""
    try:
        audio = ID3(filepath)
        return any(frame.startswith('APIC') for frame in audio.keys())
    except Exception:
        return False

def add_artwork(filepath: str, image_data: bytes, mime_type: str = 'image/jpeg') -> bool:
    """Add APIC frame to MP3 file. Returns True if successful."""
    try:
        audio = ID3(filepath)
        audio.add(APIC(
            encoding=3,  # UTF-8
            mime=mime_type,
            type=3,  # Front cover
            desc='Cover',
            data=image_data
        ))
        audio.save(v2_version=3)
        return True
    except Exception:
        return False

def fetch_from_musicbrainz(release_group_id: str) -> Optional[Tuple[bytes, str]]:
    """Fetch cover art from MusicBrainz Cover Art Archive.
    
    Returns (image_data, mime_type) or None.
    Uses direct CAA API for 500px front cover.
    """
    global _LAST_CAA_REQUEST
    
    if not release_group_id:
        return None
    
    # Rate limiting
    elapsed = time.time() - _LAST_CAA_REQUEST
    if elapsed < CAA_MIN_INTERVAL:
        time.sleep(CAA_MIN_INTERVAL - elapsed)
    
    try:
        # Cover Art Archive API: https://coverartarchive.org/
        # Format: https://coverartarchive.org/release-group/{mbid}/front-500
        url = f"https://coverartarchive.org/release-group/{release_group_id}/front-500"
        
        _LAST_CAA_REQUEST = time.time()
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            mime_type = response.headers.get('Content-Type', 'image/jpeg')
            return (response.content, mime_type)
        
        return None
    except Exception:
        return None

def fetch_from_lastfm(artist: str, album: str, api_key: str) -> Optional[Tuple[bytes, str]]:
    """Fetch cover art from Last.fm album.getInfo API.
    
    Returns (image_data, mime_type) or None.
    Uses 'extralarge' size (300x300).
    """
    global _LAST_LASTFM_REQUEST
    
    if not artist or not album or not api_key:
        return None
    
    # Rate limiting
    elapsed = time.time() - _LAST_LASTFM_REQUEST
    if elapsed < LASTFM_MIN_INTERVAL:
        time.sleep(LASTFM_MIN_INTERVAL - elapsed)
    
    try:
        # Last.fm API: album.getInfo
        url = "https://ws.audioscrobbler.com/2.0/"
        params = {
            'method': 'album.getInfo',
            'artist': artist,
            'album': album,
            'api_key': api_key,
            'format': 'json'
        }
        
        _LAST_LASTFM_REQUEST = time.time()
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code != 200:
            return None
        
        data = response.json()
        album_data = data.get('album')
        if not album_data:
            return None
        
        # Get largest available image
        images = album_data.get('image', [])
        for img in reversed(images):  # Start from largest
            if img.get('size') in ['extralarge', 'large']:
                image_url = img.get('#text')
                if image_url and not image_url.endswith('2a96cbd8b46e442fc41c2b86b821562f.png'):  # Skip Last.fm placeholder
                    # Download image
                    time.sleep(0.1)  # Brief pause before image download
                    img_response = requests.get(image_url, timeout=10)
                    if img_response.status_code == 200:
                        mime_type = img_response.headers.get('Content-Type', 'image/jpeg')
                        return (img_response.content, mime_type)
        
        return None
    except Exception:
        return None

def fetch_from_soundcloud(artist: str, title: str, version: str, client_id: str) -> Optional[Tuple[bytes, str]]:
    """Fetch cover art from SoundCloud track search.
    
    Returns (image_data, mime_type) or None.
    Uses artwork_url from track object (up to 500x500).
    """
    global _LAST_SOUNDCLOUD_REQUEST
    
    if not artist or not title or not client_id:
        return None
    
    # Rate limiting
    elapsed = time.time() - _LAST_SOUNDCLOUD_REQUEST
    if elapsed < SOUNDCLOUD_MIN_INTERVAL:
        time.sleep(SOUNDCLOUD_MIN_INTERVAL - elapsed)
    
    try:
        # SoundCloud API v2: search tracks
        query = f"{artist} {title}"
        if version:
            query += f" {version}"
        
        url = "https://api-v2.soundcloud.com/search/tracks"
        params = {
            'q': query,
            'client_id': client_id,
            'limit': 3
        }
        
        _LAST_SOUNDCLOUD_REQUEST = time.time()
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code != 200:
            return None
        
        data = response.json()
        collection = data.get('collection', [])
        
        for track in collection:
            artwork_url = track.get('artwork_url')
            if artwork_url:
                # Replace 'large' (100x100) with 't500x500' for better quality
                artwork_url = artwork_url.replace('-large.', '-t500x500.')
                
                # Download image
                time.sleep(0.1)  # Brief pause before image download
                img_response = requests.get(artwork_url, timeout=10)
                if img_response.status_code == 200:
                    mime_type = img_response.headers.get('Content-Type', 'image/jpeg')
                    return (img_response.content, mime_type)
        
        return None
    except Exception:
        return None

def fetch_cover_art(
    filepath: str,
    artist: str,
    album: str,
    title: str,
    version: str = "",
    release_group_id: Optional[str] = None,
    lastfm_api_key: Optional[str] = None,
    soundcloud_client_id: Optional[str] = None,
    skip_if_exists: bool = True
) -> Tuple[bool, str]:
    """Fetch and add cover art to MP3 file using multi-source fallback.
    
    Args:
        filepath: Path to MP3 file
        artist: Artist name
        album: Album name (for Last.fm)
        title: Track title (for SoundCloud)
        version: Track version/remix info (for SoundCloud)
        release_group_id: MusicBrainz release group ID (optional)
        lastfm_api_key: Last.fm API key (optional)
        soundcloud_client_id: SoundCloud client ID (optional)
        skip_if_exists: If True, skip files that already have artwork
    
    Returns:
        (success: bool, source: str) where source is 'mb', 'lastfm', 'soundcloud', 'exists', or 'failed'
    """
    # Check if artwork already exists
    if skip_if_exists and has_artwork(filepath):
        return (True, 'exists')
    
    # Try MusicBrainz Cover Art Archive
    if release_group_id:
        result = fetch_from_musicbrainz(release_group_id)
        if result:
            image_data, mime_type = result
            if add_artwork(filepath, image_data, mime_type):
                return (True, 'mb')
    
    # Try Last.fm
    if lastfm_api_key and album:
        result = fetch_from_lastfm(artist, album, lastfm_api_key)
        if result:
            image_data, mime_type = result
            if add_artwork(filepath, image_data, mime_type):
                return (True, 'lastfm')
    
    # Try SoundCloud
    if soundcloud_client_id:
        result = fetch_from_soundcloud(artist, title, version, soundcloud_client_id)
        if result:
            image_data, mime_type = result
            if add_artwork(filepath, image_data, mime_type):
                return (True, 'soundcloud')
    
    return (False, 'failed')
