# Legacy Modules

This directory contains modules from the bucket-taxonomy-based architecture.

**Status:** DEPRECATED as of November 2025

**DO NOT USE** these modules for new features. They are maintained for backward compatibility only.

## Deprecated Modules

- **taxonomy.py** - Old bucket taxonomy system (`READY TO PLAY/CLUB/AFRO HOUSE` etc.)
- **placement.py** - Bucket assignment heuristics (genre/BPM → bucket mapping)
- **classify.py** - AI bucket guessing (simple keyword heuristics)
- **genre.py** - External genre votes → bucket suggestions using taxonomy_map.yml

## Migration Path

**Old approach (deprecated):**

```python
from djlib.taxonomy import allowed_targets, target_to_path
buckets = allowed_targets()  # ["READY TO PLAY/CLUB/HOUSE", ...]
path = target_to_path("READY TO PLAY/CLUB/HOUSE")
```

**New approach (recommended):**

```python
from djlib.logistics import build_library_path, get_destination_path
from djlib.genre_canonical import resolve_genre

# Simple logistics paths
dest = get_destination_path("library")  # LIBRARY/, REJECT/, ARCHIVE/
path = build_library_path(artist, filename)  # LIBRARY/{Artist}/{filename}

# Canonical genres (no bucket mapping)
genre_key, genre_label = resolve_genre("afro-house")  # ("AFRO_HOUSE", "Afro House")
```

## Why The Change?

The app is now a **library cleaner first**, not a "set builder":

1. **Folders are logistics**, not musical categories
2. **Playlists/smart sets** will be built separately (future phases)
3. **ML/context** (cocktail vs club) is a future enhancement
4. **Genre classification** is separate from folder organization

See `docs/ARCHITECTURE_EN.md` for the new philosophy.
