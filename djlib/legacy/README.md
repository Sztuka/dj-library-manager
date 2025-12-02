# Legacy Modules

This directory contains legacy modules maintained for historical reference only.

**Status:** DEPRECATED as of November 2025

## Remaining Legacy Code

- **classify.py** - Simple keyword-based classification heuristics (kept for reference)

## Removed Modules (November-December 2025)

The following modules have been **completely removed** from the codebase:

- ~~**taxonomy.py**~~ - Bucket taxonomy system removed (READY TO PLAY/CLUB/AFRO HOUSE etc.)
- ~~**placement.py**~~ - Bucket assignment heuristics removed
- ~~**genre.py**~~ - External genre votes → bucket suggestions removed
- ~~**buckets.py**~~ - Bucket validation helpers removed

All taxonomy YAML files (`taxonomy.yml`, `taxonomy.local.yml`, `taxonomy_map.yml`) have also been deleted.

## Current Approach

**Logistics-only paths:**

```python
from djlib.logistics import build_library_path, get_destination_path
from djlib.genre_canonical import resolve_genre

# Simple logistics destinations
dest = get_destination_path("library")  # Library root, reject, archive, or mixes
path = build_library_path(artist, filename)  # Library/{Artist}/{filename}

# Canonical genres (independent of folder structure)
genre_key, genre_label = resolve_genre("afro-house")  # ("AFRO_HOUSE", "Afro House")
```

## Why The Change?

The app is now a **library cleaner first**, not a "set builder":

1. **Folders are logistics**, not musical categories
2. **Genre classification** is separate from folder organization
3. **Playlists/smart sets** will be built separately (future phases)
4. **ML/context** (cocktail vs club) is a future enhancement

See `docs/ARCHITECTURE_EN.md` for the new philosophy.
