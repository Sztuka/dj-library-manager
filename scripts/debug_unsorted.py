#!/usr/bin/env python3
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))

from djlib.unsorted import load_unsorted_rows
from djlib.config import UNSORTED_CSV

rows = load_unsorted_rows(UNSORTED_CSV)

# Print tracks without genre
print('=== TRACKI BEZ GATUNKU ===')
count = 0
for idx, row in enumerate(rows, 2):
    genre = row.get('genre')
    if not genre or genre == 'None':
        artist = row.get('artist') or row.get('artist_suggest') or ''
        title = row.get('title') or row.get('title_suggest') or ''
        version_info = row.get('version_info') or ''
        version_suggest = row.get('version_suggest') or ''
        print(f'{idx}: {artist} - {title}')
        print(f'    version_info={version_info!r}')
        print(f'    version_suggest={version_suggest!r}')
        count += 1

print(f'\n=== RAZEM: {count} tracków bez gatunku ===')
