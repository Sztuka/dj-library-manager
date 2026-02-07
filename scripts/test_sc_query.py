#!/usr/bin/env python3
"""Test SoundCloud query strategies."""
import sys
sys.path.insert(0, "/Users/sztuka/Projects/dj-library-manager")

import requests
import importlib
import djlib.metadata.soundcloud as sc_module
importlib.reload(sc_module)

from djlib.metadata.soundcloud import get_valid_client_id, _candidate_queries, get_soundcloud_genres
get_soundcloud_genres.cache_clear()

cid = get_valid_client_id()
print(f"Client ID: {cid[:10]}...")

# Test cases
test_cases = [
    ("Kon x Bun Xapa, Moojo", "Belly Dancer (Bananza) x Hate it or love it", ""),
    ("Stromae", "Alors on danse", "ALLERTZ REMIX"),
    ("Axwell, Ingrosso, Steve Angello, Laidback Luke", "Leave The World Behind", "Reviction FR Life Edit"),
]

for artist, title, version in test_cases:
    print(f"\n{'='*60}")
    print(f"Artist: {artist}")
    print(f"Title: {title}")
    print(f"Version: {version}")
    queries = _candidate_queries(artist, title, version)
    print(f"Queries: {queries}")
    
    result = get_soundcloud_genres(artist, title, version)
    print(f"Result: {result}")
