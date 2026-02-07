#!/usr/bin/env python3
"""Debug genre resolution for specific tracks."""

import sys
sys.path.insert(0, "/Users/sztuka/Projects/dj-library-manager")

from djlib.metadata.genre_resolver import resolve
from djlib.metadata.soundcloud import get_soundcloud_genres

# Clear cache
get_soundcloud_genres.cache_clear()

# Test 1: Major Lazer Remix
print("=== Bad Bunny - Dakiti (Major Lazer Remix) ===")
result1 = resolve("Bad Bunny & Jhay Cortez", "Dakiti", "Major Lazer Remix")
if result1:
    print(f"Main: {result1.main}")
    print(f"Subs: {result1.subs}")
    print(f"Confidence: {result1.confidence}")
    print(f"Breakdown: {result1.breakdown}")
else:
    print("Result: None")

print()

# Test 2: Reviction Edit  
print("=== Swedish House Mafia - Leave The World Behind (Reviction FR Life Edit) ===")
result2 = resolve("Axwell, Ingrosso, Steve Angello, Laidback Luke", "Leave The World Behind", "Reviction FR Life Edit")
if result2:
    print(f"Main: {result2.main}")
    print(f"Subs: {result2.subs}")
    print(f"Confidence: {result2.confidence}")
    print(f"Breakdown: {result2.breakdown}")
else:
    print("Result: None")
