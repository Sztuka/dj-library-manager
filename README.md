# DJ Library Manager

**Automated DJ library organization with Rekordbox/Traktor integration, multi-source metadata enrichment, and audio analysis.**

**Version:** MVP v1.0 (January 2026)

---

## What This Does

A smart library workflow for DJs that:

- ✅ **Scans UNSORTED folder** → validates Rekordbox/Traktor analysis → generates `data/unsorted.csv` staging area
- ✅ **Multi-source metadata enrichment** (Beatport, MusicBrainz, Last.fm, SoundCloud) → canonical genre resolution
- ✅ **Manual curation workflow** via Review UI → edit metadata, select destination, mark `done = TRUE`
- ✅ **Cleans metadata** → removes spam tags, clears album field, embeds custom cover art
- ✅ **Safe file operations** → moves approved tracks to organized structure with undo support
- ✅ **DJ software sync** → automatic Rekordbox/Traktor database updates (paths, IDs, metadata)
- ✅ **Audio analysis** → Essentia feature extraction for ML training datasets

---

## File Locations

| What                    | Path                | Notes                       |
| ----------------------- | ------------------- | --------------------------- |
| **Staging spreadsheet** | `data/unsorted.csv` | CSV file in project folder  |
| **Unsorted music**      | `~/Music Unsorted/` | Tracks pending processing   |
| **Music library**       | `~/Music Library/`  | Organized, approved tracks  |
| **Rejected tracks**     | `~/Music Rejected/` | Tracks marked for rejection |
| **Archive**             | `~/Music Archive/`  | Archived tracks by artist   |
| **Library database**    | `data/library.csv`  | Master track database       |

---

## Quick Start

### Prerequisites

- macOS (tested) or Linux
- Python 3.11+ (3.13 recommended)
- **Rekordbox 6** installed (for database integration)
- **Essentia** (optional, for audio analysis)

### Installation

```bash
# 1. Clone and setup
git clone https://github.com/Sztuka/dj-library-manager.git
cd dj-library-manager
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .

# 2. Configure library paths
python -m djlib.cli configure

# 3. (Optional) Setup legacy metadata helpers
python -m djlib.cli setup-beatport  # Optional; not required for enrich-online

# 4. (Optional) Install Essentia for audio analysis
python scripts/install_essentia.py
python -m djlib.cli analyze-audio --check-env
```

See [docs/INSTALL.md](docs/INSTALL.md) for detailed installation instructions.

---

## Workflow

### WORKFLOW 0: Sync DJ Libraries (Optional)

Synchronizes `library.csv` with Rekordbox/Traktor databases. Run periodically to keep everything in sync.

```bash
# Dry-run (preview):
python -m djlib.cli sync-dj-libraries

# Apply changes:
python -m djlib.cli sync-dj-libraries --write
```

### WORKFLOW 1: Scan UNSORTED

Scans your inbox folder, validates Rekordbox analysis, and creates the staging spreadsheet.

```bash
# Strict mode (requires Rekordbox DB confirmation):
python -m djlib.cli scan --strict

# Flexible mode (accepts tag-only files):
python -m djlib.cli scan
```

### WORKFLOW 2: Enrich Metadata

Fetches metadata from online sources (Beatport, MusicBrainz, Last.fm, SoundCloud).

```bash
python -m djlib.cli enrich-online
```

**Options:**

- `--force-genres` — Refresh genre data even if already filled
- `--skip-soundcloud` — Skip SoundCloud source

### Manual Curation: Review UI

1. Run `python -m djlib.cli review` to launch the Review UI
2. Review and edit: `artist`, `title`, `genre`, `year`, `destination`
3. Select destination: `library`, `reject`, `archive`, or `mixes`
4. Set `done = TRUE` for approved tracks
5. Changes are saved automatically

**Genre dropdown:** Uses 30 canonical genres from `genres.yml`

**AI features (right-click context menu):**

- **Suggest genre (AI)** — one-shot genre classification
- **Identify track (AI)** — identify artist/title from filename and metadata
- **AI Chat** — conversational AI for correcting mashups, challenging genre, refining metadata

### WORKFLOW 3: Export Approved Tracks

Moves approved tracks to their destinations, writes tags, and syncs with DJ software.

```bash
python -m djlib.cli apply

# Preview only:
python -m djlib.cli apply --dry-run
```

**What happens:**

1. Moves files to destination folders (by artist)
2. Renames to format: `Artist - Title (Version) [Key BPM].ext`
3. Writes clean metadata (artist, title, genre, year)
4. Embeds custom cover art
5. Updates Rekordbox/Traktor databases
6. Adds to `library.csv`

### WORKFLOW 4: Audio Analysis (Optional)

Extracts audio features with Essentia for ML training.

```bash
python -m djlib.cli analyze-audio
```

### WORKFLOW 5: ML Dataset Export (Optional)

Exports training dataset combining library metadata with Essentia features.

```bash
python -m djlib.cli ml-export-training-dataset
```

### Undo Last Export

```bash
python -m djlib.cli undo
```

---

## Metadata Sources

| Source          | Data Provided        | Weight       | Setup                    |
| --------------- | -------------------- | ------------ | ------------------------ |
| **Web Search + AI** | Genre + release year | Primary      | OpenAI API key           |
| **Last.fm**         | Tags, play counts    | Supporting   | No setup needed          |
| **MusicBrainz**     | Year, album, artist  | Supporting   | No setup needed          |
| **Beatport**        | Optional legacy/API  | Optional     | `setup-beatport` command |
| **SoundCloud**      | Optional URL scrape  | Optional     | Auto-configured          |

**Genre Resolution:** `enrich-online` uses the production `nano+WS+LF` classifier (web search + Last.fm + OpenAI), with MusicBrainz/Last.fm as supporting metadata sources.

---

## Folder Structure

### Output Structure

```text
~/Music Library/
├── Artist Name/
│   ├── Artist - Track A (Remix) [5A 128].mp3
│   └── Artist - Track B [2B 125].flac
└── MIXES/
    └── DJ Mix Name [128].mp3

~/Music Rejected/
└── track.mp3  (flat structure)

~/Music Archive/
└── Artist Name/
    └── Artist - Old Track [1A 110].mp3
```

### Project Structure

```text
dj-library-manager/
├── djlib/                    # Main package
│   ├── cli.py                # CLI entry point (argparse)
│   ├── config.py             # Paths, settings, YAML config loader
│   ├── csvdb.py              # CSV read/write operations
│   ├── enrich.py             # AcoustID / enrichment orchestration
│   ├── fingerprint.py        # fpcalc audio fingerprinting wrapper
│   ├── genre_canonical.py    # Canonical genre resolution
│   ├── genre_mapper.py       # genres.yml → label mapping
│   ├── tags.py               # Audio tag reading/writing (mutagen)
│   ├── external_sync.py      # Rekordbox / Traktor sync
│   ├── audio/                # Essentia feature extraction + SQLite cache
│   ├── metadata/             # API clients
│   │   ├── lastfm.py         # Last.fm tags + track info
│   │   ├── mb_client.py      # MusicBrainz recording/artist lookup
│   │   ├── soundcloud.py     # SoundCloud search
│   │   └── web_search.py     # SearXNG web search + entity extraction
│   ├── ml/                   # ML dataset export (pandas)
│   └── review/               # Flask Review UI (port 8899)
│       ├── server.py          # API endpoints + HTML serving
│       ├── static/            # app.js, style.css (vanilla JS, no build)
│       └── templates/         # index.html (Jinja2)
├── scripts/                  # Standalone scripts and AB tests
│   ├── ab_test_genre.py      # Genre classifier AB test framework
│   └── ...                   # ~30 utility / debug scripts
├── data/
│   ├── unsorted.csv          # Staging area (tracks pending review)
│   ├── library.csv           # Master database (~30 fields per track)
│   └── ab_test/              # Gold-labeled test set + results.json
├── LOGS/                     # Move logs, scan status (gitignored)
├── genres.yml                # ~60 canonical genres with families + synonyms
├── config.yml                # Default config (paths, API keys)
├── config.local.yml          # Local overrides (gitignored)
└── docs/                     # Architecture, roadmaps, analysis docs
```

---

## Configuration Files

| File               | Purpose                                                |
| ------------------ | ------------------------------------------------------ |
| `config.local.yml` | Local paths and settings (gitignored)                  |
| `genres.yml`       | ~60 canonical genres with families, also_in, synonyms  |

---

## VS Code Tasks

The project includes VS Code tasks for common operations:

| Task                             | Description                          |
| -------------------------------- | ------------------------------------ |
| `STEP 0 — Setup`                 | Create venv and install dependencies |
| `STEP 1 — Configure`             | Configure library paths              |
| `WORKFLOW 0 — Sync DJ Libraries` | Sync with Rekordbox/Traktor          |
| `WORKFLOW 1 — Scan UNSORTED`     | Scan inbox folder                    |
| `WORKFLOW 2 — Enrich online`     | Fetch online metadata                |
| `WORKFLOW 3 — Export approved`   | Export approved tracks               |
| `WORKFLOW 4 — Analyze audio`     | Essentia analysis                    |
| `WORKFLOW 5 — ML dataset export` | Export training dataset              |
| `TESTS — run`                    | Run test suite                       |

---

## Documentation

- [Installation Guide](docs/INSTALL.md) — Detailed setup instructions
- [Architecture](docs/ARCHITECTURE.md) — Technical documentation
- [Possible Upgrades](docs/possible_upgrades.md) — Future enhancements

---

## Requirements

See `requirements.txt` for Python dependencies. Key packages:

- `mutagen` — Audio tag reading/writing
- `openpyxl` — Excel file handling
- `pyrekordbox` — Rekordbox database access
- `requests-cache` — API response caching
- `essentia` (optional) — Audio analysis

---

## License

Private project. Not for distribution.
