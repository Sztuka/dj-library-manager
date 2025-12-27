"""Album artwork fetching with multi-source fallback.

Priority chain:

For ORIGINALS (no version):
1. MusicBrainz Cover Art Archive (500px front cover, best for general music)
2. Beatport dynamic URI (1400x1400, gold standard for EDM)
3. Last.fm album.getInfo (300x300, broad coverage)
4. SoundCloud track artwork (500x500, last resort)

For REMIXES (has version):
1. Beatport dynamic URI (1400x1400, if found specific remix)
2. SoundCloud track artwork (500x500, PRIORITY for remixes - like year logic)
3. Last.fm album.getInfo (300x300, fallback)

Skips MusicBrainz for remixes (returns original cover, not remix).
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
_LAST_BEATPORT_REQUEST = 0.0
CAA_MIN_INTERVAL = 1.0  # MusicBrainz: 1 req/s
LASTFM_MIN_INTERVAL = 0.2  # Last.fm: 5 req/s
SOUNDCLOUD_MIN_INTERVAL = 0.5  # SoundCloud: 2 req/s
BEATPORT_MIN_INTERVAL = 1.0  # Beatport: 1 req/s

def has_artwork(filepath: str) -> bool:
    """Check if audio file already has cover art (supports MP3, FLAC, M4A, AIFF)."""
    from mutagen import File
    from pathlib import Path
    
    try:
        ext = Path(filepath).suffix.lower()
        audio = File(filepath)
        
        if audio is None:
            return False
        
        # MP3 (ID3 tags)
        if ext == '.mp3':
            if hasattr(audio, 'tags') and audio.tags:
                return any(str(k).startswith('APIC') for k in audio.tags.keys())
            return False
        
        # FLAC (Vorbis comments with pictures)
        if ext == '.flac':
            return bool(getattr(audio, 'pictures', None))
        
        # M4A/AAC (MP4 tags)
        if ext in ['.m4a', '.mp4', '.aac']:
            if hasattr(audio, 'tags') and audio.tags:
                return 'covr' in audio.tags
            return False
        
        # AIFF (ID3 tags like MP3)
        if ext in ['.aiff', '.aif']:
            if hasattr(audio, 'tags') and audio.tags:
                return any(str(k).startswith('APIC') for k in audio.tags.keys())
            return False
        
        return False
    except Exception:
        return False


def add_artwork(filepath: str, image_data: bytes, mime_type: str = 'image/jpeg') -> bool:
    """Add cover art to audio file. Supports MP3, FLAC, M4A, AIFF.
    
    Removes existing cover art first, then adds new one.
    Returns True if successful.
    """
    from mutagen import File
    from mutagen.flac import FLAC, Picture
    from mutagen.mp4 import MP4, MP4Cover
    from pathlib import Path
    
    try:
        ext = Path(filepath).suffix.lower()
        
        # MP3 (ID3 tags)
        if ext == '.mp3':
            audio = ID3(filepath)
            
            # Remove ALL existing APIC frames
            apic_keys = [key for key in audio.keys() if key.startswith('APIC')]
            for key in apic_keys:
                audio.delall(key)
            
            # Add new cover art
            audio.add(APIC(
                encoding=3,  # UTF-8
                mime=mime_type,
                type=3,  # Front cover
                desc='Cover',
                data=image_data
            ))
            audio.save(v2_version=3)
            return True
        
        # FLAC (Vorbis comments with pictures)
        if ext == '.flac':
            audio = FLAC(filepath)
            
            # Remove existing pictures
            audio.clear_pictures()
            
            # Add new cover art
            picture = Picture()
            picture.type = 3  # Front cover
            picture.mime = mime_type
            picture.desc = 'Cover'
            picture.data = image_data
            audio.add_picture(picture)
            audio.save()
            return True
        
        # M4A/AAC (MP4 tags)
        if ext in ['.m4a', '.mp4', '.aac']:
            audio = MP4(filepath)
            
            # Determine image format
            if 'png' in mime_type.lower():
                img_format = MP4Cover.FORMAT_PNG
            else:
                img_format = MP4Cover.FORMAT_JPEG
            
            # Add cover art
            audio['covr'] = [MP4Cover(image_data, imageformat=img_format)]
            audio.save()
            return True
        
        # AIFF (ID3 tags like MP3)
        if ext in ['.aiff', '.aif']:
            from mutagen.aiff import AIFF
            audio = AIFF(filepath)
            
            if audio.tags is None:
                audio.add_tags()
            
            # Remove existing APIC frames
            apic_keys = [key for key in audio.tags.keys() if str(key).startswith('APIC')]
            for key in apic_keys:
                del audio.tags[key]
            
            # Add new cover art
            audio.tags.add(APIC(
                encoding=3,
                mime=mime_type,
                type=3,
                desc='Cover',
                data=image_data
            ))
            audio.save()
            return True
        
        # Unsupported format
        return False
        
    except Exception as e:
        import sys
        print(f"add_artwork error for {filepath}: {e}", file=sys.stderr)
        return False

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

def get_lastfm_cover_url(artist: str, album: str, api_key: str) -> Optional[str]:
    """Get Last.fm cover art URL without downloading the image.
    
    Returns URL string or None.
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
        
        # Get largest available image URL
        images = album_data.get('image', [])
        for img in reversed(images):  # Start from largest
            if img.get('size') in ['extralarge', 'large']:
                image_url = img.get('#text')
                if image_url and not image_url.endswith('2a96cbd8b46e442fc41c2b86b821562f.png'):  # Skip Last.fm placeholder
                    return image_url
        
        return None
    except Exception:
        return None

def fetch_from_beatport(artwork_url: str) -> Optional[Tuple[bytes, str]]:
    """Fetch cover art from Beatport dynamic URI.
    
    Args:
        artwork_url: Beatport dynamic URI (e.g., "https://geo-media.beatsource.com/.../{w}x{h}/...")
    
    Returns:
        (image_data, mime_type) or None if failed
    """
    # Rate limiting
    global _LAST_BEATPORT_REQUEST
    elapsed = time.time() - _LAST_BEATPORT_REQUEST
    BEATPORT_MIN_INTERVAL = 1.0  # 1 second between requests
    if elapsed < BEATPORT_MIN_INTERVAL:
        time.sleep(BEATPORT_MIN_INTERVAL - elapsed)
    
    try:
        # Replace {w}x{h} placeholder with 1400x1400 (best quality)
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
    """Get cover art URL without downloading the image.
    
    Uses same priority logic as fetch_cover_art but only returns URL.
    
    Returns:
        URL string or None
    """
    # Archive.org (best for live recordings when available)
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

    # For ORIGINALS: try MusicBrainz first (skip for remixes)
    # Prefer release-group over specific release (more reliable for cover art)
    # If we only have release_mbid (no release_group_id), skip to Last.fm fallback
    # since many canonical releases don't have cover art
    if not version:
        if release_group_id:
            return f"https://coverartarchive.org/release-group/{release_group_id}/front-500"
        # Skip release_mbid if we have Last.fm available (more reliable than specific releases)
        # elif release_mbid:
        #     return f"https://coverartarchive.org/release/{release_mbid}/front-500"
    
    # Try Beatport (CLI verifies match before passing URL)
    if beatport_artwork_url:
        # Replace {w}x{h} placeholder with 1400x1400
        if '{w}x{h}' in beatport_artwork_url:
            return beatport_artwork_url.replace('{w}x{h}', '1400x1400')
        return beatport_artwork_url
    
    # Try Last.fm (fallback for originals when MusicBrainz has no cover)
    if not version and album and lastfm_api_key:
        try:
            lfm_url = get_lastfm_cover_url(artist, album, lastfm_api_key)
            if lfm_url:
                return lfm_url
        except Exception:
            pass
    
    # Try SoundCloud (for remixes prioritized, for originals last resort)
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
    """Fetch and add cover art to MP3 file using multi-source fallback.
    
    Priority chain:
    
    For ORIGINALS (no version):
    1. MusicBrainz Cover Art Archive (500px) - tries release first, then release-group
    2. Beatport dynamic URI (1400x1400 - highest quality for EDM)
    3. Last.fm album.getInfo (300x300)
    4. SoundCloud track artwork (500x500)
    
    For REMIXES (has version):
    1. Beatport dynamic URI (1400x1400 - if found specific remix)
    2. SoundCloud track artwork (500x500 - priority for remixes!)
    3. Last.fm album.getInfo (300x300 - fallback)
    
    Args:
        filepath: Path to MP3 file
        artist: Artist name
        album: Album name (for Last.fm)
        title: Track title (for SoundCloud)
        version: Track version/remix info (for SoundCloud)
        release_group_id: MusicBrainz release group ID (optional, ignored for remixes)
        release_mbid: MusicBrainz release ID (optional, more specific than release_group_id)
        beatport_artwork_url: Beatport dynamic URI (optional, best quality for EDM)
        lastfm_api_key: Last.fm API key (optional)
        soundcloud_client_id: SoundCloud client ID (optional)
        skip_if_exists: If True, skip files that already have artwork
        disable_beatport: If True, skip Beatport artwork fetching
        cover_art_url: Pre-fetched cover art URL (optional, if provided will be used directly)
    
    Returns:
        (success: bool, source: str) where source is 'mb', 'beatport', 'lastfm', 'soundcloud', 'exists', or 'failed'
    """
    # Check if artwork already exists
    if skip_if_exists and has_artwork(filepath):
        return (True, 'exists')
    
    # If cover_art_url was provided (pre-fetched), download directly
    if cover_art_url:
        try:
            response = requests.get(cover_art_url, timeout=10)
            if response.status_code == 200:
                mime_type = response.headers.get('Content-Type', 'image/jpeg')
                if add_artwork(filepath, response.content, mime_type):
                    # Determine source from URL
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
            pass  # Fall through to normal priority chain
    
    # For ORIGINALS: try MusicBrainz first (skip for remixes - returns original cover)
    if not version and (release_group_id or release_mbid):
        result = fetch_from_musicbrainz(release_group_id, release_mbid)
        if result:
            image_data, mime_type = result
            if add_artwork(filepath, image_data, mime_type):
                return (True, 'mb')
    
    # Try Beatport (best quality for EDM - 1400x1400)
    # CLI verifies match quality (artist+title+version) before passing artwork_url
    if not disable_beatport and beatport_artwork_url:
        result = fetch_from_beatport(beatport_artwork_url)
        if result:
            image_data, mime_type = result
            if add_artwork(filepath, image_data, mime_type):
                return (True, 'beatport')
    
    # For REMIXES: prioritize SoundCloud (like year logic - high weight for remixes)
    if version and soundcloud_client_id:
        result = fetch_from_soundcloud(artist, title, version, soundcloud_client_id)
        if result:
            image_data, mime_type = result
            if add_artwork(filepath, image_data, mime_type):
                return (True, 'soundcloud')
    
    # Try Last.fm
    if lastfm_api_key and album:
        result = fetch_from_lastfm(artist, album, lastfm_api_key)
        if result:
            image_data, mime_type = result
            if add_artwork(filepath, image_data, mime_type):
                return (True, 'lastfm')
    
    # For ORIGINALS: try SoundCloud as last resort
    if not version and soundcloud_client_id:
        result = fetch_from_soundcloud(artist, title, version, soundcloud_client_id)
        if result:
            image_data, mime_type = result
            if add_artwork(filepath, image_data, mime_type):
                return (True, 'soundcloud')
    
    return (False, 'failed')
