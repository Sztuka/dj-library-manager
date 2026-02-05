"""Cover art embedding for audio files.

This module provides functions to check and embed cover art into audio files.
Supports MP3, FLAC, M4A/AAC, and AIFF formats.

Note: Online cover art fetching has been moved to djlib/legacy/coverart_fetch.py
We now use a standard DJ Library cover art embedded from a local file.
"""
from __future__ import annotations
from pathlib import Path

from mutagen.id3 import ID3
from mutagen.id3._frames import APIC


def has_artwork(filepath: str) -> bool:
    """Check if audio file already has cover art.
    
    Supports MP3, FLAC, M4A/AAC, AIFF formats.
    
    Args:
        filepath: Path to the audio file
        
    Returns:
        True if file has embedded cover art, False otherwise
    """
    from mutagen import File
    
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
    """Add cover art to audio file.
    
    Removes existing cover art first, then adds new one.
    Supports MP3, FLAC, M4A/AAC, AIFF formats.
    
    Args:
        filepath: Path to the audio file
        image_data: Raw bytes of the image
        mime_type: MIME type of the image (default: image/jpeg)
        
    Returns:
        True if successful, False otherwise
    """
    from mutagen import File
    from mutagen.flac import FLAC, Picture
    from mutagen.mp4 import MP4, MP4Cover
    
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
            
            assert audio.tags is not None, "Failed to add tags to AIFF file"
            
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


def embed_cover_art_from_file(audio_filepath: str, cover_filepath: str) -> bool:
    """Embed cover art from a local image file into an audio file.
    
    This is the main function used by the apply workflow to embed
    the standard DJ Library cover art.
    
    Args:
        audio_filepath: Path to the audio file (MP3, FLAC, M4A, AIFF)
        cover_filepath: Path to the cover image file (JPG, PNG)
        
    Returns:
        True if successful, False otherwise
    """
    cover_path = Path(cover_filepath)
    if not cover_path.exists():
        return False
    
    # Read image data
    try:
        with open(cover_path, 'rb') as f:
            image_data = f.read()
    except Exception:
        return False
    
    # Determine MIME type
    ext = cover_path.suffix.lower()
    if ext in ('.jpg', '.jpeg'):
        mime_type = 'image/jpeg'
    elif ext == '.png':
        mime_type = 'image/png'
    else:
        mime_type = 'image/jpeg'  # Default to JPEG
    
    return add_artwork(audio_filepath, image_data, mime_type)
