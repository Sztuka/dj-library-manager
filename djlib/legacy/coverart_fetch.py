"""
LEGACY: Online cover art fetching with multi-source fallback.

This module was used for fetching album artwork from various online sources.
It has been moved to legacy/ because we now use a standard DJ Library cover art
that is embedded from a local file during the apply workflow.

The code is preserved here for potential future use if we decide to add
online cover art fetching as an optional feature.

Original priority chain:

For ORIGINALS (no version):
1. MusicBrainz Cover Art Archive (500px front cover, best for general music)
2. Beatport dynamic URI (1400x1400, gold standard for EDM)
3. Last.fm album.getInfo (300x300, broad coverage)
4. SoundCloud track artwork (500x500, last resort)

For REMIXES (has version):
1. Beatport dynamic URI (1400x1400, if found specific remix)
2. SoundCloud track artwork (500x500, PRIORITY for remixes - like year logic)
3. Last.fm album.getInfo (300x300, fallback)
"""
from __future__ import annotations
from typing import Optional, Tuple
import requests
import time

# Rate limiting
_LAST_CAA_REQUEST = 0.0
_LAST_LASTFM_REQUEST = 0.0
_LAST_SOUNDCLOUD_REQUEST = 0.0
_LAST_BEATPORT_REQUEST = 0.0
CAA_MIN_INTERVAL = 1.0  # MusicBrainz: 1 req/s
LASTFM_MIN_INTERVAL = 0.2  # Last.fm: 5 req/s
SOUNDCLOUD_MIN_INTERVAL = 0.5  # SoundCloud: 2 req/s
BEATPORT_MIN_INTERVAL = 1.0  # Beatport: 1 req/s


def fetch_from_musicbrainz(release_group_id: str, release_mbid: Optional[str] = None) -> Optional[Tuple[bytes, str]]:
    """Fetch cover art from MusicBrainz Cover Art Archive.
    
    Tries in order:
    1. Specific release MBID (if provided) - most accurate
    2. Release-group MBID (fallback) - may be generic
    
    Returns (image_data, mime_type) or None.
    Uses direct CAA API for 500px front cover.
    """
    global _LAST_CAA_REQUEST
    
    if not release_group_id and not release_mbid:
        return None
    
    urls_to_try = []
    
    # Try release first (specific album edition)
    if release_mbid:
        urls_to_try.append(f"https://coverartarchive.org/release/{release_mbid}/front-500")
    
    # Then fallback to release-group (generic for all editions)
    if release_group_id:
        urls_to_try.append(f"https://coverartarchive.org/release-group/{release_group_id}/front-500")
    
    for url in urls_to_try:
        # Rate limiting
        elapsed = time.time() - _LAST_CAA_REQUEST
        if elapsed < CAA_MIN_INTERVAL:
            time.sleep(CAA_MIN_INTERVAL - elapsed)
        
        try:
            _LAST_CAA_REQUEST = time.time()
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                mime_type = response.headers.get('Content-Type', 'image/jpeg')
                return (response.content, mime_type)
        except Exception:
            continue
    
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
        
        images = album_data.get('image', [])
        for img in reversed(images):
            if img.get('size') in ['extralarge', 'large']:
                image_url = img.get('#text')
                if image_url and not image_url.endswith('2a96cbd8b46e442fc41c2b86b821562f.png'):
                    time.sleep(0.1)
                    img_response = requests.get(image_url, timeout=10)
                    if img_response.status_code == 200:
                        mime_type = img_response.headers.get('Content-Type', 'image/jpeg')
                        return (img_response.content, mime_type)
        
        return None
    except Exception:
        return None


def get_lastfm_cover_url(artist: str, album: str, api_key: str) -> Optional[str]:
    """Get Last.fm cover art URL without downloading the image."""
    global _LAST_LASTFM_REQUEST
    
    if not artist or not album or not api_key:
        return None
    
    elapsed = time.time() - _LAST_LASTFM_REQUEST
    if elapsed < LASTFM_MIN_INTERVAL:
        time.sleep(LASTFM_MIN_INTERVAL - elapsed)
    
    try:
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
        
        images = album_data.get('image', [])
        for img in reversed(images):
            if img.get('size') in ['extralarge', 'large']:
                image_url = img.get('#text')
                if image_url and not image_url.endswith('2a96cbd8b46e442fc41c2b86b821562f.png'):
                    return image_url
        
        return None
    except Exception:
        return None


def fetch_from_beatport(artwork_url: str) -> Optional[Tuple[bytes, str]]:
    """Fetch cover art from Beatport dynamic URI."""
    global _LAST_BEATPORT_REQUEST
    elapsed = time.time() - _LAST_BEATPORT_REQUEST
    if elapsed < BEATPORT_MIN_INTERVAL:
        time.sleep(BEATPORT_MIN_INTERVAL - elapsed)
    
    try:
        if '{w}x{h}' in artwork_url:
            artwork_url = artwork_url.replace('{w}x{h}', '1400x1400')
        
        _LAST_BEATPORT_REQUEST = time.time()
        response = requests.get(artwork_url, timeout=10)
        
        if response.status_code == 200:
            mime_type = response.headers.get('Content-Type', 'image/jpeg')
            return (response.content, mime_type)
        
        return None
    except Exception:
        return None


def fetch_from_soundcloud(artist: str, title: str, version: str, client_id: str) -> Optional[Tuple[bytes, str]]:
    """Fetch cover art from SoundCloud track search."""
    global _LAST_SOUNDCLOUD_REQUEST
    
    if not artist or not title or not client_id:
        return None
    
    elapsed = time.time() - _LAST_SOUNDCLOUD_REQUEST
    if elapsed < SOUNDCLOUD_MIN_INTERVAL:
        time.sleep(SOUNDCLOUD_MIN_INTERVAL - elapsed)
    
    try:
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
                artwork_url = artwork_url.replace('-large.', '-t500x500.')
                time.sleep(0.1)
                img_response = requests.get(artwork_url, timeout=10)
                if img_response.status_code == 200:
                    mime_type = img_response.headers.get('Content-Type', 'image/jpeg')
                    return (img_response.content, mime_type)
        
        return None
    except Exception:
        return None


def get_cover_art_url(
    artist: str,
    title: str,
    version: str = "",
    album: str = "",
    release_group_id: Optional[str] = None,
    release_mbid: Optional[str] = None,
    archive_org_identifier: Optional[str] = None,
    archive_org_cover_url: Optional[str] = None,
    beatport_artwork_url: Optional[str] = None,
    soundcloud_client_id: Optional[str] = None,
    lastfm_api_key: Optional[str] = None,
) -> Optional[str]:
    """Get cover art URL without downloading the image."""
    # Archive.org
    if archive_org_cover_url:
        return archive_org_cover_url
    if archive_org_identifier:
        try:
            from djlib.metadata import archive_org
            url = archive_org.get_cover_url(archive_org_identifier)
            if url:
                return url
        except Exception:
            pass

    # For ORIGINALS: try MusicBrainz first
    if not version:
        if release_group_id:
            return f"https://coverartarchive.org/release-group/{release_group_id}/front-500"
    
    # Beatport
    if beatport_artwork_url:
        if '{w}x{h}' in beatport_artwork_url:
            return beatport_artwork_url.replace('{w}x{h}', '1400x1400')
        return beatport_artwork_url
    
    # Last.fm
    if not version and album and lastfm_api_key:
        try:
            lfm_url = get_lastfm_cover_url(artist, album, lastfm_api_key)
            if lfm_url:
                return lfm_url
        except Exception:
            pass
    
    # SoundCloud
    if soundcloud_client_id:
        try:
            from djlib.metadata.soundcloud import get_track_artwork_url
            sc_url = get_track_artwork_url(artist, title, version, soundcloud_client_id)
            if sc_url:
                return sc_url
        except Exception:
            pass
    
    return None


def fetch_cover_art(
    filepath: str,
    artist: str,
    album: str,
    title: str,
    version: str = "",
    release_group_id: Optional[str] = None,
    release_mbid: Optional[str] = None,
    beatport_artwork_url: Optional[str] = None,
    lastfm_api_key: Optional[str] = None,
    soundcloud_client_id: Optional[str] = None,
    skip_if_exists: bool = True,
    disable_beatport: bool = False,
    cover_art_url: Optional[str] = None
) -> Tuple[bool, str]:
    """Fetch and add cover art to audio file using multi-source fallback.
    
    Returns:
        (success: bool, source: str) where source is 'mb', 'beatport', 'lastfm', 'soundcloud', 'exists', or 'failed'
    """
    from djlib.metadata.coverart import has_artwork, add_artwork
    
    if skip_if_exists and has_artwork(filepath):
        return (True, 'exists')
    
    # If cover_art_url was provided, download directly
    if cover_art_url:
        try:
            response = requests.get(cover_art_url, timeout=10)
            if response.status_code == 200:
                mime_type = response.headers.get('Content-Type', 'image/jpeg')
                if add_artwork(filepath, response.content, mime_type):
                    if 'coverartarchive.org' in cover_art_url:
                        return (True, 'mb')
                    elif 'sndcdn.com' in cover_art_url:
                        return (True, 'soundcloud')
                    elif 'beatsource.com' in cover_art_url or 'beatport' in cover_art_url:
                        return (True, 'beatport')
                    elif 'lastfm' in cover_art_url:
                        return (True, 'lastfm')
                    else:
                        return (True, 'direct_url')
        except Exception:
            pass
    
    # For ORIGINALS: try MusicBrainz first
    if not version and (release_group_id or release_mbid):
        result = fetch_from_musicbrainz(release_group_id, release_mbid)
        if result:
            image_data, mime_type = result
            if add_artwork(filepath, image_data, mime_type):
                return (True, 'mb')
    
    # Beatport
    if not disable_beatport and beatport_artwork_url:
        result = fetch_from_beatport(beatport_artwork_url)
        if result:
            image_data, mime_type = result
            if add_artwork(filepath, image_data, mime_type):
                return (True, 'beatport')
    
    # For REMIXES: prioritize SoundCloud
    if version and soundcloud_client_id:
        result = fetch_from_soundcloud(artist, title, version, soundcloud_client_id)
        if result:
            image_data, mime_type = result
            if add_artwork(filepath, image_data, mime_type):
                return (True, 'soundcloud')
    
    # Last.fm
    if lastfm_api_key and album:
        result = fetch_from_lastfm(artist, album, lastfm_api_key)
        if result:
            image_data, mime_type = result
            if add_artwork(filepath, image_data, mime_type):
                return (True, 'lastfm')
    
    # For ORIGINALS: SoundCloud as last resort
    if not version and soundcloud_client_id:
        result = fetch_from_soundcloud(artist, title, version, soundcloud_client_id)
        if result:
            image_data, mime_type = result
            if add_artwork(filepath, image_data, mime_type):
                return (True, 'soundcloud')
    
    return (False, 'failed')
