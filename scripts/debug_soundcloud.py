#!/usr/bin/env python3
"""Debug script for genre resolution."""
from djlib.metadata.genre_resolver import resolve

# Test problematycznych tracków
tracks = [
    ('Akon', 'Right Now', 'Okan Evci & Emre Yuksel Remix'),
    ('Anyma', 'After Love', 'Blue Purple Afro House Remix'),
    ('Alors on danse (ALLERTZ REMIX)', 'Stromae', ''),
    ('Artemas', 'I Like The Way You Miss Me', 'Tasty Or Not Remix'),
    ('Alicia Keys', 'Show Me Love', 'Claudio Cristo & Yves Latroa Remix'),
]

print('=== Test resolve() z fallback ===')
for artist, title, version in tracks:
    result = resolve(artist, title, version)
    print(f'{artist} - {title}')
    print(f'  main={result.main if result else None}')
    if result:
        print(f'  sources: {[s.source for s in result.breakdown]}')
    print()
