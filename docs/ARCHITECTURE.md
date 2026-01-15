# DJ Library Manager — Architecture

**Version:** MVP v1.0  
**Date:** January 2026  
**Purpose:** Technical documentation for developers and AI agents

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Project Structure](#project-structure)
3. [Data Model](#data-model)
4. [CLI Commands](#cli-commands)
5. [Metadata Pipeline](#metadata-pipeline)
6. [DJ Software Integration](#dj-software-integration)
7. [Audio Analysis](#audio-analysis)
8. [Technical Details](#technical-details)

---

## System Overview

DJ Library Manager is a **library cleaner** that:

1. Scans unorganized audio files from an inbox folder
2. Validates Rekordbox/Traktor analysis (BPM, Key)
3. Enriches metadata from multiple online sources
4. Provides Excel-based curation workflow
5. Organizes approved files into clean folder structure
6. Syncs with DJ software databases

### Core Philosophy

- **Folders are logistics**, not musical categories
- **Genre classification** lives in metadata, not folder names
- **Simple destinations**: library, reject, archive, mixes
- **Excel as UI**: human-friendly curation, machine-readable data

---

## Project Structure

```text
dj-library-manager/
├── djlib/                      # Main application module
│   ├── cli.py                  # CLI entry point and commands
│   ├── config.py               # Path and settings configuration
│   ├── csvdb.py                # CSV database operations
│   ├── unsorted.py             # UNSORTED folder & Excel management
│   ├── tags.py                 # Audio tag reading/writing (mutagen)
│   ├── tag_cleaner.py          # Spam tag removal
│   ├── djlib_tags.py           # Custom DJLIB_* tags (track IDs)
│   ├── filename.py             # Final filename generation
│   ├── mover.py                # File moving operations
│   ├── placement.py            # Destination folder logic
│   ├── fingerprint.py          # Audio fingerprint (fpcalc)
│   ├── enrich.py               # Enrichment orchestration
│   ├── external_sync.py        # DJ software integration
│   ├── rekordbox_status.py     # Rekordbox DB validation
│   ├── genre_canonical.py      # Canonical genre resolver
│   ├── genre_mapper.py         # Genre normalization
│   ├── audio/                  # Audio analysis
│   │   ├── cache.py            # SQLite cache for Essentia
│   │   ├── features.py         # Feature extraction
│   │   └── essentia_backend.py # Essentia integration
│   ├── metadata/               # API clients
│   │   ├── genre_resolver.py   # Multi-source genre resolution
│   │   ├── beatport.py         # Beatport API (OAuth)
│   │   ├── mb_client.py        # MusicBrainz API
│   │   ├── lastfm.py           # Last.fm API
│   │   ├── soundcloud.py       # SoundCloud API
│   │   └── coverart.py         # Cover art fetching/embedding
│   └── ml/                     # ML dataset export
│       ├── export_dataset.py   # Training dataset generation
│       └── models.py           # Model utilities
├── data/
│   ├── unsorted.xlsx           # Staging spreadsheet
│   ├── library.csv             # Master track database
│   └── training_dataset_full.csv
├── LOGS/
│   ├── moves-{timestamp}.csv   # Move logs (for undo)
│   ├── audio_analysis.sqlite   # Essentia cache
│   ├── scan_status.json        # Last scan status
│   └── external_snapshots/     # DJ software snapshots
├── genres.yml                  # Canonical genre definitions
├── config.local.yml            # Local configuration (gitignored)
└── rules.yml                   # Auto-decision rules (legacy)
```

---

## Data Model

### library.csv (Master Database)

Primary database for all organized tracks.

```python
FIELDNAMES = [
    "track_id",              # UUID5 hash (stable identifier)
    "rekordbox_id",          # Rekordbox database ID
    "traktor_id",            # Traktor database ID
    "file_path",             # Current file path
    "original_path",         # Original path before move
    "file_hash",             # SHA256 hash
    "fingerprint",           # AcoustID fingerprint
    "added_date",            # ISO timestamp
    "final_filename",        # Generated filename
    "final_path",            # Destination path
    "artist", "title", "version_info",
    "genre", "bpm", "key_camelot",
    "energy_hint", "must_play", "occasion_tags", "notes",
    "is_duplicate",
    "pop_playcount", "pop_listeners",
    # MusicBrainz data
    "recording_mbid", "release_group_id",
    "original_album_title", "original_release_date",
    "original_release_year", "original_release_mbid",
]
```

### unsorted.xlsx (Staging Spreadsheet)

Excel file with all pending tracks. Key columns:

| Column          | Type      | Description                        |
| --------------- | --------- | ---------------------------------- |
| `track_id`      | UUID      | Stable track identifier            |
| `file_path`     | Path      | Source file location               |
| `artist`        | Editable  | Artist name                        |
| `title`         | Editable  | Track title                        |
| `genre`         | Dropdown  | One of 30 canonical genres         |
| `destination`   | Dropdown  | library / reject / archive / mixes |
| `done`          | Boolean   | TRUE to approve for export         |
| `bpm`           | Number    | Beats per minute                   |
| `key_camelot`   | String    | Key in Camelot notation (1A-12B)   |
| `genre_suggest` | Read-only | Suggested genre from enrichment    |
| `*_suggest`     | Read-only | Various suggestions                |

---

## CLI Commands

### Main Workflow Commands

| Command                          | Description                      |
| -------------------------------- | -------------------------------- |
| `configure`                      | Interactive configuration wizard |
| `scan [--strict]`                | Scan UNSORTED folder             |
| `enrich-online [--force-genres]` | Fetch online metadata            |
| `apply [--dry-run]`              | Export approved tracks           |
| `undo`                           | Revert last export               |

### DJ Software Commands

| Command                            | Description                       |
| ---------------------------------- | --------------------------------- |
| `sync-dj-libraries [--write]`      | Sync library.csv with DJ software |
| `import-rekordbox`                 | Import Rekordbox snapshot         |
| `import-traktor --collection PATH` | Import Traktor snapshot           |
| `add-to-rekordbox [--write]`       | Add tracks to Rekordbox           |
| `add-to-traktor --collection PATH` | Add tracks to Traktor             |

### Utility Commands

| Command                               | Description             |
| ------------------------------------- | ----------------------- |
| `analyze-audio [--check-env]`         | Essentia audio analysis |
| `ml-export-training-dataset`          | Export ML training data |
| `dupes`                               | Show duplicate tracks   |
| `fix-fingerprints`                    | Repair fingerprint data |
| `genres resolve --artist X --title Y` | Test genre resolution   |

---

## Metadata Pipeline

### Genre Resolution

Multi-source weighted voting system:

| Source      | Weight | Data                       |
| ----------- | ------ | -------------------------- |
| Beatport    | 10     | EDM genres (authoritative) |
| Last.fm     | 6      | Tags from user community   |
| MusicBrainz | 3      | Structured genre data      |
| SoundCloud  | 2      | Genre tags                 |

**Resolution Process:**

1. Query all available sources
2. Normalize genres to canonical keys (genres.yml)
3. Apply weights and specificity boost
4. Return highest-scoring genre

**Specificity Boost:** Subgenres get 1.5-2.0x multiplier over generic parents.

```python
# Example: "tech house" vs "house"
# tech house: weight 10 × boost 1.5 = 15
# house: weight 10 × boost 1.0 = 10
# Winner: tech house
```

### Canonical Genres (genres.yml)

30 canonical genres with synonyms:

```yaml
tech_house:
  name: "Tech House"
  synonyms: ["tech-house", "techhouse"]

house:
  name: "House"
  synonyms: ["deep house", "progressive house"]

rock_and_roll:
  name: "Rock 'n' Roll"
  synonyms: ["rock and roll", "rock & roll", "rockabilly"]
```

### Artist Validation (Beatport)

Beatport results are validated against search artist to prevent false matches:

```python
# Normalize and compare:
bp_artist = "Vitess".lower().replace(" ", "")  # "vitess"
search_artist = "Shakin' Stevens".lower().replace(" ", "")  # "shakin'stevens"
# No match → reject Beatport result
```

---

## DJ Software Integration

### Rekordbox Integration

**Library:** `pyrekordbox` for database access

**Capabilities:**

- Read track IDs from master.db (SQLite + SQLCipher)
- Extract BPM/Key (more reliable than file tags for FLAC)
- Write metadata updates to database
- Update file paths after moves

**BPM Storage:** Rekordbox stores BPM × 100 (118 BPM = 11800)

```python
# Writing BPM to Rekordbox:
content.BPM = int(bpm_float * 100)  # 118.0 → 11800
```

### Traktor Integration

**Library:** XML parsing (collection.nml)

**Capabilities:**

- Read track IDs from collection.nml
- Extract metadata (BPM, Key, cues)
- Update file paths
- Add new tracks

### Custom Tags (DJLIB\_\*)

Persistent track identification written to audio files:

| Tag                   | Description             |
| --------------------- | ----------------------- |
| `DJLIB_TRACK_ID`      | UUID5 stable identifier |
| `DJLIB_REKORDBOX_ID`  | Rekordbox database ID   |
| `DJLIB_TRAKTOR_ID`    | Traktor database ID     |
| `DJLIB_ORIGINAL_PATH` | Original file path      |

Stored as TXXX frames (MP3) or Vorbis comments (FLAC/OGG).

---

## Audio Analysis

### Essentia Integration

**Modes:**

1. Python bindings (preferred)
2. CLI fallback (`essentia_streaming_extractor_music`)
3. Docker fallback (cross-platform)

**Features Extracted:**

- BPM, Key (with Camelot conversion)
- Energy, Danceability
- Spectral features (centroid, rolloff, flux)
- MFCCs (for ML training)

### Cache Storage

SQLite database: `LOGS/audio_analysis.sqlite`

```sql
CREATE TABLE audio_features (
    file_path TEXT PRIMARY KEY,
    file_hash TEXT,
    bpm REAL,
    key TEXT,
    key_camelot TEXT,
    energy REAL,
    danceability REAL,
    -- ... 50+ features
    analyzed_at TEXT,
    algorithm_version TEXT
);
```

---

## Technical Details

### File Naming Format

```text
Artist - Title (Version) [Key BPM].ext
```

Examples:

- `Daft Punk - Around The World [5A 121].mp3`
- `Armand Van Helden - My My My (Deekline Remix) [9A 136].flac`

### Tag Cleaning

Removes spam metadata while preserving:

- DJ software data (cues, ratings, artwork)
- Custom DJLIB\_\* tags

Spam patterns removed:

- `musicdjs.club`, `chomikuj.pl`, etc.
- Promo tags, watermarks

### Cover Art

During export:

1. Clear album tag (compilations not useful for DJs)
2. Embed custom cover art from `data/djlibrary_cover-2-resized.jpg`

### HTTP Caching

API responses cached in SQLite: `djlib_http_cache.sqlite`

- MusicBrainz: 14 days
- Last.fm: 14 days
- Beatport: Per-session (OAuth tokens)

### Configuration (config.local.yml)

```yaml
library_root: "~/Music Library"
inbox_dir: "~/Music Unsorted"
reject_dir: "~/Music Rejected"
archive_dir: "~/Music Archive"

# Optional
beatport_username: "user@email.com"
traktor_collection: "~/Documents/Native Instruments/Traktor/collection.nml"
```

---

## Error Handling

### Common Issues

| Issue              | Solution                                   |
| ------------------ | ------------------------------------------ |
| Rekordbox locked   | Close Rekordbox before running             |
| Excel file locked  | Save and close Excel before `apply`        |
| Missing fpcalc     | Install Chromaprint or use bundled binary  |
| Essentia not found | Install via Homebrew or Conda              |
| BPM divided by 100 | Fixed: Rekordbox BPM stored as value × 100 |

### Logs

All operations logged to `LOGS/`:

- `moves-{timestamp}.csv` — File moves (for undo)
- `scan_status.json` — Last scan results
- `enrich_status.json` — Enrichment progress

---

## Future Enhancements

See [possible_upgrades.md](possible_upgrades.md) for planned improvements:

- ML genre prediction
- Smart playlist generation
- Parallel API fetching
- Batch processing optimizations
