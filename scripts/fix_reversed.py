#!/usr/bin/env python3
"""Fix reversed artist/title entries in unsorted.csv."""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))

from djlib.unsorted import load_unsorted_rows, write_unsorted_rows
from djlib.config import UNSORTED_CSV

rows = load_unsorted_rows(UNSORTED_CSV)

# Find reversed entries (where artist looks like a title with version keywords)
VERSION_KEYWORDS = ['remix', 'edit', 'mix', 'bootleg', 'vip', 'dub', 'rework', 'version']

fixed = 0
for row in rows:
    artist = (row.get('artist') or '').strip()
    title = (row.get('title') or '').strip()
    
    # Check if artist has version keywords but title doesn't  
    artist_has_version = any(kw in artist.lower() for kw in VERSION_KEYWORDS)
    title_has_version = any(kw in title.lower() for kw in VERSION_KEYWORDS)
    
    if artist_has_version and not title_has_version:
        # Swap!
        print(f'FIXING: {artist} - {title}')
        print(f'    -> {title} - {artist}')
        row['artist'] = title
        row['title'] = artist
        row['artist_suggest'] = title
        row['title_suggest'] = artist
        fixed += 1

print(f'\nTotal fixed: {fixed}')
if fixed > 0:
    write_unsorted_rows(UNSORTED_CSV, rows, [])
    print('Saved!')
else:
    print('No changes needed.')
