#!/usr/bin/env python3
"""Fix reversed artist/title entries in unsorted.xlsx."""
import sys
sys.path.insert(0, "/Users/sztuka/Projects/dj-library-manager")

import pandas as pd

df = pd.read_excel('data/unsorted.xlsx')

# Find reversed entries (where artist looks like a title with version keywords)
VERSION_KEYWORDS = ['remix', 'edit', 'mix', 'bootleg', 'vip', 'dub', 'rework', 'version']

fixed = 0
for idx, row in df.iterrows():
    artist = str(row.get('artist') or '').strip()
    title = str(row.get('title') or '').strip()
    
    # Check if artist has version keywords but title doesn't  
    artist_has_version = any(kw in artist.lower() for kw in VERSION_KEYWORDS)
    title_has_version = any(kw in title.lower() for kw in VERSION_KEYWORDS)
    
    if artist_has_version and not title_has_version:
        # Swap!
        print(f'FIXING: {artist} - {title}')
        print(f'    -> {title} - {artist}')
        df.at[idx, 'artist'] = title
        df.at[idx, 'title'] = artist
        df.at[idx, 'artist_suggest'] = title
        df.at[idx, 'title_suggest'] = artist
        fixed += 1

print(f'\nTotal fixed: {fixed}')
if fixed > 0:
    df.to_excel('data/unsorted.xlsx', index=False)
    print('Saved!')
else:
    print('No changes needed.')
