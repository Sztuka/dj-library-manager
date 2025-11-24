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

DJ Library Manager is a Rekordbox-first DJ library organization system. The system scans new audio files, enforces Rekordbox analysis quality, enriches metadata from online sources (MusicBrainz, Last.fm, optionally SoundCloud), classifies tracks by genre, and organizes them into folder structures.

### Core Features:

- **Rekordbox Integration**: DB-first validation of analyzed tracks (BPM/Key from Rekordbox)
- **Strict Mode**: Enforce Rekordbox analysis in UNSORTED folder for quality control
- **UNSORTED Inbox Scanning**: Extract tags, validate Rekordbox analysis, generate proposals
- **Online Enrichment**: Metadata from MusicBrainz, AcoustID, Last.fm, SoundCloud (optional)
- **Local Audio Analysis**: BPM/Key/Energy extraction with Essentia (Rekordbox alternative/cache)
- **Tag Writing**: Write metrics to ID3 tags (Camelot notation, TBPM/TKEY compatibility)
- **Tag Cleaning**: Remove spam metadata (musicdjs.club, chomikuj.pl) while preserving DJ software data (Traktor/Serato cues, ratings, artwork)
- **Genre Resolution**: Multi-source genre detection (MusicBrainz/Last.fm/SoundCloud) with weighted aggregation + per-source CSV columns
- **Automatic Classification**: AI guessing + taxonomy mapping to buckets
- **Taxonomy Management**: Category (bucket) structure validation
- **Rules-Based Decisions**: Auto-decide based on metadata heuristics
- **File Moving & Renaming**: Structured organization with undo support
- **Duplicate Detection**: Audio fingerprint + file hash comparison
- **Suggest/Accept Workflow**: Metadata proposals for user review
- **ML Training Export**: Export analyzed tracks with Essentia features for ML model training

---

## File and Folder Structure

### Project Structure:

```
dj-library-manager/
├── djlib/              # Main application module
│   ├── config.py       # Path and settings configuration
│   ├── taxonomy.py     # Bucket taxonomy management
│   ├── csvdb.py        # CSV database operations
│   ├── tags.py         # Audio tag reading/writing
│   ├── tag_cleaner.py  # ID3 spam tag removal (preserves DJ software data)
│   ├── rekordbox_status.py  # Rekordbox DB integration
│   ├── fingerprint.py  # Audio fingerprint and file hash
│   ├── filename.py     # File naming generation
│   ├── mover.py        # File moving operations
│   ├── classify.py     # AI guessing buckets (legacy)
│   ├── placement.py    # Automatic bucket decisions
│   ├── enrich.py       # Online metadata enrichment
│   ├── genre.py        # Genre resolution and taxonomy mapping
│   ├── extern.py       # External integrations (Last.fm)
│   ├── buckets.py      # Bucket validation
│   ├── unsorted.py     # UNSORTED folder management
│   ├── audio/          # Local audio analysis
│   │   ├── cache.py    # Audio metrics cache (SQLite)
│   │   ├── features.py # Audio feature extraction
│   │   └── essentia_backend.py # Essentia analysis backend
│   ├── metadata/       # Metadata API clients
│   │   ├── genre_resolver.py   # Main genre resolver (source weights)
│   │   ├── mb_client.py        # MusicBrainz client
│   │   ├── lastfm.py           # Last.fm client
│   │   └── soundcloud.py       # SoundCloud client + health check
│   ├── bucketing/      # Auto-bucketing modules
│   │   ├── base.py     # Base interfaces
│   │   ├── rules.py    # Deterministic rules (v0)
│   │   └── simple_ml.py # ML classifier (v0.1)
│   └── ml/             # ML training dataset export
│       ├── export_dataset.py  # Training dataset generation
│       └── models.py   # Model evaluation utilities
├── scripts/            # CLI scripts
├── docs/               # Documentation
├── taxonomy.yml        # Bucket definitions
├── taxonomy_map.yml    # Tag → bucket mapping
├── rules.yml           # Auto-decide rules
└── config.local.yml    # Local configuration (gitignored)
```

### Library Structure (LIB_ROOT):

```
~/Music_DJ/
├── UNSORTED/                 # Scanned by scan command (strict mode)
├── READY TO PLAY/            # Production-ready tracks
│   ├── CLUB/
│   │   ├── HOUSE
│   │   ├── TECH HOUSE
│   │   ├── TECHNO
│   │   └── ...
│   └── OPEN FORMAT/
│       ├── RNB
│       ├── HIP-HOP
│       └── ...
├── REVIEW QUEUE/             # Needs review
│   ├── UNDECIDED
│   └── NEEDS EDIT
├── LOGS/                     # Operation logs
│   ├── enrich_status.json    # Enrichment status (plan: add SoundCloud decision)
│   ├── fingerprint_status.json
│   ├── moves-{timestamp}.csv # Move logs
│   └── dupes.csv             # Duplicate reports
├── unsorted.xlsx             # Staging spreadsheet (Excel)
└── library.csv               # Main database (deprecated, use unsorted.xlsx)
```

---

## Taxonomy and Naming Conventions

### Naming Standards

**IMPORTANT:** All bucket names use SPACES (not underscores).

### target_subfolder Format:

```
READY TO PLAY/{SECTION}/{BUCKET}
REVIEW QUEUE/{BUCKET}
```

Where `{SECTION}` is: `CLUB` or `OPEN FORMAT`

### Available Buckets (from taxonomy.yml):

#### READY TO PLAY / CLUB:

- `CLUB/AFRO HOUSE`
- `CLUB/DEEP HOUSE`
- `CLUB/ELECTRO`
- `CLUB/ELECTRO SWING`
- `CLUB/HOUSE`
- `CLUB/MELODIC TECHNO`
- `CLUB/TECH HOUSE`
- `CLUB/TECHNO`
- `CLUB/TRANCE`
- `CLUB/DNB`
- `MIXES` (top-level bucket)

#### READY TO PLAY / OPEN FORMAT:

- `OPEN FORMAT/2000s`
- `OPEN FORMAT/2010s`
- `OPEN FORMAT/70s`
- `OPEN FORMAT/80s`
- `OPEN FORMAT/90s`
- `OPEN FORMAT/FUNK SOUL`
- `OPEN FORMAT/HIP-HOP`
- `OPEN FORMAT/LATIN REGGAETON`
- `OPEN FORMAT/PARTY DANCE`
- `OPEN FORMAT/POLISH SINGALONG`
- `OPEN FORMAT/RNB`
- `OPEN FORMAT/ROCK CLASSICS`
- `OPEN FORMAT/ROCKNROLL`

#### REVIEW QUEUE:

- `UNDECIDED`
- `NEEDS EDIT`

### Important Conventions:

1. **Spaces instead of underscores**: `TECH HOUSE` not `TECH_HOUSE`
2. **Uppercase**: All names in UPPERCASE
3. **Word order**: `MELODIC TECHNO` not `TECHNO MELODIC`, `AFRO HOUSE` not `AFROHOUSE`
4. **Section separator**: `/` between section and bucket
5. **Prefix in target_subfolder**: Always full path, e.g., `READY TO PLAY/CLUB/HOUSE`

---

## CSV Data Structure

### File: `unsorted.xlsx` (Excel staging)

Main staging database in Excel format. Columns defined in `djlib/csvdb.py::FIELDNAMES`:

| Column                  | Description                           | Example                                |
| ----------------------- | ------------------------------------- | -------------------------------------- |
| `source_path`           | Original file path                    | `/Users/user/Music/UNSORTED/track.mp3` |
| `artist`                | Artist (accepted)                     | `Daft Punk`                            |
| `title`                 | Track title (accepted)                | `Get Lucky`                            |
| `version_info`          | Version/remix (accepted)              | `Radio Edit`, `Extended Mix`           |
| `tag_genre`             | Genre from audio tags                 | `Electronic`                           |
| `tag_bpm`               | BPM from Rekordbox/tags               | `120`                                  |
| `tag_key_camelot`       | Key from Rekordbox/tags (Camelot)     | `6A`                                   |
| `energy_hint`           | Energy hint (optional)                | `high`, `medium`                       |
| `file_hash`             | SHA-256 file hash                     | `a1b2c3...`                            |
| `fingerprint`           | Audio fingerprint (Chromaprint)       | `AQAA...`                              |
| `is_duplicate`          | Duplicate flag (by fingerprint)       | `TRUE`, `FALSE`                        |
| `bucket_suggest`        | AI-suggested bucket                   | `READY TO PLAY/CLUB/HOUSE`             |
| `bucket_suggest_reason` | Suggestion rationale                  | `genre=house; conf=0.95`               |
| `target_bucket`         | User's final decision                 | `READY TO PLAY/CLUB/HOUSE`             |
| `done`                  | Approval flag (TRUE = ready to apply) | `TRUE`, `FALSE`                        |
| `notes`                 | User notes                            | Any text                               |
| **Metadata proposals:** |                                       |                                        |
| `artist_suggest`        | Proposed artist                       | `Daft Punk`                            |
| `title_suggest`         | Proposed title                        | `Get Lucky`                            |
| `version_suggest`       | Proposed version                      | `Radio Edit`                           |
| `genre_suggest`         | Proposed main genre (aggregated)      | `House, Electronic, Dance`             |
| `genres_musicbrainz`    | Raw genres from MusicBrainz           | `house; electronic`                    |
| `genres_lastfm`         | Raw tags from Last.fm                 | `house; french; disco`                 |
| `genres_soundcloud`     | Tag list from SoundCloud (optional)   | `house; afro; remix`                   |
| `pop_playcount`         | Last.fm playcount (if available)      | `123456`                               |
| `pop_listeners`         | Last.fm listeners (if available)      | `34567`                                |
| `album_suggest`         | Proposed album                        | `Random Access Memories`               |
| `year_suggest`          | Proposed release year                 | `2013`                                 |
| `meta_source`           | Metadata source                       | `musicbrainz`, `acoustid+musicbrainz`  |
| **Essentia features:**  | (for ML export only)                  |                                        |
| `ess_bpm`               | BPM from Essentia                     | `120.5`                                |
| `ess_key_camelot`       | Key from Essentia (Camelot)           | `6A`                                   |
| `ess_energy`            | Energy score (0-1)                    | `0.75`                                 |
| `ess_danceability`      | Danceability score                    | `0.82`                                 |
| `ess_*`                 | Other Essentia features (see ML docs) | Various metrics                        |

### Important Fields:

**`target_bucket`**:

- Empty = not decided
- `REJECT` = rejected
- `READY TO PLAY/...` or `REVIEW QUEUE/...` = ready to move

**`done`**:

- `TRUE` = approved for apply command
- `FALSE` or empty = skip

**`is_duplicate`**:

- Checked by comparing `fingerprint` (Chromaprint)
- Falls back to `file_hash` if no fingerprint

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

- `LIB_ROOT`: Main library directory (default `~/Music_DJ`)
- `UNSORTED_DIR`: Folder to scan (default `~/Music_DJ/UNSORTED`)
- `READY_TO_PLAY_DIR`: `LIB_ROOT / "READY TO PLAY"`
- `REVIEW_QUEUE_DIR`: `LIB_ROOT / "REVIEW QUEUE"`
- `LOGS_DIR`: `LIB_ROOT / "LOGS"`
- `UNSORTED_XLSX_PATH`: `LIB_ROOT / "unsorted.xlsx"`

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

- **MusicBrainz** (weight 3.0): Recording/release-group/artist genres
- **Last.fm** (weight 6.0): Track top tags
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

1. Scan LIBRARY folders (READY TO PLAY, REVIEW QUEUE)
2. For each file: extract Essentia features
3. Combine with user-assigned bucket (label)
4. Export CSV with columns: `source_path`, `bucket`, `ess_bpm`, `ess_key_camelot`, `ess_energy`, `ess_*`

**Use Cases**:

- Train bucket classification models
- Evaluate feature importance
- Build custom auto-bucketing systems

---

## Workflows and Processes

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
   - **Genre resolution**: Aggregate MB/Last.fm/SoundCloud with weighted voting
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
2. Reviews proposals (`artist_suggest`, `title_suggest`, `genre_suggest`, `bucket_suggest`)
3. Edits final values (`artist`, `title`, `target_bucket`)
4. Selects bucket from taxonomy dropdown (data validation)
5. Marks `done = TRUE` for approved tracks

**Validation**:

- Bucket dropdown: only valid taxonomy values
- Required fields: `artist`, `title`, `target_bucket`, `done`

### 5. Apply Decisions (Move to LIBRARY)

**Command**: `apply [--dry-run]`

**Process**:

1. Load `unsorted.xlsx`
2. Filter rows with `done = TRUE`
3. For each approved track:
   - Resolve target path from `target_bucket`
   - Generate final filename with Camelot notation
   - Move and rename file
   - Log operation to `LOGS/moves-{timestamp}.csv`
4. Remove done=TRUE rows from `unsorted.xlsx`

**Result**: Files organized in LIBRARY, staging cleared

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

1. Scan LIBRARY folders (READY TO PLAY, REVIEW QUEUE)
2. For each file:
   - Extract Essentia features (BPM, Key, Energy, Danceability, Spectral, etc.)
   - Determine bucket from file path
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

- **Multi-Source Aggregation**: Combines data from 3 sources (MB/Last.fm/SoundCloud) with weights: Last.fm 6.0, MB 3.0, SoundCloud 2.0
- **Output Format**: "Main Genre, Sub1, Sub2" (max 3)
- **Confidence Threshold**: Base threshold for adding tag: >= 0.03; override existing: >= 0.08 (tunable with more sources)
- **Taxonomy Mapping**: Tags → buckets via `taxonomy_map.yml`

### Name Conflict Resolution

- If file with same name exists: add number `(2)`, `(3)`, etc.
- **Implementation**: `djlib/mover.py::move_with_rename()`

### target_bucket Validation

- Check if target exists in taxonomy before moving
- **Implementation**: `djlib/taxonomy.py::is_valid_target()`

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

### Check Target Validity:

```python
from djlib.taxonomy import is_valid_target

is_valid_target("READY TO PLAY/CLUB/HOUSE")  # True
is_valid_target("READY TO PLAY/CLUB/UNKNOWN")  # False
```

### Use Auto-Decide:

```python
from djlib.placement import decide_bucket

row = {"genre": "tech house", "bpm": "128", ...}
bucket, confidence, reason = decide_bucket(row)
# ("CLUB/TECH HOUSE", 0.95, "genre=tech house")
```

---

## Notes for AI Agents

### When Proposing Changes:

1. **Check taxonomy.yml**: Always use names consistent with current taxonomy
2. **Use spaces**: Never underscores in bucket names
3. **target_subfolder format**: Always full path with prefix (`READY TO PLAY/...`)
4. **CSV schema**: Don't add new columns without updating `FIELDNAMES`
5. **Path handling**: Always use `pathlib.Path`, not strings
6. **Backward compatibility**: Check if existing scripts will work with changes

### When Adding New Buckets:

1. Add to `taxonomy.yml` (`ready_buckets` or `review_buckets` section)
2. Run `ensure_taxonomy_dirs()` to create directories
3. Update `djlib/placement.py` if bucket should be auto-recognized
4. Update `rules.yml` if rules needed for this bucket

### When Modifying Auto-Decide Rules:

- `rules.yml`: Simple keyword-based rules (contains → target)
- `placement.py`: Advanced rules based on metadata (genre, BPM, era)

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
