# DJ Library Manager

**Automated DJ library organization system with Rekordbox integration, audio analysis, and ML-powered categorization.**

## 🎯 What This Does

Smart workflow manager for DJs that:

- ✅ **Scans UNSORTED folder** → validates Rekordbox analysis → generates `unsorted.xlsx` staging area
- ✅ **Extracts audio features** with Essentia (BPM, key, energy, spectral features) → SQLite cache for ML training
- ✅ **Multi-source metadata enrichment** (Beatport, MusicBrainz, Last.fm, SoundCloud) → genre suggestions & popularity metrics
- ✅ **Manual curation workflow** via Excel → edit metadata, assign buckets, mark `done = TRUE`
- ✅ **Cleans spam metadata** → removes piracy tags (musicdjs.club, chomikuj.pl) while preserving DJ software data (Traktor/Serato cues)
- ✅ **Safe file operations** → moves approved tracks to library folders with undo support
- ✅ **ML dataset export** → combines Rekordbox tags + Essentia features for training genre/bucket models

**Key innovation:** Treats Rekordbox as source of truth for BPM/Key while using Essentia for rich ML features (spectral, MFCC, chroma). No tag conflicts, one workflow.

## 🚀 Quick Start

### Prerequisites

- macOS (tested) or Linux
- Python 3.11+ (3.13 recommended)
- **Rekordbox 6** installed (for DB integration)
- **Essentia** (optional, for audio analysis) - see `docs/INSTALL.md`

### Installation

```bash
# 1. Clone repo
git clone https://github.com/Sztuka/dj-library-manager.git
cd dj-library-manager

# 2. Setup environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .

# 3. Configure library paths + metadata sources
python -m djlib.cli configure
# Sets library_root, inbox_dir
# Optionally: configure Beatport (EDM genres/artwork)

# 4. (Optional) Install Essentia for audio analysis
python scripts/install_essentia.py

# 5. Verify setup
python -m djlib.cli analyze-audio --check-env
```

**Metadata Sources** (optional, for enrichment):

- **Beatport**: Run `python -m djlib.cli setup-beatport` for EDM genres + 1400x1400 artwork
- **SoundCloud**: Works out-of-box (auto-refresh client_id)
- **MusicBrainz/Last.fm**: No setup required

### 5-Step Workflow

**1. PREPARE FILES IN REKORDBOX**

```bash
# Import tracks to Rekordbox collection
# Analyze tracks (Right-click → Analyze)
# Enable: Preferences → Write metadata to files
```

**2. SCAN UNSORTED FOLDER** (with Rekordbox validation)

```bash
# Strict mode: ONLY accept Rekordbox-analyzed files
python -m djlib.cli scan --strict

# Or use VS Code task: "WORKFLOW 1 — Scan UNSORTED"
# Creates unsorted.xlsx with metadata staging area
```

**3. ANALYZE AUDIO FEATURES** (optional, for ML training)

```bash
python -m djlib.cli analyze-audio
# Or task: "WORKFLOW 2 — Analyze audio (Essentia)"
# Extracts 50+ features → LOGS/audio_analysis.sqlite
```

**4. CURATE IN EXCEL**

- Open `unsorted.xlsx`
- Fill: `artist`, `title`, `genre`, `target_subfolder`
- Mark ready tracks: `done = TRUE`
- Save and close

**5. EXPORT APPROVED TRACKS**

```bash
python -m djlib.cli apply
# Or task: "WORKFLOW 4 — Export approved tracks"
# Cleans spam tags (musicdjs.club, chomikuj.pl, etc.)
# Preserves DJ software data (PRIV/Traktor cues, GEOB/Serato markers, POPM/ratings, APIC/artwork)
# Moves done=TRUE files → library folders
# Updates library.csv with metadata
```

### Optional: ML Dataset Export

```bash
python -m djlib.cli ml-export-training-dataset
# Task: "WORKFLOW 5 — ML dataset export"
# Creates data/training_dataset_full.csv with:
# - tag_bpm/tag_key_camelot (from Rekordbox)
# - ess_bpm/ess_key_camelot/ess_energy (from Essentia)
# - 50+ spectral/MFCC/chroma features for training
```

---

## 🏗️ Architecture Overview

### Rekordbox-First Design

**Problem:** BPM/Key detection conflicts between DJ software (Rekordbox, Traktor, Essentia)

**Solution:** Two-tier system:

1. **Rekordbox = Source of Truth** for BPM/Key in library workflow
   - `scan --strict` enforces Rekordbox DB confirmation
   - Tags (TBPM/TKEY) must be present before processing
2. **Essentia = ML Feature Cache** for training only
   - Stores algorithmic BPM/Key + rich audio features
   - Never writes to tags (cache-only)
   - ML models can compare professional (Rekordbox) vs algorithmic (Essentia)

### Data Flow

```
UNSORTED/
  ↓ scan --strict (check Rekordbox DB)
unsorted.xlsx (staging area)
  ↓ manual curation (Excel)
  ↓ apply (move done=TRUE files)
LIBRARY/
  CLUB/
  OPEN FORMAT/
  REVIEW QUEUE/
library.csv (master database)
  ↓ ml-export-training-dataset
data/training_dataset_full.csv (ML training)
```

### Key Modules

| Module                            | Purpose                | Key Features                             |
| --------------------------------- | ---------------------- | ---------------------------------------- |
| `djlib/rekordbox_status.py`       | Rekordbox integration  | DB queries, tag validation, strict mode  |
| `djlib/audio/essentia_backend.py` | Audio analysis         | BPM/Key/Energy + 50+ features → SQLite   |
| `djlib/tag_cleaner.py`            | ID3 tag cleaning       | Removes spam, preserves DJ software data |
| `djlib/metadata/coverart.py`      | Album artwork fetching | 4-source fallback, rate limiting, APIC   |
| `djlib/metadata/beatport.py`      | Beatport integration   | JWT auto-refresh, EDM genres, 1400px art |
| `djlib/ml/export_dataset.py`      | ML dataset builder     | Combines Rekordbox tags + Essentia cache |
| `djlib/enrich.py`                 | Metadata enrichment    | Beatport, MusicBrainz, Last.fm, SC APIs  |
| `djlib/cli.py`                    | Command-line interface | All workflows + VS Code tasks            |

---

## 📊 Key Features

### 1. Rekordbox Integration (`djlib/rekordbox_status.py`)

**Two detection methods:**

- `was_analyzed_from_db()` - Queries Rekordbox database (authoritative)
- `was_analyzed_from_tags()` - Reads TBPM/TKEY from ID3 tags (fallback)

**Strict Mode** (recommended for UNSORTED):

```bash
# Enforce Rekordbox DB confirmation
python -m djlib.cli scan --strict
# Rejects files with only Traktor/Serato tags
```

**Normal Mode** (flexible, for moved files):

```bash
# Accept DB OR tags from any DJ software
python -m djlib.cli scan
# Works after file moves (DB paths become stale)
```

See `docs/REKORDBOX_INTEGRATION.md` for scenarios and troubleshooting.

### 2. Audio Analysis (Essentia)

**Cache-only approach** (no tag writes):

- BPM detection with histogram peak selection (100-120 "sweet spot" priority)
- Key detection in Camelot notation (1A-12B)
- Energy, danceability, mood, voice/instrumental detection
- Spectral features (centroid, rolloff, flux, complexity)
- MFCC coefficients (timbre analysis)
- Chroma features (harmonic content)

**Storage:** `LOGS/audio_analysis.sqlite` (JSON blobs per track)

**Commands:**

```bash
# Analyze all unsorted tracks
python -m djlib.cli analyze-audio

# Force recompute specific file
python -m djlib.cli analyze-audio --recompute --path "track.mp3"

# Check environment
python -m djlib.cli analyze-audio --check-env
```

### 3. Audio Tag Cleaning (`djlib/tag_cleaner.py`)

**Automatic spam removal** during WORKFLOW 4 (export):

**Removes piracy metadata:**

- Publisher tags from file sharing sites (TPUB: musicdjs.club, chomikuj.pl)
- Comment spam (COMM: www.p2pdl.com, www.mp3baza.pl, ulub.pl)
- Useless tags (MCDI, TPOS, SYLT, USLT, WCOM, WOAF, etc.)

**Preserves critical DJ software data:**

- **PRIV** (Traktor: cue points, loops, beatgrids)
- **GEOB** (Serato: markers, analysis, autotags, beatgrid offsets)
- **POPM** (Popularimeter: ratings/stars in Traktor/Rekordbox/Windows Media Player)
- **APIC** (Album artwork for visual identification)

**Smart detection:**

- Tag values scanned for spam keywords
- COMM/TPUB only removed if contain spam URLs
- TXXX (custom tags) preserved if DJ software related

**Statistics in output:**

```
🧹 Czyszczenie spam tagów: cleaned=6, errors=0
📀 Zapis tagów audio: ok=28, errors=0
```

### 4. Multi-Source Metadata Enrichment

**Sources** (with quality weights):

- **Beatport** (weight 10.0) - **gold standard for EDM**, 100+ precise subgenres (progressive house, melodic techno, afro house)
- **Last.fm** (weight 6.0) - genre tags, popularity
- **MusicBrainz** (weight 3.0) - canonical genres
- **SoundCloud** (weight 2.0, optional) - user tags

**New columns in `unsorted.xlsx`:**

- `genres_beatport`, `genres_musicbrainz`, `genres_lastfm`, `genres_soundcloud` (raw lists)
- `genre_suggest` (weighted fusion with Beatport priority)
- `pop_playcount`, `pop_listeners` (popularity metrics)

**Auto-refresh authentication** (no manual token extraction):

- **Beatport**: JWT token auto-refreshes via Playwright (~10s per hour), credentials in system keyring
- **SoundCloud**: client_id auto-refreshes via Playwright (~2s per 30 days), cached locally

**Commands:**

```bash
# Enrich all tracks (Beatport, MusicBrainz, Last.fm, SoundCloud)
python -m djlib.cli enrich-online

# Setup Beatport credentials (one-time, stored in system keyring)
python -m djlib.metadata.beatport --setup

# Force refresh + skip SoundCloud
python -m djlib.cli enrich-online --force-genres --skip-soundcloud

# Fetch album artwork (4-source fallback with Beatport 1400x1400)
python -m djlib.cli enrich-online --fetch-covers
```

### 5. Album Artwork Fetching (`djlib/metadata/coverart.py`)

**4-source fallback chain** (by quality):

1. **MusicBrainz Cover Art Archive** (500px front cover, best for general music)
2. **Beatport dynamic URI** (1400x1400, **gold standard for EDM** - highest resolution)
3. **Last.fm album.getInfo** (300x300 extralarge, medium quality)
4. **SoundCloud track artwork** (up to 500x500, for niche/unreleased tracks)

**Features:**

- Only adds artwork if APIC frame is missing (never overwrites)
- Respects rate limits (MB/Beatport: 1 req/s, Last.fm: 5 req/s, SoundCloud: 2 req/s)
- Automatic format detection (JPEG/PNG)
- Integration with workflow 3 enrichment
- Beatport provides best quality for electronic music (1400x1400 vs 500px)

**Statistics in output:**

```
🎨 Okładki: added=15, skipped=8, failed=2
```

### 4. ML Training Dataset

**Feature combination:**

```csv
track_id,file_path,
tag_bpm,tag_key_camelot,          # From Rekordbox (source of truth)
ess_bpm,ess_key_camelot,ess_energy, # From Essentia (algorithmic)
ess_spectral_centroid,ess_spectral_rolloff,...  # 50+ features
genre_label,bucket_label          # Training labels
```

**Use cases:**

- Train genre classifier (compare Rekordbox vs Essentia BPM)
- Bucket prediction (auto-assign CLUB/OPEN FORMAT/etc)
- Energy-based recommendations
- Detect analysis discrepancies (manual correction needed)

**Command:**

```bash
python -m djlib.cli ml-export-training-dataset \
  --out data/training_full.csv \
  --require-both-labels  # Skip tracks without genre+bucket
```

See `docs/ML_PIPELINE.md` for training examples.

---

## 🎛️ VS Code Tasks

Use built-in tasks for common workflows:

| Task                      | Command                   | Description                            |
| ------------------------- | ------------------------- | -------------------------------------- |
| **STEP 0**                | Setup venv & deps         | One-time installation                  |
| **TOOLS**                 | Check audio env           | Verify Essentia installation           |
| **WORKFLOW 1**            | Scan UNSORTED             | `scan --strict` (Rekordbox validation) |
| **WORKFLOW 1 (flexible)** | Scan UNSORTED (no strict) | `scan` (accept any tags)               |
| **WORKFLOW 2**            | Analyze audio (Essentia)  | Extract features → cache               |
| **WORKFLOW 3**            | Enrich online             | Fetch metadata from APIs               |
| **WORKFLOW 4**            | Export approved tracks    | Move `done=TRUE` → library             |
| **WORKFLOW 5**            | ML dataset export         | Build training CSV                     |
| **TESTS**                 | Run tests / coverage      | Validate changes                       |

---

## 📁 Project Structure

```
dj-library-manager/
├── djlib/                    # Core Python package
│   ├── cli.py               # Command-line interface
│   ├── rekordbox_status.py  # Rekordbox DB integration
│   ├── audio/
│   │   ├── essentia_backend.py  # Audio analysis
│   │   └── cache.py         # SQLite feature storage
│   ├── ml/
│   │   └── export_dataset.py    # Training data builder
│   ├── metadata/
│   │   ├── beatport.py      # Beatport API (JWT auto-refresh)
│   │   ├── coverart.py      # Album artwork fetching
│   │   ├── lastfm.py        # Last.fm API
│   │   ├── mb_client.py     # MusicBrainz API
│   │   └── soundcloud.py    # SoundCloud API (client_id auto-refresh)
│   └── ...
├── docs/
│   ├── REKORDBOX_INTEGRATION.md  # DB integration guide
│   ├── ARCHITECTURE.md           # System design
│   ├── ML_PIPELINE.md            # Training workflows
│   └── INSTALL.md                # Setup instructions
├── tests/                    # Unit & integration tests
├── scripts/                  # Utility scripts
├── .vscode/tasks.json       # VS Code task definitions
├── requirements.txt         # Python dependencies
├── config.yml               # Configuration template
└── README.md                # This file
```

---

## 🔧 Configuration

### Initial Setup

First run triggers interactive wizard:

```bash
python -m djlib.cli scan
# Prompts for:
# - Library root (default: ~/Music Library)
# - Inbox dir (default: ~/Unsorted)
```

Saves to `config.local.yml`:

```yaml
library_root: /Volumes/Music/Library
inbox_dir: /Volumes/Music/INBOX_UNSORTED
csv_path: library.csv

# Optional API keys
lastfm_api_key: YOUR_KEY
soundcloud_client_id: YOUR_KEY
```

**Marker files** for auto-detection:

- `.djlib_root` in library folder
- `.djlib_inbox` in inbox folder

### Rekordbox Settings

**Critical:** Enable tag writing in Rekordbox:

```
Preferences → Advanced → Browse
☑ Write metadata to files
Frequency: Every time
```

Without this, `scan --strict` will fail after file moves (DB has old paths).

---

## 🧪 Testing

```bash
# Run all tests
pytest -q

# With coverage report
pytest --cov=djlib --cov-report=term-missing

# Or use VS Code tasks:
# - "TESTS — run"
# - "TESTS — coverage"
```

**Key test files:**

- `tests/test_rekordbox_status.py` - DB integration scenarios
- `tests/test_audio_basic.py` - Essentia feature extraction
- `tests/test_enrich_artist_normalization.py` - Special artist handling (AC/DC, ABBA, etc)

---

## 📚 Documentation Index

| Topic                     | File                            | Description                                           |
| ------------------------- | ------------------------------- | ----------------------------------------------------- |
| **Setup & Installation**  | `docs/INSTALL.md`               | Essentia installation, dependencies                   |
| **Rekordbox Integration** | `docs/REKORDBOX_INTEGRATION.md` | DB queries, strict mode, scenarios                    |
| **Architecture**          | `docs/ARCHITECTURE_EN.md`       | Modules, data flow, design decisions (English)        |
| **Roadmap**               | `docs/ROADMAP_EN.md`            | Development plan: Rekordbox + Essentia + ML (English) |
| **ML Pipeline**           | `docs/ML_PIPELINE.md`           | Training workflows, feature engineering               |

---

## 🤝 Contributing

### For LLMs / Code Assistants

**Key context files to read:**

1. `djlib/rekordbox_status.py` - Understand Rekordbox-first design
2. `djlib/audio/essentia_backend.py` - Audio analysis architecture
3. `djlib/ml/export_dataset.py` - ML feature combination
4. `docs/REKORDBOX_INTEGRATION.md` - Scenarios & edge cases

**Common tasks:**

- Adding new Essentia features → modify `essentia_backend.py` + `export_dataset.py`
- Improving BPM detection → tune histogram peak selection weights
- New metadata source → follow `djlib/metadata/lastfm.py` pattern
- ML model integration → use `data/training_dataset_full.csv`

### Architecture Principles

1. **Rekordbox = Source of Truth** for BPM/Key in library (never overwrite tags)
2. **Essentia = Cache Only** for ML training (no tag writes, no xlsx updates)
3. **Graceful Degradation** (works without DB, without Essentia, without API keys)
4. **Undo Support** for all file operations (logs in `LOGS/moves-*.csv`)
5. **Type Safety** (Python 3.11+ type hints, validate with mypy)

---

## 🐛 Troubleshooting

### "Files not detected as analyzed"

```bash
# Check tags
python -c "from mutagen.id3 import ID3; print(ID3('track.mp3').get('TBPM'), ID3('track.mp3').get('TKEY'))"

# Check DB status
python -c "from djlib.rekordbox_status import debug_print_db_status; debug_print_db_status()"

# Solution: Ensure Rekordbox "Write metadata" is enabled, re-analyze
```

### "Strict mode rejects my files"

```bash
# Option 1: Use normal mode (accepts tags from any source)
python -m djlib.cli scan

# Option 2: Import to Rekordbox, analyze, then re-run
python -m djlib.cli scan --strict
```

### "After moving files, detection fails"

**Expected behavior** - DB tracks by path, which changes after moves.

**Solution:** Use normal mode (tags travel with files):

```bash
python -m djlib.cli scan  # no --strict
```

### "Essentia analysis missing"

```bash
# Check environment
python -m djlib.cli analyze-audio --check-env

# Force recompute
python -m djlib.cli analyze-audio --recompute
```

See `docs/REKORDBOX_INTEGRATION.md` for detailed scenarios.

---

## 📝 License

MIT License - see LICENSE file

---

## 🎵 Credits

**Audio Analysis:**

- [Essentia](https://essentia.upf.edu/) - Music analysis library
- [Chromaprint](https://acoustid.org/chromaprint) - Audio fingerprinting

**Metadata Sources:**

- [MusicBrainz](https://musicbrainz.org/) - Music metadata database
- [Last.fm](https://www.last.fm/) - Genre tags & popularity
- [SoundCloud](https://soundcloud.com/) - User-generated tags

**Rekordbox Integration:**

- [pyrekordbox](https://github.com/dylanljones/pyrekordbox) - Rekordbox DB reader

---

**Built for DJs who want:** Smart organization → Clean library → More time mixing 🎧
