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
8. [Review UI](#review-ui)
9. [Technical Details](#technical-details)

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
│   ├── unsorted.csv            # Staging CSV
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

### unsorted.csv (Staging CSV)

CSV file with all pending tracks. Key columns:

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

Multi-source weighted voting system in `djlib/metadata/genre_resolver.py`.

**Architecture (post P1-P3 refactor):**

```text
resolve(artist, title, version, *, sources=ALL_SOURCES)
  ├── _detect_remix(version, title, artist) → bool
  ├── _score_beatport()   → Optional[SourceScore]  (early exit if specific EDM)
  ├── _score_lastfm()     → Optional[SourceScore]
  ├── _score_musicbrainz() → Optional[SourceScore]
  ├── _score_soundcloud()  → Optional[SourceScore]
  └── _rank(scores)        → GenreResolution(main, subs, confidence, breakdown)
```

**Sources and weights:**

| Source      | Weight | Data                       | Control                  |
| ----------- | ------ | -------------------------- | ------------------------ |
| Beatport    | 10     | EDM genres (authoritative) | `sources={"beatport"}`   |
| Last.fm     | 6      | Tags from user community   | `sources={"lastfm"}`     |
| MusicBrainz | 3      | Structured genre data      | `sources={"mb"}`         |
| SoundCloud  | 2-8    | Genre tags (remix boost)   | `sources={"soundcloud"}` |

`ALL_SOURCES = frozenset({"beatport", "lastfm", "mb", "soundcloud"})`

**Key types:**

- `SourceScore(source, weight, tags)` — per-source scoring result
- `GenreResolution(main, subs, confidence, breakdown)` — final result

**Scoring pipeline (per tag):**

```text
raw tag → canonical() → _is_noise()? → _downweight_factor() → _specificity_boost()
                ↓              ↓
            ALIASES map    _NOISE_TERMS filter (validated at import vs genres.yml)
```

**Specificity Boost:** Subgenres get 1.5-2.0x multiplier over generic parents.

**Lazy loading:** `genres.yml` parsed via `@lru_cache` — no module-level I/O.

**Test coverage:** 54 tests in `tests/test_genre_resolver.py`:

- Pure function units: canonical, \_is_noise, \_downweight, \_specificity_boost, \_detect_remix
- Mocked integration: resolve() with mocked API fetchers
- Golden-file regression: known tracks with expected genre

### Canonical Genres (genres.yml)

50 canonical genres across 8 categories with ~680 lines of synonyms:

```yaml
AFRO_HOUSE:
  label: "Afro House"
  category: electronic
  boost: 1.8
  synonyms:
    - "afro house"
    - "afro tech"
    - "tribal afro house"
    # ...
```

Categories: `electronic`, `rock`, `pop`, `urban`, `caribbean`, `world`, `jazz`, `other`

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

### ML Genre Classification Pipeline

**Status:** Infrastructure ready, training not implemented yet.
**Roadmap:** See [ML_GENRE_CLASSIFICATION_ROADMAP.md](ML_GENRE_CLASSIFICATION_ROADMAP.md)

Pipeline flow:

```text
audio files → Essentia (~80 features) → SQLite cache
                                              ↓
library.csv (genre labels) ──────────→ training dataset CSV
                                              ↓
                                    LightGBM training (TODO)
                                              ↓
                                    genre_model.pkl → _score_essentia()
```

Key modules:

- `djlib/audio/essentia_backend.py` — feature extraction
- `djlib/audio/cache.py` — SQLite analysis cache
- `djlib/ml/export_dataset.py` — joins features + labels
- `djlib/ml/models.py` — model config placeholders
- `djlib/ml/train.py` — training code (TODO)

Goal: Replace API-based genre resolution with audio-based ML classification.

---

## Review UI

Flask-based single-page application for curating unsorted tracks.

- **Server:** `djlib/review/server.py` (port 8899)
- **Frontend:** Vanilla JS + CSS (no frameworks, no build step)
- **Templates:** Jinja2 (`djlib/review/templates/index.html`)

### API Endpoints

| Method | Endpoint                 | Purpose                                              |
| ------ | ------------------------ | ---------------------------------------------------- |
| GET    | `/api/tracks`            | List tracks (`?source=unsorted\|library\|processed`) |
| POST   | `/api/tracks/update`     | Update track fields in CSV                           |
| GET    | `/api/genres`            | List canonical genres from `genres.yml`              |
| GET    | `/api/library-index`     | Track IDs present in `library.csv`                   |
| GET    | `/api/ai-status`         | Check if OpenAI API key is configured                |
| POST   | `/api/suggest-genre`     | AI genre suggestion for a track (one-shot)           |
| POST   | `/api/identify-track`    | AI track identification from filename/metadata       |
| POST   | `/api/ai-chat`           | Conversational AI chat for metadata refinement       |
| POST   | `/api/enrich-track`      | Re-enrich track from online sources                  |
| POST   | `/api/swap-artist-title` | Swap artist/title and re-parse from filename         |

### AI Chat (`/api/ai-chat`)

Conversational endpoint for iterative metadata correction with **web search**.

- **API backend:** OpenAI Responses API (`/v1/responses`) with `web_search_preview` tool
- **Model:** `gpt-4o-mini` — cost-effective, supports web search and tool use
- **Web search:** Enabled by default. The model decides when to search based on context:
  - Track cannot be confidently identified from metadata alone
  - User explicitly asks to search or look up a track
  - Verification of release year, remix credits, or artist spelling is needed
- **Request:** `{ "track_id": "...", "message": "..." }` or `{ "track_id": "...", "reset": true }`
- **Response:** `{ "reply": "...", "suggestion": { ... } | null, "history_length": N, "web_search": true, "sources": [{"url": "...", "title": "..."}] }`
  - `web_search` and `sources` only present when the model used web search
- **Sessions:** Per-track conversation stored in memory with TTL (1 hour) and LRU eviction (max 100 sessions)
- **Stale prompt refresh:** System prompt is rebuilt from current CSV data on every request, so edits via the table are reflected immediately
- **Suggestion blocks:** AI outputs ` ```suggestion ` fenced JSON, parsed and returned separately
- **Field mapping:** `version` in AI output is normalized to `version_info` for CSV compatibility
- **Track deletion guard:** If a track is removed while chatting, session is cleaned up and 404 returned
- **Frontend features:**
  - Quick prompt buttons (Identify, Genre?, Mashup?, Fix names, **Search online**) — shown on first open, hidden after first message
  - 🔍 web search badge on AI replies that used web search
  - Clickable source citations (Beatport, Discogs, etc.) under AI replies
  - Current→suggested diff display in suggestion blocks (strikethrough old value)
  - Draggable panel (grab header to reposition)
  - Minimize/restore (─ button in header)
  - Keyboard shortcut: `Ctrl/Cmd+K` to toggle panel

**Note:** The `/api/identify-track` and `/api/suggest-genre` endpoints still use the Chat Completions API (`/v1/chat/completions`) without web search, as they are one-shot calls where the model's training data is sufficient.

### AI Naming Conventions (in system prompt)

- **Mashups/Edits:** Edit creator = artist, combined track names = title, "Edit" = version  
  Example: `Loup Musa - Ethnica x We Dem Boyz (Edit)`
- **Remixes:** Original artist = artist, remixer name in version  
  Example: `Original Artist - Title (Remixer Remix)`
- **Featuring:** `feat.` goes in the title, not the artist field
- **Title Case** for all names and titles

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
