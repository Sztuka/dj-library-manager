#!/usr/bin/env python3
"""Debug SoundCloud query for Alesso X Depeche Mode mashup."""
import sys
sys.path.insert(0, "/Users/sztuka/Projects/dj-library-manager")

import requests
from djlib.metadata.soundcloud import get_valid_client_id

cid = get_valid_client_id()
queries = [
    'Enjoy The Silence X If I Lose Myself Vidojean',
    'Alesso X Depeche Mode Vidojean',
    'Vidojean Oliver Loenn Enjoy Silence',
    'Vidojean Enjoy The Silence mashup',
    'Vidojean Alesso mashup',
]

for q in queries:
    print(f'Query: {q}')
    r = requests.get('https://api-v2.soundcloud.com/search/tracks', 
                     params={'q': q, 'client_id': cid, 'limit': 3}, timeout=10)
    print(f'  Status: {r.status_code}')
    if r.status_code == 200:
        data = r.json()
        coll = data.get('collection', [])
        print(f'  Found: {len(coll)} tracks')
        for item in coll[:2]:
            title = item.get('title', '?')
            genre = item.get('genre', '')
            tags = item.get('tag_list', '')[:60]
            print(f'    - {title}')
            print(f'      Genre: {genre}, Tags: {tags}')
    print()
