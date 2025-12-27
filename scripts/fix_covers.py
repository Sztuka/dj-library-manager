#!/usr/bin/env python3
"""
Fix missing cover art for files already exported to library.

Scans library for files without cover art and tries to fetch from:
1. MusicBrainz Cover Art Archive
2. Last.fm
3. SoundCloud
"""
import sys
from pathlib import Path
from mutagen import File

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from djlib.config import load_config
from djlib.metadata.coverart import has_artwork, add_artwork, fetch_from_musicbrainz, fetch_from_lastfm
from djlib.metadata import mb_client
from djlib.config import get_lastfm_api_key

AUDIO_EXTS = {'.mp3', '.flac', '.m4a', '.aiff', '.aif'}


def parse_filename(name: str) -> tuple[str, str]:
    """Extract artist and title from filename like 'Artist - Title [Key BPM].ext'"""
    stem = Path(name).stem
    # Remove [Key BPM] suffix
    import re
    stem = re.sub(r'\s*\[[^\]]+\]\s*$', '', stem)
    
    if ' - ' in stem:
        parts = stem.split(' - ', 1)
        return parts[0].strip(), parts[1].strip()
    return '', stem


def fix_cover_art(filepath: Path, dry_run: bool = False) -> tuple[bool, str]:
    """Try to add cover art to file if missing."""
    if has_artwork(str(filepath)):
        return True, 'exists'
    
    artist, title = parse_filename(filepath.name)
    if not artist or not title:
        return False, 'parse_failed'
    
    print(f"  Processing: {artist} - {title}")
    
    # Try MusicBrainz
    try:
        # Search for recording to get release_group_id
        match = mb_client.search_recording(artist, title)
        if match and match.release_group_id:
            result = fetch_from_musicbrainz(match.release_group_id, None)
            if result:
                image_data, mime_type = result
                if not dry_run:
                    if add_artwork(str(filepath), image_data, mime_type):
                        return True, 'musicbrainz'
                else:
                    return True, 'musicbrainz (dry-run)'
    except Exception as e:
        print(f"    MB error: {e}")
    
    # Try Last.fm
    lastfm_key = get_lastfm_api_key()
    if lastfm_key:
        try:
            # Get album name from Last.fm
            from djlib.metadata.lastfm import track_info
            info = track_info(artist, title)
            album = info.get('album', '')
            
            if album:
                result = fetch_from_lastfm(artist, album, lastfm_key)
                if result:
                    image_data, mime_type = result
                    if not dry_run:
                        if add_artwork(str(filepath), image_data, mime_type):
                            return True, 'lastfm'
                    else:
                        return True, 'lastfm (dry-run)'
        except Exception as e:
            print(f"    Last.fm error: {e}")
    
    return False, 'not_found'


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Fix missing cover art in library')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done')
    parser.add_argument('--artist', type=str, help='Only process specific artist folder')
    args = parser.parse_args()
    
    config = load_config()
    library_root = Path(config.get('library_root', ''))
    
    if not library_root.exists():
        print(f"Library not found: {library_root}")
        sys.exit(1)
    
    print(f"Scanning: {library_root}")
    print(f"Dry run: {args.dry_run}\n")
    
    fixed = 0
    failed = 0
    skipped = 0
    
    for artist_dir in sorted(library_root.iterdir()):
        if not artist_dir.is_dir():
            continue
        if args.artist and args.artist.lower() not in artist_dir.name.lower():
            continue
        
        print(f"\n📁 {artist_dir.name}")
        
        for audio_file in artist_dir.glob('*'):
            if audio_file.suffix.lower() not in AUDIO_EXTS:
                continue
            
            success, source = fix_cover_art(audio_file, dry_run=args.dry_run)
            
            if source == 'exists':
                skipped += 1
            elif success:
                fixed += 1
                print(f"    ✅ Added from {source}")
            else:
                failed += 1
                print(f"    ❌ {source}")
    
    print(f"\n{'='*50}")
    print(f"Fixed: {fixed}, Failed: {failed}, Already had cover: {skipped}")


if __name__ == '__main__':
    main()
