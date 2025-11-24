"""
Tag Cleaner - removes spam/unwanted metadata from audio files.

Addresses piracy metadata (chomikuj.pl, mp3baza, musicdjs.club, etc.)
and other unnecessary tags (Serato/Traktor binary, album art, comments).
"""

from pathlib import Path
from typing import List, Set
import logging

logger = logging.getLogger(__name__)

# Tags to KEEP (whitelist approach)
ESSENTIAL_TAGS = {
    # Basic metadata
    'TIT2',  # Title
    'TPE1',  # Artist
    'TPE2',  # Album Artist
    'TALB',  # Album
    'TRCK',  # Track number
    'TCON',  # Genre
    'TDRC',  # Date/Year
    'TLEN',  # Length
    
    # DJ-critical tags
    'TBPM',  # BPM
    'TKEY',  # Key
    'POPM',  # Rating/Stars (Traktor/Rekordbox/WMP)
    
    # DJ software data (CUE POINTS, LOOPS, BEATGRIDS)
    'PRIV',  # Traktor/Serato private data - KEEP ALL!
    'GEOB',  # Serato binary data - KEEP ALL!
    
    # Visual
    'APIC',  # Album art - useful in Rekordbox
}

# Spam/piracy indicators to check in tag VALUES
SPAM_KEYWORDS = [
    'chomikuj.pl',
    'mp3baza',
    'mp3base',
    'musicdjs.club',
    'ulub.pl',
    'www.mp3',
    'www.p2p',
    'downloaded',
    'torrent',
    'ripped by',
    'uploaded by',
]

# Tags to REMOVE (spam/useless only)
SPAM_TAGS = {
    'COMM',  # Comments (ONLY if contains spam URLs) - handled in logic
    'TPUB',  # Publisher (ONLY if spam site) - handled in logic
    'MCDI',  # CD table of contents (useless)
    'USLT',  # Unsynchronized lyrics (often spam)
    'SYLT',  # Synchronized lyrics
    'WCOM',  # Commercial URL
    'WOAF',  # Official audio file URL
    'WOAS',  # Official artist URL
    'WORS',  # Official internet radio URL
    'WPAY',  # Payment URL
    'WPUB',  # Publishers official webpage
    'TPOS',  # Disc number (not needed for DJ)
    'TSST',  # Set subtitle (not needed)
}

# DJ-specific tags to KEEP (even if TXXX/custom)
DJ_SOFTWARE_TAGS = [
    'SERATO',
    'TRAKTOR',
    'REKORDBOX',
    'MIXXX',
    'DJUCED',
]


def clean_tags(filepath: Path, dry_run: bool = False) -> dict:
    """
    Remove spam/unwanted tags from audio file.
    
    Args:
        filepath: Path to audio file
        dry_run: If True, only report what would be removed
        
    Returns:
        dict with 'removed_tags', 'kept_tags', 'errors'
    """
    try:
        from mutagen.id3 import ID3
    except ImportError:
        return {'error': 'mutagen not installed'}
    
    result = {
        'removed_tags': [],
        'kept_tags': [],
        'errors': []
    }
    
    try:
        tags = ID3(filepath)
    except Exception as e:
        result['errors'].append(f"Failed to load: {e}")
        return result
    
    tags_to_delete = []
    
    for tag_key in tags.keys():
        # Extract base tag name (e.g., 'COMM:ID3v1 Comment:eng' -> 'COMM')
        base_tag = tag_key.split(':')[0]
        tag_value = str(tags[tag_key]).lower()
        
        # Check if tag should be removed
        should_remove = False
        reason = None
        
        # Special case: TXXX (user-defined) - keep if DJ software related
        if base_tag == 'TXXX':
            is_dj_tag = any(dj_sw in tag_key.upper() for dj_sw in DJ_SOFTWARE_TAGS)
            if is_dj_tag:
                should_remove = False  # Keep DJ software tags
            else:
                # Check for spam in custom tags
                has_spam = any(spam in tag_value for spam in SPAM_KEYWORDS)
                if has_spam:
                    should_remove = True
                    reason = "custom tag with spam"
        
        # Always keep essential tags (unless they contain spam)
        elif base_tag in ESSENTIAL_TAGS:
            # BUT check if value contains spam (for COMM, TPUB)
            if base_tag in ['COMM', 'TPUB']:
                for spam_keyword in SPAM_KEYWORDS:
                    if spam_keyword.lower() in tag_value:
                        should_remove = True
                        reason = f"contains spam: {spam_keyword}"
                        break
        
        # Remove if in spam list
        elif base_tag in SPAM_TAGS:
            should_remove = True
            reason = f"spam/useless tag"
        
        # Remove unknown tags (not in essential list)
        elif base_tag not in ESSENTIAL_TAGS:
            should_remove = True
            reason = f"not essential"
        
        if should_remove:
            tags_to_delete.append(tag_key)
            result['removed_tags'].append({
                'tag': tag_key,
                'reason': reason,
                'value': str(tags[tag_key])[:100]  # First 100 chars
            })
        else:
            result['kept_tags'].append({
                'tag': tag_key,
                'value': str(tags[tag_key])[:100]
            })
    
    # Actually remove tags if not dry run
    if not dry_run and tags_to_delete:
        for tag_key in tags_to_delete:
            try:
                del tags[tag_key]
            except Exception as e:
                result['errors'].append(f"Failed to remove {tag_key}: {e}")
        
        try:
            tags.save()
            logger.info(f"Cleaned {len(tags_to_delete)} tags from {filepath.name}")
        except Exception as e:
            result['errors'].append(f"Failed to save: {e}")
    
    return result


def scan_directory(directory: Path, max_files: int | None = None, dry_run: bool = True) -> dict:
    """
    Scan directory for files needing cleaning.
    
    Args:
        directory: Path to scan
        max_files: Maximum number of files to scan (None = all)
        dry_run: If True, only report without cleaning
        
    Returns:
        dict with summary statistics
    """
    summary = {
        'total_files': 0,
        'files_with_spam': 0,
        'total_tags_removed': 0,
        'files': []
    }
    
    audio_files = list(directory.glob('**/*.mp3'))
    if max_files:
        audio_files = audio_files[:max_files]
    
    for filepath in audio_files:
        summary['total_files'] += 1
        
        result = clean_tags(filepath, dry_run=dry_run)
        
        if result.get('removed_tags'):
            summary['files_with_spam'] += 1
            summary['total_tags_removed'] += len(result['removed_tags'])
            summary['files'].append({
                'path': str(filepath),
                'result': result
            })
    
    return summary


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python tag_cleaner.py <directory> [--clean]")
        sys.exit(1)
    
    directory = Path(sys.argv[1])
    dry_run = '--clean' not in sys.argv
    
    if not directory.exists():
        print(f"Directory not found: {directory}")
        sys.exit(1)
    
    print(f"Scanning: {directory}")
    print(f"Mode: {'DRY RUN (no changes)' if dry_run else 'CLEANING'}")
    print()
    
    summary = scan_directory(directory, max_files=10, dry_run=dry_run)
    
    print(f"\nSummary:")
    print(f"  Total files scanned: {summary['total_files']}")
    print(f"  Files with spam tags: {summary['files_with_spam']}")
    print(f"  Total tags to remove: {summary['total_tags_removed']}")
    
    if summary['files_with_spam'] > 0:
        print(f"\nFiles with spam:")
        for file_info in summary['files'][:5]:  # Show first 5
            print(f"\n  {Path(file_info['path']).name}")
            for tag in file_info['result']['removed_tags'][:3]:  # Show first 3 tags
                print(f"    - {tag['tag']}: {tag['value'][:50]}...")
