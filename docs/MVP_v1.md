# MVP v1 — DJ Library Manager

**Status:** Released (January 2026)

---

## Definition

MVP v1 is the first stable, end-to-end workflow for organizing a DJ library with Rekordbox/Traktor integration, Excel-based curation, and optional audio analysis. It prioritizes clean metadata, deterministic file organization, and reliable DJ software sync.

---

## Scope (Included)

### Core Workflow

1. **Scan** UNSORTED folder → generate `data/unsorted.xlsx`
2. **Enrich** metadata online (Beatport, MusicBrainz, Last.fm, SoundCloud)
3. **Curate** in Excel (artist/title/genre/year + destination + done)
4. **Apply** → move files, write tags, embed cover art, sync DJ software
5. **Undo** → revert last export if needed

### Metadata & Tagging

- Canonical genre system (`genres.yml`, 30 genres)
- Genre resolution with weighted sources + specificity boost
- Album tag always cleared
- Custom cover art embedded from local file
- Spam tag cleaning (preserve DJ software tags)

### DJ Software Integration

- Rekordbox DB sync (read IDs, write paths/metadata)
- Traktor collection sync (read IDs, write paths/metadata)
- Persistent DJLIB\_\* tags for identity

### Audio Analysis (Optional)

- Essentia feature extraction
- SQLite cache (`LOGS/audio_analysis.sqlite`)
- ML dataset export (`data/training_dataset_full.csv`)

---

## Non‑Goals (Explicitly Out of Scope)

- Smart playlist / bucket classification
- ML genre prediction in production workflow
- Full automated metadata decisions without manual Excel review
- Multi-user or cloud sync
- Windows support

---

## Folder Model

**Destinations:**

- `library` → organized by artist
- `reject` → flat folder
- `archive` → organized by artist
- `mixes` → flat folder under library

---

## CLI Commands (MVP v1)

- `configure`
- `scan [--strict]`
- `enrich-online [--force-genres]`
- `apply [--dry-run]`
- `undo`
- `sync-dj-libraries [--write]`
- `analyze-audio [--check-env]`
- `ml-export-training-dataset`

---

## Known Constraints

- Rekordbox must be closed for write operations
- Excel file must be closed before `apply`
- Requires macOS/Linux and Rekordbox 6
- Beatport requires credentials

---

## Success Criteria

- Deterministic, repeatable file organization
- Accurate metadata (artist/title/genre/year)
- No album tags, consistent cover art
- Rekordbox/Traktor databases kept in sync
- Human-in-the-loop curation via Excel

---

## Documentation

- Main README: ../README.md
- Architecture: ARCHITECTURE.md
- Install: INSTALL.md
- Future work: possible_upgrades.md
