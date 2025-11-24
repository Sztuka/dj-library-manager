#!/usr/bin/env python3
"""
Inspect all ID3 tags in audio files to find spam/unwanted metadata.

Usage:
    python scripts/inspect_id3_tags.py [directory]
"""

from pathlib import Path
from mutagen.id3 import ID3
from collections import Counter
import sys

def inspect_tags(directory: Path, max_files: int = 50):
    """
    Scan audio files and collect all ID3 tag types.
    
    Args:
        directory: Directory to scan
        max_files: Maximum number of files to inspect
    """
    tag_counts = Counter()
    suspicious_tags = {}
    
    print(f"Scanning: {directory}")
    print("=" * 80)
    
    audio_files = list(directory.glob("**/*.mp3"))[:max_files]
    print(f"Found {len(audio_files)} files to inspect\n")
    
    for idx, file_path in enumerate(audio_files, 1):
        try:
            tags = ID3(file_path)
            
            print(f"\n[{idx}/{len(audio_files)}] {file_path.name}")
            print("-" * 80)
            
            has_suspicious = False
            for key in sorted(tags.keys()):
                tag_counts[key] += 1
                frame = tags[key]
                value = str(frame)
                
                # Check for suspicious content
                suspicious_keywords = [
                    'chomikuj', 'mp3baza', 'mp3base', 'download', 'torrent',
                    'www.', 'http', '.pl', '.com', 'uploaded by', 'ripped by',
                    'APIC:', 'image'
                ]
                
                is_suspicious = any(kw.lower() in value.lower() for kw in suspicious_keywords)
                
                if is_suspicious or key not in ['TIT2', 'TPE1', 'TALB', 'TDRC', 'TCON', 'TBPM', 'TKEY']:
                    print(f"  ⚠️  {key}: {value[:100]}")
                    has_suspicious = True
                    
                    if file_path.name not in suspicious_tags:
                        suspicious_tags[file_path.name] = []
                    suspicious_tags[file_path.name].append((key, value[:200]))
                else:
                    print(f"  ✓  {key}: {value[:80]}")
            
            if not has_suspicious:
                print("  [All tags look clean]")
                
        except Exception as e:
            print(f"  ❌ Error reading {file_path.name}: {e}")
    
    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY: All ID3 tag types found")
    print("=" * 80)
    for tag_type, count in tag_counts.most_common():
        print(f"  {tag_type}: {count} files")
    
    if suspicious_tags:
        print("\n" + "=" * 80)
        print("FILES WITH SUSPICIOUS TAGS")
        print("=" * 80)
        for filename, tags_list in suspicious_tags.items():
            print(f"\n{filename}:")
            for tag_key, tag_value in tags_list:
                print(f"  {tag_key}: {tag_value}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        directory = Path(sys.argv[1])
    else:
        # Default to ~/Music_DJ/UNSORTED
        directory = Path.home() / "Music_DJ" / "UNSORTED"
    
    if not directory.exists():
        print(f"Error: Directory not found: {directory}")
        sys.exit(1)
    
    inspect_tags(directory, max_files=50)
