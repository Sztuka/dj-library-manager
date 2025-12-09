# MusicBrainz Canonical Data Integration

## Overview

DJ Library Manager now uses **MusicBrainz Canonical Data** for fast, offline lookup of canonical recording→release mappings. This solves the "wrong album" problem where MusicBrainz API searches return compilations, live albums, or bootlegs instead of the original studio release.

## What is Canonical Data?

MusicBrainz Canonical Data is a curated dataset that:

- Contains **canonical recording→release pairs** (typically first studio release)
- **Filters out** Live albums, Compilations, Bootlegs
- Pre-normalized for **fast fuzzy matching**
- Updated **monthly** by MusicBrainz
- **~15 GB** uncompressed CSV (~2 GB compressed)

## Setup

### 1. Download Canonical Dump

```bash
cd data/
curl -O https://data.metabrainz.org/pub/musicbrainz/canonical_data/musicbrainz-canonical-dump-LATEST/musicbrainz-canonical-dump-LATEST.tar.zst
```

Latest dumps available at:
https://data.metabrainz.org/pub/musicbrainz/canonical_data/

### 2. Import to SQLite

```bash
.venv/bin/python -m djlib.cli import-canonical-dump
```

This will:

- Extract CSV from `.tar.zst` archive
- Import all rows into SQLite database
- Create indexed lookup table
- **Takes ~10-15 minutes** (one-time operation)
- Creates `data/musicbrainz_canonical.db` (~5 GB)

### 3. Verify

```bash
ls -lh data/musicbrainz_canonical.db
# Should show ~5 GB file
```

## Usage

Once the database is set up, it's **automatic**! The enrichment pipeline uses it:

```bash
.venv/bin/python -m djlib.cli enrich-online
```

**Lookup strategy:**

1. **Canonical lookup** (instant, offline) ✨
2. Fallback to **Live MusicBrainz API** (if canonical miss)
3. Fallback to **AcoustID** (if MB miss)

## Benefits

### Before (MusicBrainz API only)

- ❌ AC/DC "T.N.T." → "Return of the Phoenix" (live 2009)
- ❌ Led Zeppelin "Whole Lotta Love" → "Thunder Rock" (compilation 1990)
- ❌ 1-2 seconds per track (API rate limit)
- ❌ Requires internet connection

### After (Canonical Data)

- ✅ AC/DC "T.N.T." → "T.N.T." (1975) studio album
- ✅ Led Zeppelin "Whole Lotta Love" → "Led Zeppelin II" (1969)
- ✅ <10ms per track (indexed lookup)
- ✅ Works offline

## Performance

| Operation      | Before     | After      |
| -------------- | ---------- | ---------- |
| Lookup time    | 1-2s (API) | <10ms (DB) |
| 100 tracks     | ~200s      | ~1s        |
| Offline mode   | ❌ No      | ✅ Yes     |
| Canonical data | ❌ No      | ✅ Yes     |

## Maintenance

### Update Canonical Data

MusicBrainz releases new dumps monthly. To update:

```bash
cd data/
rm musicbrainz-canonical-dump-*.tar.zst
curl -O https://data.metabrainz.org/pub/musicbrainz/canonical_data/musicbrainz-canonical-dump-LATEST/musicbrainz-canonical-dump-LATEST.tar.zst

.venv/bin/python -m djlib.cli import-canonical-dump --force
```

### Storage Requirements

- **Download:** 2.1 GB (`.tar.zst` compressed)
- **Database:** ~5 GB (SQLite with indexes)
- **Total:** ~7 GB disk space

Store on **local SSD** for best performance. Network storage (NAS) will be 5-10x slower.

## Troubleshooting

### Database not found

```
❌ No canonical dump found in data/ folder.
```

**Solution:** Download dump first (see Setup step 1)

### Import fails

```
❌ Import failed: ValueError: CSV file not found in archive
```

**Solution:** Re-download dump (may be corrupted)

### Slow lookups

If lookups are slow (>100ms), check:

- Database is on **local SSD** (not network drive)
- Database has indexes (re-import with `--force` if needed)

## Technical Details

### Database Schema

```sql
CREATE TABLE canonical_recordings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_credit_name TEXT NOT NULL,
    release_name TEXT NOT NULL,
    release_mbid TEXT NOT NULL,
    recording_name TEXT NOT NULL,
    recording_mbid TEXT NOT NULL,
    combined_lookup TEXT NOT NULL  -- normalized "artisttrack" for matching
);

CREATE INDEX idx_combined_lookup ON canonical_recordings(combined_lookup);
```

### Normalization

Lookups use normalized keys:

- Remove special characters: `AC/DC` → `acdc`
- Lowercase: `Led Zeppelin` → `led zeppelin`
- Remove spaces: `led zeppelin whole lotta love` → `ledzeppelinwholottalove`

This enables **fuzzy matching** without external libraries.

### Integration Points

Canonical lookup is integrated in:

- `djlib/enrich.py` → `lookup_musicbrainz()` (artist+title search)
- `djlib/enrich.py` → `lookup_acoustid()` (fingerprint → recording MBID)

Both try canonical first, then fallback to API.

## Credits

- **MusicBrainz Canonical Data**: https://musicbrainz.org/doc/Canonical_MusicBrainz_data
- **MetaBrainz Foundation**: https://metabrainz.org/
- Data licensed under **CC0** (public domain)
