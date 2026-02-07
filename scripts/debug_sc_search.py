#!/usr/bin/env python3
"""Debug SoundCloud search for Anyma - After Love."""
import time
import requests

from djlib.metadata.soundcloud import get_valid_client_id, API_SEARCH

cid = get_valid_client_id()
print(f"Client ID: {cid[:10]}...")

# Different query approaches
queries = [
    "Blue Purple Afro House After Love",
    "Blue Purple After Love",
    "anyma after love blue purple",
    "blue purple anyma",
    "iambluepurple after love",
]

for q in queries:
    time.sleep(1)
    print(f"\nQuery: {q!r}")
    r = requests.get(API_SEARCH, params={"q": q, "client_id": cid, "limit": 3}, timeout=10)
    print(f"  Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json() or {}
        coll = data.get("collection") or []
        print(f"  Results: {len(coll)}")
        for item in coll[:3]:
            title = item.get("title", "")[:50]
            user = item.get("user", {}).get("username", "")
            genre = item.get("genre", "")
            print(f"    - {user}: {title} [{genre}]")
