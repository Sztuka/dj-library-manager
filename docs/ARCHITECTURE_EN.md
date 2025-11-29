# DJ Library Manager - Architecture Documentation

**Version:** 2.2  
**Date:** November 2025  
**Purpose:** Technical documentation for AI agents and developers

---

## Table of Contents

1. [System Overview](#system-overview)
2. [File and Folder Structure](#file-and-folder-structure)
3. [Taxonomy and Naming Conventions](#taxonomy-and-naming-conventions)
4. [CSV Data Structure](#csv-data-structure)
5. [File Naming Format](#file-naming-format)
6. [Modules and Components](#modules-and-components)
7. [Workflows and Processes](#workflows-and-processes)
8. [Technical Solutions](#technical-solutions)
9. [Coding Standards](#coding-standards)

---

## System Overview

DJ Library Manager is a **library cleaner first**, not a set builder. The system scans messy audio collections, enforces quality control via Rekordbox analysis, enriches metadata from online sources (MusicBrainz, Last.fm, Beatport, SoundCloud), and organizes tracks into a clean, deterministic folder structure.

### Core Philosophy (November 2025 Refactor)

**What changed:**

- ❌ **Removed:** Bucket-based taxonomy (`READY TO PLAY/CLUB/AFRO HOUSE` etc.)
- ✅ **Added:** Simple logistics folders (`LIBRARY`, `REJECT`, `ARCHIVE`)
- ✅ **Added:** Canonical genre resolution (genres.yml) separate from folder organization
- ✅ **Future:** Smart playlists and ML-based context will be built on top of clean library structure

**Why:**

1. **Folders are logistics**, not expressions of "sets" or "vibes"
2. **Genre classification** is orthogonal to file organization
3. **Playlists are the right place** for context-based organization (cocktail vs club vs pool party)
4. **ML predictions** should drive search/filtering, not folder placement

### Core Features:

- **Rekordbox Integration**: DB-first validation of analyzed tracks (BPM/Key from Rekordbox)
- **Strict Mode**: Enforce Rekordbox analysis in UNSORTED folder for quality control
- **UNSORTED Inbox Scanning**: Extract tags, validate Rekordbox analysis, generate proposals
- **Online Enrichment**: Metadata from MusicBrainz, AcoustID, Last.fm, SoundCloud, Beatport
- **Canonical Genre Resolution**: Multi-source genre detection → normalized genre keys (genres.yml)
- **Album Artwork Fetching**: 3-source fallback (Beatport → MusicBrainz CAA → Last.fm → SoundCloud) with rate limiting
- **Local Audio Analysis**: Essentia feature extraction (50+ features) → SQLite cache for ML training
- **Tag Cleaning**: Remove spam metadata (musicdjs.club, chomikuj.pl) while preserving DJ software data (Traktor/Serato cues, ratings, artwork)
- **File Moving & Renaming**: Deterministic logistics-only organization with undo support
- **Duplicate Detection**: Audio fingerprint + file hash comparison
- **Suggest/Accept Workflow**: Metadata proposals for user review
- **ML Training Export**: Export analyzed tracks with Essentia features for external ML model training

---

## File and Folder Structure

### Project Structure:

```
dj-library-manager/
├── djlib/              # Main application module
│   ├── config.py       # Path and settings configuration
│   ├── csvdb.py        # CSV database operations (includes rekordbox_id, traktor_id)
│   ├── tags.py         # Audio tag reading/writing
│   ├── tag_cleaner.py  # ID3 spam tag removal (preserves DJ software data)
│   ├── djlib_tags.py   # Custom DJLIB_* tags (track_id, rekordbox_id, traktor_id)
│   ├── external_sync.py # DJ software integration (Rekordbox/Traktor sync)
│   ├── rekordbox_status.py  # Rekordbox DB integration
│   ├── fingerprint.py  # Audio fingerprint and file hash
│   ├── filename.py     # File naming generation
│   ├── mover.py        # File moving operations
│   ├── placement.py    # Automatic bucket decisions
│   ├── enrich.py       # Online metadata enrichment
│   ├── extern.py       # External integrations (Last.fm)
│   ├── unsorted.py     # UNSORTED folder management
│   ├── legacy/         # DEPRECATED modules (not actively used)
│   │   ├── buckets.py      # Old bucket validation
│   │   ├── classify.py     # Old AI guessing buckets
│   │   ├── genre.py        # Old genre resolution
│   │   └── taxonomy.py     # Old taxonomy management
│   ├── audio/          # Local audio analysis
│   │   ├── cache.py    # Audio metrics cache (SQLite)
│   │   ├── features.py # Audio feature extraction
│   │   └── essentia_backend.py # Essentia analysis backend
│   ├── metadata/       # Metadata API clients
│   │   ├── genre_resolver.py   # Main genre resolver (source weights)
│   │   ├── beatport.py         # Beatport client (JWT auto-refresh)
│   │   ├── mb_client.py        # MusicBrainz client
│   │   ├── lastfm.py           # Last.fm client
│   │   ├── soundcloud.py       # SoundCloud client + health check
│   │   └── coverart.py         # Album artwork fetching (3-source fallback)
│   ├── bucketing/      # Auto-bucketing modules (FUTURE - for playlists)
│   │   ├── base.py     # Base interfaces
│   │   ├── rules.py    # Deterministic rules (v0)
│   │   └── simple_ml.py # ML classifier (v0.1)
│   └── ml/             # ML training dataset export
│       ├── export_dataset.py  # Training dataset generation
│       └── models.py   # Model evaluation utilities
├── scripts/            # CLI scripts
├── docs/               # Documentation
├── genres.yml          # Canonical genre definitions with synonyms (ACTIVE)
├── rules.yml           # Auto-decide rules (legacy)
├── taxonomy.yml        # Bucket definitions (DEPRECATED - see djlib/legacy/)
├── taxonomy_map.yml    # Tag → bucket mapping (DEPRECATED - genres.yml is canonical)
└── config.local.yml    # Local configuration (gitignored)
```

### Library Structure (LIB_ROOT):

```
~/Music_Library/
├── UNSORTED/                 # Scanned by scan command (strict mode)
├── Artist 1/                 # Main collection (organized by artist)
│   ├── Artist 1 - Track A (Remix) [5A 123].flac
│   └── Artist 1 - Track B [2B 128].mp3
├── Artist 2/
│   └── Artist 2 - Track C [8A 120].flac
├── MIXES/                    # DJ mixes (flat structure)
│   └── DJ Mix Name [128].mp3
└── ...

~/Music Rejected/             # Separate folder (flat structure)
├── track-1.mp3
├── track-2.flac
└── ...

~/Music Archive/              # Separate folder (organized by artist)
├── Artist A/
│   └── Artist A - Old Track [1A 110].mp3
└── ...

./LOGS/                       # Operation logs (in repo)
├── enrich_status.json        # Enrichment status
├── fingerprint_status.json
├── audio_analysis.sqlite     # Essentia feature cache
├── moves-{timestamp}.csv     # Move logs (with undo support)
└── dupes.csv                 # Duplicate reports

./data/
├── unsorted.xlsx             # Staging spreadsheet (Excel workflow)
├── library.csv               # Main database (track metadata + paths)
└── training_dataset_full.csv # ML training export
```

**Legacy folders** (deprecated, may exist in old installations):

```
~/Music_Library/
├── READY TO PLAY/            # Old bucket structure (DEPRECATED)
│   ├── CLUB/                 # Use main library folders for new tracks
│   └── OPEN FORMAT/
└── REVIEW QUEUE/             # Old review structure (DEPRECATED)
    └── UNDECIDED/
```

**Note:** Bucketing system (READY TO PLAY/CLUB/etc.) was removed in November 2025 refactor. Folders are now simple logistics: Main Library (organized by artist), Reject (flat), Archive (organized). Playlist generation and smart sets will be future features built on top of this clean structure.

---

## Folder Organization Philosophy

### New Model (November 2025)

**Folders are pure logistics**, not expressions of musical categories or "vibes".

**Structure:**

- `LIBRARY/{Artist}/{filename}` - All accepted tracks, organized by artist for easy browsing
- `REJECT/{filename}` - Tracks rejected but kept (no artist folders, flat structure)
- `ARCHIVE/{Artist}/{filename}` - Inactive/old tracks (optional)

**Why:**

1. **Deterministic**: One artist = one folder, no ambiguity
2. **Scalable**: No "which genre bucket?" decisions during curation
3. **Portable**: Easy to move to any DJ software or file manager
4. **Playlist-first**: Musical organization happens in playlists, not folders

**Future enhancements** (not in folder structure):

- Smart playlists based on genre, energy, context
- ML-predicted "cocktail" vs "club main room" scores
- Harmonic mixing suggestions (Camelot key wheel)
- BPM progression sequences

### Genre Classification

**Canonical genres** defined in `genres.yml` (single source of truth):

- Each genre has a `key` (e.g., `AFRO_HOUSE`), `label` (e.g., "Afro House"), and `synonyms`
- Resolver matches raw genre strings → canonical key + label
- Stored in `library.csv` as:
  - `genre`: User-selected label (e.g., "Afro House")
  - `genre_canonical`: Normalized key (e.g., "AFRO_HOUSE")

**Examples of canonical genres:**

- Electronic: `AFRO_HOUSE`, `TECH_HOUSE`, `MELODIC_TECHNO`, `TECHNO`, `HARD_TECHNO`, `HARDCORE`, `HOUSE`, `ELECTRO_SWING`, `TRANCE`, `DNB`
- Urban/Pop: `HIP_HOP`, `RNB`, `LATIN`, `POP`
- Rock: `ROCK`, `INDIE_ROCK`, `ROCK_N_ROLL`
- Classic: `DISCO`, `FUNK`, `SOUL`, `BLUES`, `SWING`
- Caribbean: `REGGAE`, `DANCEHALL`
- Afrobeats: `AFROBEATS` (distinct from `AFRO_HOUSE`)

See `genres.yml` for complete list and synonyms.

**Used for:**

- Filtering/search in future UI
- ML training dataset labels
- Playlist generation rules
- Metadata enrichment from multiple sources

**NOT used for:**

- Folder organization (folders are logistics only)

**Important:** Concepts like "singalong", "party", "wedding" are **bucket/usage concepts** (organizational categories), **not canonical genres**. They belong in folder structure or playlists, not in the genre field.

### Legacy Taxonomy (Deprecated)

**Old model (pre-November 2025):**

- Buckets like `READY TO PLAY/CLUB/AFRO HOUSE`
- target_subfolder determined folder placement
- Required taxonomy.yml + taxonomy_map.yml configuration

**Current model:**

- Simple logistics folders: Main Library (by artist), Reject (flat), Archive (by artist), Mixes (flat)
- Canonical genres in genres.yml (30 genres with synonyms)
- destination column: library/reject/archive/mixes
- Bucketing/playlists = future feature

**Status:** Legacy modules in `djlib/legacy/`, kept for backward compatibility only.

---

## File Naming Format

### Standard Format:

Main staging database in Excel format. Columns defined in `djlib/csvdb.py::FIELDNAMES`:

| Column                  | Description                             | Example                                 |
| ----------------------- | --------------------------------------- | --------------------------------------- |
| `source_path`           | Original file path                      | `/Users/user/Music/UNSORTED/track.mp3`  |
| `artist`                | Artist (accepted)                       | `Daft Punk`                             |
| `title`                 | Track title (accepted)                  | `Get Lucky`                             |
| `version_info`          | Version/remix (accepted)                | `Radio Edit`, `Extended Mix`            |
| `tag_genre`             | Genre from audio tags                   | `Electronic`                            |
| `tag_bpm`               | BPM from Rekordbox/tags                 | `120`                                   |
| `tag_key_camelot`       | Key from Rekordbox/tags (Camelot)       | `6A`                                    |
| `energy_hint`           | Energy hint (optional)                  | `high`, `medium`                        |
| `file_hash`             | SHA-256 file hash                       | `a1b2c3...`                             |
| `fingerprint`           | Audio fingerprint (Chromaprint)         | `AQAA...`                               |
| `is_duplicate`          | Duplicate flag (by fingerprint)         | `TRUE`, `FALSE`                         |
| `bucket_suggest`        | AI-suggested bucket                     | `READY TO PLAY/CLUB/HOUSE`              |
| `bucket_suggest_reason` | Suggestion rationale                    | `genre=house; conf=0.95`                |
| `target_bucket`         | User's final decision                   | `READY TO PLAY/CLUB/HOUSE`              |
| `done`                  | Approval flag (TRUE = ready to apply)   | `TRUE`, `FALSE`                         |
| `notes`                 | User notes                              | Any text                                |
| **Metadata proposals:** |                                         |                                         |
| `artist_suggest`        | Proposed artist                         | `Daft Punk`                             |
| `title_suggest`         | Proposed title                          | `Get Lucky`                             |
| `version_suggest`       | Proposed version                        | `Radio Edit`                            |
| `genre_suggest`         | Proposed main genre (aggregated)        | `House`                                 |
| `genres_beatport`       | Raw genre from Beatport                 | `Afro House`                            |
| `genres_musicbrainz`    | Raw genres from MusicBrainz             | `house; electronic`                     |
| `genres_lastfm`         | Raw tags from Last.fm                   | `house; french; disco`                  |
| `genres_soundcloud`     | Tag list from SoundCloud (optional)     | `house; afro; remix`                    |
| `pop_playcount`         | Last.fm playcount (if available)        | `123456`                                |
| `pop_listeners`         | Last.fm listeners (if available)        | `34567`                                 |
| `album_suggest`         | Proposed album                          | `Random Access Memories`                |
| `year_suggest`          | Proposed release year                   | `2013`                                  |
| `meta_source`           | Metadata source                         | `musicbrainz`, `acoustid+musicbrainz`   |
| `genre`                 | Final genre (one of 30 from genres.yml) | `House`                                 |
| `destination`           | Destination folder                      | `library`, `reject`, `archive`, `mixes` |
| `status`                | Track status (informational)            | `accept`, `reject`, `review`            |
| **Essentia features:**  | (for ML export only)                    |                                         |
| `ess_bpm`               | BPM from Essentia                       | `120.5`                                 |
| `ess_key_camelot`       | Key from Essentia (Camelot)             | `6A`                                    |
| `ess_energy`            | Energy score (0-1)                      | `0.75`                                  |
| `ess_danceability`      | Danceability score                      | `0.82`                                  |
| `ess_*`                 | Other Essentia features (see ML docs)   | Various metrics                         |

### Important Fields:

**`destination`**:

- `library` = Main Library (organized by artist)
- `reject` = Reject folder (flat structure)
- `archive` = Archive folder (organized by artist)
- `mixes` = MIXES folder (flat, for DJ mixes)
- Empty = not decided

**`status`** (informational only, doesn't control moves):

- `accept` = approved
- `reject` = rejected
- `review` = needs review
- Empty = not decided

**`done`**:

- `TRUE` = approved for apply command (move based on `destination`)
- `FALSE` or empty = skip

**`is_duplicate`**:

- Checked by comparing `fingerprint` (Chromaprint)
- Falls back to `file_hash` if no fingerprint

**Legacy fields** (backward compatibility only):

- `bucket_suggest`, `bucket_suggest_reason`, `target_bucket`: Old bucketing system (deprecated)
- `ai_guess_bucket`: Old ML suggestions (deprecated)

---

## File Naming Format

### Final Filename Format:

```
{Artist} - {Title} ({VersionInfo}) [{Key} {BPM}]{ext}
```

### Examples:

```
Daft Punk - Get Lucky (Radio Edit) [6A 120].mp3
The Prodigy - Firestarter (Original Mix) [8B 145].flac
Unknown Artist - Unknown Title (Original Mix) [?? ??].mp3
```

### Rules (from `djlib/filename.py`):

1. **VersionInfo**: If empty, set to `"Original Mix"`
2. **Key**: If empty, set to `"??"`
3. **BPM**: If empty, set to `"??"`
4. **Artist**: If empty, set to `"Unknown Artist"`
5. **Title**: If empty, set to `"Unknown Title"`
6. **Illegal characters**: `/\:*?"<>|` are replaced with `-`

### Name Conflicts:

If file with same name exists, number is added:

```
Artist - Title (Mix) [6A 120].mp3
Artist - Title (Mix) [6A 120] (2).mp3
Artist - Title (Mix) [6A 120] (3).mp3
```

---

## Modules and Components

### `djlib/rekordbox_status.py`

**Purpose**: Rekordbox DB integration and analysis validation

**Key Functions**:

- `was_analyzed_from_db(path)`: Check if file was analyzed in Rekordbox DB
- `was_analyzed(path, strict=False)`: Check analysis (DB-first, then tags)
- `debug_print_db_status()`: Print DB path and track count

**Behavior**:

- **DB-first priority**: Rekordbox DB is authoritative source
- **Strict mode**: When `strict=True`, rejects files without DB confirmation
- **Normal mode**: Accepts DB OR TBPM/TKEY tags (flexible for moved files)

**Configuration**:

- DB path: `~/Library/Pioneer/rekordbox/master.db` (macOS default)
- Requires: `pyrekordbox` library (optional, graceful degradation)

### `djlib/unsorted.py`

**Purpose**: UNSORTED folder management and Excel staging

**Key Functions**:

- `load_unsorted_xlsx()`: Load staging spreadsheet
- `save_unsorted_xlsx(rows)`: Save with done=FALSE rows only
- `export_done_to_library()`: Export approved tracks

**Workflow**:

1. `scan --strict` → populate `unsorted.xlsx`
2. User edits in Excel
3. `apply` → move done=TRUE tracks → clear from staging

### `djlib/config.py`

**Purpose**: Path and settings configuration

**Key Variables**:

- `LIB_ROOT`: Main library directory (default `~/Music Library`)
- `INBOX_UNSORTED`: UNSORTED folder to scan (default `~/Music Unsorted`)
- `REJECT_ROOT`: Reject folder (default `~/Music Rejected`)
- `ARCHIVE_ROOT`: Archive folder (default `~/Music Archive`)
- `MIXES_ROOT`: Mixes folder (default `~/Music Library/MIXES`)
- `LOGS_DIR`: Application logs (default `./LOGS` in repo)
- `UNSORTED_XLSX`: Staging spreadsheet (default `./data/unsorted.xlsx` in repo)

**Legacy variables** (deprecated, kept for backward compatibility):

- `READY_TO_PLAY_DIR`, `REVIEW_QUEUE_DIR`: Old bucketing structure

**Configuration**:

- Location: `config.local.yml` (preferred) or `~/.djlib_manager/config.yml`
- Format: YAML with keys `library_root` and `unsorted_dir`

### `djlib/audio/essentia_backend.py`

**Purpose**: Local audio analysis with Essentia

**Key Functions**:

- `analyze_file(path)`: Extract BPM/Key/Energy and features
- `batch_analyze(paths)`: Parallel batch processing
- `write_essentia_tags(path, metrics)`: Write to ID3 tags

**Features**:

- BPM detection with harmonic correction (0.5×/2×)
- Key detection → Camelot notation conversion
- Energy score (LUFS, Dynamic Complexity, Onset Rate, Spectral features)
- SQLite cache (by file hash + algorithm version)

**Use Cases**:

- Rekordbox alternative for new files
- Cache for moved files (DB paths stale)
- ML training feature extraction

### `djlib/metadata/genre_resolver.py`

**Purpose**: Multi-source genre resolution with weighted aggregation

**Key Function**:

- `resolve(artist, title, duration_s, version=None, disable_soundcloud=False)`: Main resolver

**Sources**:

- **Beatport** (weight 10.0, NEW): EDM genres + 1400px artwork, JWT auto-refresh
- **Last.fm** (weight 6.0): Track top tags
- **MusicBrainz** (weight 3.0): Recording/release-group/artist genres
- **SoundCloud** (weight 2.0, optional): Track genre + tag_list

**Algorithm**:

1. Collect tags from all sources
2. Weight by source confidence
3. Filter noise (generic tags like "music", "audio")
4. Aggregate: main genre + up to 2 sub-genres
5. Return: `genre_suggest` + per-source columns

**Configuration**:

- Weights adjustable in `config.yml`
- SoundCloud: requires `SOUNDCLOUD_CLIENT_ID`, health check before enrichment

### `djlib/ml/export_dataset.py`

**Purpose**: Export ML training dataset with Essentia features

**Key Function**:

- `export_training_dataset(output_path)`: Generate `training_dataset_full.csv`

**Workflow**:

1. Scan Main Library folders (organized by artist) + MIXES
2. For each file: extract Essentia features from cache
3. Combine with user-assigned genre (label from genres.yml)
4. Export CSV with columns: `source_path`, `genre`, `ess_bpm`, `ess_key_camelot`, `ess_energy`, `ess_*`

**Use Cases**:

- Train genre classification models
- Evaluate feature importance
- Build custom genre prediction systems
- FUTURE: Playlist/bucket assignment models

---

## Workflows and Processes

### Overview

**Production Workflow (November 2025):**

```
WORKFLOW 0: Sync DJ Libraries & Tags (optional)
  ↓ Compare library.csv with Rekordbox/Traktor
  ↓ Add missing tracks, update paths, add custom tags
  
WORKFLOW 1: Scan UNSORTED
  ↓ Read Rekordbox/Traktor DBs → get rekordbox_id/traktor_id
  ↓ Tag files with DJLIB_TRACK_ID + external IDs
  ↓ Generate unsorted.xlsx
  
WORKFLOW 2: Enrich Online
  ↓ MusicBrainz/Last.fm/Beatport/SoundCloud metadata
  
WORKFLOW 3: Manual Curation (Excel)
  ↓ Edit metadata, select destination, mark done=TRUE
  
WORKFLOW 4: Export (Apply)
  ↓ Move files to LIBRARY/REJECT/ARCHIVE
  ↓ AUTO-SYNC with Rekordbox/Traktor (add new + update paths)
  
WORKFLOW 5: Analyze Audio (Essentia)
  ↓ Extract 50+ features for ML training
  ↓ Only analyzes approved tracks in LIBRARY
  
WORKFLOW 6: ML Dataset Export
  ↓ Export training data with audio features
```

---

### 1. UNSORTED Folder Workflow (Rekordbox-First)

**Command**: `scan --strict`

**Process**:

1. Analyze files in Rekordbox (user prepares tracks)
2. Run `scan --strict`:
   - Check Rekordbox DB for analysis confirmation
   - Fall back to TBPM/TKEY tags if DB unavailable
   - Strict mode: reject files without Rekordbox confirmation
3. Generate proposals (AI guessing, genre resolution)
4. Export to `unsorted.xlsx` with `done = FALSE`

**Result**: Staging spreadsheet with proposals for user review

**Error Handling**:

- Strict mode error: "Track not in Rekordbox database (--strict mode). Solutions: 1) Import to Rekordbox and analyze, 2) Run without --strict"
- Normal mode error: "No BPM/Key analysis found. Need Rekordbox DB OR TBPM/TKEY tags"

### 2. Local Audio Analysis (Essentia Cache)

**Command**: `analyze-audio [--write-tags]`

**Process**:

1. Scan all files in UNSORTED (or specified folder)
2. For each file:
   - Check cache (by file hash + algo version)
   - If not cached: extract BPM/Key/Energy with Essentia
   - Store in SQLite cache
   - Optionally write to ID3 tags (`--write-tags`)
3. Log unstable analysis (BPM variance, low confidence)

**Result**: Cached metrics for ML export, optional tag writing for DJ software compatibility

**Use Cases**:

- Rekordbox alternative for files not analyzed there
- Cache for moved files (Rekordbox DB paths stale)
- ML feature extraction

### 3. Online Metadata Enrichment

**Command**: `enrich-online [--force-genres] [--skip-soundcloud]`

**Process**:

1. For records with `done != TRUE`:
   - **AcoustID lookup**: If fingerprint available → MusicBrainz recording
   - **MusicBrainz search**: Direct artist/title search
   - **SoundCloud probe** (optional, remix-aware): Fetch genre + tag_list, use `version` for query building
   - **Genre resolution**: Aggregate Beatport/MB/Last.fm/SoundCloud with weighted voting (Beatport 10.0 priority for EDM)
   - **Popularity hints**: Last.fm playcount/listeners
   - **Bucket suggestion**: Map genres to buckets via `taxonomy_map.yml`
2. Update `suggest_*` fields if better than existing

**Priorities**:

- AcoustID + MB (fingerprint) always wins (highest quality ID)
- Override if new data has higher aggregated confidence
- `--force-genres` forces overwrite of `genres_*` and `genre_suggest` even at equal confidence
- Preserve accepted (user) unless explicitly forced

**SoundCloud Integration**:

- Health check before enrichment: verify `SOUNDCLOUD_CLIENT_ID`
- Interactive prompt if missing/invalid
- `--skip-soundcloud` bypasses without prompt
- Remix-aware: uses `version` tokens (Extended, Remix, Radio) for targeted queries

### 4. Manual Review (Excel)

**File**: `unsorted.xlsx`

**Process**:

1. User opens `unsorted.xlsx` in Excel
2. Reviews proposals (`artist_suggest`, `title_suggest`, `genre_suggest`)
3. Edits final values (`artist`, `title`, `genre`, `destination`)
4. Selects genre from dropdown (30 canonical genres from genres.yml)
5. Selects destination: `library`, `reject`, `archive`, or `mixes`
6. Optionally sets `status`: `accept`, `reject`, or `review` (informational only)
7. Marks `done = TRUE` for approved tracks

**Validation**:

- Genre dropdown: only valid genres from genres.yml
- Destination dropdown: library/reject/archive/mixes
- Required fields: `artist`, `title`, `destination`, `done`

### 5. Apply Decisions (Move to LIBRARY)

**Command**: `apply [--dry-run]`

**Process**:

1. Load `unsorted.xlsx`
2. Filter rows with `done = TRUE`
3. For each approved track:
   - Resolve target path from `destination` column (library/reject/archive/mixes)
   - Generate final filename with Camelot notation
   - Clean spam tags, ALWAYS clear album tag
   - Move and rename file
   - Log operation to `LOGS/moves-{timestamp}.csv`
4. Remove done=TRUE rows from `unsorted.xlsx`

**Result**: Files organized in destination folders, staging cleared

**Dry Run**: `--dry-run` shows planned operations without execution

### 6. Undo (Rollback)

**Command**: `undo`

**Process**:

1. Find newest move log in `LOGS/moves-*.csv`
2. For each operation:
   - Move file back to original path
   - Restore to `unsorted.xlsx` with `done = FALSE`

### 7. ML Training Export

**Command**: `ml-export-training-dataset`

**Process**:

1. Scan Main Library folders (organized by artist) + MIXES
2. For each file:
   - Extract Essentia features (BPM, Key, Energy, Danceability, Spectral, etc.)
   - Get genre label from library.csv
3. Export to `data/training_dataset_full.csv`

**Output Columns**:

- `source_path`: File location
- `bucket`: User-assigned category (label)
- `ess_bpm`, `ess_key_camelot`, `ess_energy`, `ess_danceability`, `ess_*`: Essentia features

**Use Cases**:

- Train RandomForest/XGBoost bucket classifiers
- Feature importance analysis
- Build hybrid models (audio + text embeddings)

---

## Technical Solutions

### Rekordbox DB Integration

- **Library**: pyrekordbox 0.4.4 (Rekordbox6Database)
- **DB Path**: `~/Library/Pioneer/rekordbox/master.db` (macOS)
- **Query**: Match by FolderPath, check Analysed flag + BPM/KeyID fields
- **Graceful Degradation**: If pyrekordbox unavailable, fall back to tag-only mode

### Strict Mode Enforcement

- **Purpose**: Ensure Rekordbox-specific analysis quality in UNSORTED folder
- **DB-First Priority**: Rekordbox DB is authoritative, tags are fallback
- **Flexibility**: Normal mode (no --strict) accepts any BPM/Key source after file moves

### Local Audio Analysis (Essentia)

- **Framework**: Essentia v2.1-beta6-dev Python bindings
- **Features**: BPM, Key (Camelot), Energy, Onset Rate, Spectral Centroid/Rolloff
- **Cache**: SQLite database (`LOGS/audio_analysis.sqlite`) to avoid re-analysis
- **Tag Writing**: ID3 TKEY tag for keys, standard tags for BPM/Energy
- **Performance**: Batch processing, progress tracking, error handling
- **Rekordbox Alternative**: Complete metric extraction without DJ software

### Duplicate Detection

- **Primary Method**: Chromaprint fingerprint (compare audio even with different formats/bitrate)
- **Fallback**: SHA-256 hash (only identical files)
- **Implementation**: `djlib/fingerprint.py`

### Metadata Proposal System

- **Suggest/Accept Workflow**: Metadata split into accepted (main fields) and proposed (`suggest_*`)
- **Sources**: Filename parsing, audio tags, MusicBrainz, AcoustID, Last.fm, SoundCloud (optional)
- **Priorities**: AcoustID > MusicBrainz > filename/tags
- **Status**: `done` flag for approval

### Online Enrichment

- **MusicBrainz**: Recording search, genres/tags from recording/release-group/artist
- **AcoustID**: Fingerprint-based lookup (requires API key)
- **Last.fm**: Top tags for tracks
- **SoundCloud**: Track genre + tag_list (requires CLIENT_ID)
- **Rate Limiting**: 1 req/s for MB, HTTP caching with `requests-cache`

### Genre Resolution

- **Multi-Source Aggregation**: Combines data from 4 sources with weights: **Beatport 10.0** (gold standard for EDM, 100+ subgenres), Last.fm 6.0, MB 3.0, SoundCloud 2.0
- **Output Format**: "Main Genre, Sub1, Sub2" (max 3)
- **Confidence Threshold**: Base threshold for adding tag: >= 0.03; override existing: >= 0.08 (tunable with more sources)
- **Taxonomy Mapping**: Tags → buckets via `taxonomy_map.yml`
- **Auto-Refresh**: Beatport JWT (~1h, Playwright), SoundCloud client_id (~30d, Playwright) - transparent token renewal

### Name Conflict Resolution

- If file with same name exists: add number `(2)`, `(3)`, etc.
- **Implementation**: `djlib/mover.py::move_with_rename()`

### destination Validation (CURRENT)

- Check if destination is valid: `library`, `reject`, `archive`, or `mixes`
- **Implementation**: `djlib/unsorted.py::DESTINATION_CHOICES`

### target_bucket Validation (LEGACY - deprecated)

- Old bucketing system (pre-November 2025)
- **Implementation**: `djlib/legacy/taxonomy.py::is_valid_target()`

---

## Coding Standards

### Code Format:

- Python 3.10+
- Type hints (with `from __future__ import annotations`)
- `black` formatter (optional)
- `ruff` linter (optional)

### Naming Conventions:

- **Functions**: snake_case (`load_records`, `target_to_path`)
- **Classes**: PascalCase (`AppConfig`)
- **Variables**: snake_case (`csv_path`, `dest_dir`)
- **Constants**: UPPER_SNAKE_CASE (`FIELDNAMES`, `LIB_ROOT`)

### Import Structure:

```python
from __future__ import annotations

# Standard library
from pathlib import Path
from typing import List, Dict

# Third-party
import yaml
import requests

# Local imports
from djlib.config import LIB_ROOT
from djlib.taxonomy import allowed_targets
```

### Error Handling:

- Check file existence before operations
- Graceful degradation (e.g., no fingerprint → use hash)
- Log errors to stdout (exceptions not silent)

### Paths:

- Always use `pathlib.Path` instead of strings
- Resolve paths relative to `LIB_ROOT` set in config
- `_expand()` function in `config.py` handles `~` and expands paths

### CSV:

- Always UTF-8 encoding
- `newline=""` for cross-platform compatibility
- Use `csv.DictReader/DictWriter` with `FIELDNAMES`

### API Keys and Configuration:

- **AcoustID**: `acoustid_api_key` in config (Application API key)
- **Last.fm**: `lastfm_api_key` in config or env `LASTFM_API_KEY`
- **SoundCloud**: `SOUNDCLOUD_CLIENT_ID` in env (optional, health check)
- **MusicBrainz**: User-Agent in config (`app_name`, `app_version`, `contact`)

### External Dependencies:

```python
# Core
mutagen>=1.46          # Audio tag reading/writing
pyacoustid>=0.3        # AcoustID fingerprint lookup
requests>=2.31         # HTTP requests
requests-cache>=1.1    # HTTP caching
pyrekordbox>=0.4.4     # Rekordbox DB integration

# Audio analysis (optional)
essentia>=2.1b6.dev0   # Local BPM/Key/Energy extraction

# Optional for enrichment
musicbrainzngs>=0.7   # MusicBrainz API client
pylast>=5.2           # Last.fm API
```

### Caching:

- HTTP requests cached in `djlib_http_cache.sqlite`
- Rate limiting: 1 req/s for MusicBrainz
- Retry logic for API calls

---

## Testing and Quality

### Test Suites

- **Unit tests**: Filename parsing, config, audio cache, taxonomy, basic placement logic
- **Integration tests**: CLI commands (scan, enrich-online, apply, undo) on mini-fixtures

### Running Tests

**VS Code Tasks**:

- `TESTS — run` (pytest -q)
- `TESTS — coverage` (coverage with missing lines report)

**Manual CLI**:

```bash
pytest -q
pytest --cov=djlib --cov-report=term-missing
```

### Quality Gates

- **Build**: No compilation (pure Python) — verification via STEP 0 task
- **Tests**: All must pass (target: coverage > 80%)
- **Lint**: Planned introduction of `ruff` + `black` (CI future)

### Planned Quality Extensions

- Log user decision on skipping SoundCloud to `enrich_status.json`
- Snapshot tests for multi-source genre fusion (deterministic weight + alphabetical sort)
- Parametrized filename tests for multi-parentheses/duplicates/empty tokens

---

## Examples

### Add New Bucket:

```python
from djlib.taxonomy import add_ready_bucket, ensure_taxonomy_dirs

add_ready_bucket("CLUB/PROGRESSIVE HOUSE")
ensure_taxonomy_dirs()  # Creates directories
```

### Check Genre Validity (CURRENT):

```python
from djlib.metadata.genre_resolver import GENRES_YML_PATH
import yaml

with open(GENRES_YML_PATH) as f:
    genres_config = yaml.safe_load(f)
    valid_genres = list(genres_config['genres'].keys())

is_valid = "House" in valid_genres  # True
```

### Check Destination Validity (CURRENT):

```python
from djlib.unsorted import DESTINATION_CHOICES

destination = "library"
is_valid = destination in DESTINATION_CHOICES  # True
```

### Legacy Examples (deprecated, for backward compatibility only):

```python
from djlib.legacy.taxonomy import is_valid_target

is_valid_target("READY TO PLAY/CLUB/HOUSE")  # Old system
is_valid_target("READY TO PLAY/CLUB/UNKNOWN")  # Old system
```

---

## Notes for AI Agents

### When Proposing Changes:

1. **Check genres.yml**: Always use genres from the canonical 30-genre list
2. **Use destination column**: library/reject/archive/mixes (not target_subfolder)
3. **Album handling**: ALWAYS cleared during export (updates["album"] = "")
4. **CSV schema**: Don't add new columns without updating `UNSORTED_COLUMNS` in unsorted.py
5. **Path handling**: Always use `pathlib.Path`, not strings
6. **Backward compatibility**: Legacy fields (target_subfolder, bucket_suggest) kept for old data migration

### When Adding New Genres:

1. Add to `genres.yml` with synonyms
2. Update genre_resolver weights if needed
3. Run validation tests to ensure synonym matching works
4. Document in genres.yml with clear definition

### Current System (November 2025):

- **Folder structure**: Main Library/{Artist}/, Reject/ (flat), Archive/{Artist}/, Mixes/ (flat)
- **Genre system**: 30 canonical genres in genres.yml with comprehensive synonyms
- **Excel workflow**: destination column controls moves, status is informational only
- **Bucketing**: FUTURE feature for smart playlists (not folder organization)
- **Legacy support**: target_subfolder still works for backward compatibility, but destination is preferred

### Deprecated Features (kept for backward compatibility):

- `djlib/legacy/`: buckets.py, classify.py, genre.py, taxonomy.py
- `taxonomy.yml`, `taxonomy_map.yml`: Old bucketing configs
- `target_subfolder`, `bucket_suggest`, `ai_guess_bucket`: Old CSV columns
- `READY TO PLAY/`, `REVIEW QUEUE/`: Old folder structure

---

**Last Updated**: November 2025 (Rekordbox integration + ML export)  
**Version**: 2.2

---

### Planned Extension: `enrich_status.json`

Format (proposed) adding SoundCloud audit section:

```json
{
  "started_at": "2025-11-12T14:03:22Z",
  "completed_at": "2025-11-12T14:05:47Z",
  "rows_processed": 312,
  "soundcloud": {
    "client_id_status": "invalid", // ok | invalid | missing | error | rate-limit
    "decision": "aborted", // active | skipped | aborted
    "prompt_shown": true,
    "attempted_requests": 0,
    "timestamp": "2025-11-12T14:03:25Z"
  },
  "sources_counts": {
    "musicbrainz": 250,
    "lastfm": 260,
    "soundcloud": 0
  }
}
```

Goals:

- **Transparency**: Know if SoundCloud absence was decision vs error
- **Quality Monitoring**: Correlate completeness vs source activity
- **Foundation**: For automatic retry/adaptive rules

Implementation: In `enrich-online` command — initial write (started), update after health check (SC status), finalize on completion or abort.
