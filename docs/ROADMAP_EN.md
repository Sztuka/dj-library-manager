# DJ Library Manager — Roadmap (Rekordbox + Essentia, Non-Commercial)

This document describes the roadmap for Rekordbox-first architecture with Essentia audio analysis as cache/alternative source for BPM and Key, extended with Energy and low-level features. We assume non-commercial mode (private / potentially open-source in the future), optimizing for quality without copyleft restrictions.

**Note on Bucketing (November 2025):** The bucketing system (READY TO PLAY/CLUB/etc.) has been removed from folder organization. Current focus is on clean library structure (Main Library by artist, Reject, Archive). Bucketing/playlist generation features described in this document are **FUTURE ENHANCEMENTS** - they will be built as smart playlists on top of the existing clean structure, not as folder organization.

## 1) Assumptions and Goals (Updated November 2025)

- **Rekordbox as Source of Truth**: BPM and Key from Rekordbox DB (TBPM/TKEY tags) take priority in UNSORTED folder
- **Strict Mode Enforcement**: `scan --strict` requires Rekordbox DB confirmation for quality control
- **Essentia as Cache/Alternative**: Local metrics (BPM/Key/Energy) cached for ML training and analysis
- **Energy + Audio Features**: Local metrics (LUFS, Dynamic Complexity, Spectral Centroid/Rolloff, Onset Rate) → basis for "energy score"
- **Bucketing**: FUTURE feature for smart playlists - not currently used for folder organization
- **Current Organization**: Simple logistics folders (Main Library by artist, Reject flat, Archive by artist)
- **Online Sources (Beatport/MB/Last.fm/SoundCloud\*)**: Beatport gold standard for EDM genres (weight 10.0), others remain auxiliary; main decisions (BPM/Key/Energy) based on audio. (\*SoundCloud optional, with health check and skip option)
- **Caching, Repeatability & Audit**: Deterministic analysis, cache by file hash + algorithm version

## 2) Module Architecture

```
djlib/
  rekordbox_status.py   # NEW: Rekordbox DB integration, strict mode validation
  unsorted.py           # NEW: UNSORTED folder management (Excel staging)
  audio/                # Audio analysis
    __init__.py
    essentia_backend.py # Essentia calls (BPM/Key/Energy, features)
    features.py         # Normalization, sampling, 0.5×/2× corrections, energy score
    cache.py            # SQLite/JSON cache + algorithm versioning
  tags.py               # Tag reading (bpm, key, …)
  metadata/             # MB/LFM/SoundCloud/Beatport (optional)
    beatport.py         # Beatport client (JWT auto-refresh)
    genre_resolver.py   # Noise filters + multi-source weights (Beatport/MB/LFM/SC)
    mb_client.py        # MusicBrainz client
    lastfm.py           # Last.fm client
    soundcloud.py       # SoundCloud client (client_id auto-refresh)
    coverart.py         # Album artwork fetching (4-source fallback)
  bucketing/                # FUTURE: Smart playlist generation
    base.py             # Base interfaces
    rules.py            # Deterministic rules (v0)
    simple_ml.py        # ML classifier (v0.1)
  ml/                   # NEW: ML training dataset export
    __init__.py
    export_dataset.py   # Training dataset generation
    models.py           # Model evaluation utilities
  cli.py                # CLI commands (scan, analyze-audio, enrich-online, apply, ml-export-training-dataset)
  csvdb.py              # CSV operations (unsorted.xlsx)
  config.py             # Paths, settings
scripts/
  report_preview.py     # Extended with bpm_detected/key_detected/energy + per-source genres (DONE)
  assign_buckets.py     # CLI for bucketing (v0.1)
```

## 3) End-to-End Pipeline

### WORKFLOW 0: Sync DJ Libraries & Tags (Optional)

**Command:** `python -m djlib.cli sync-dj-libraries --write`

**Purpose:** Ensure library.csv is in sync with Rekordbox/Traktor databases

**Steps:**
1. Compare library.csv with Rekordbox DB and Traktor collection.nml
2. Identify missing tracks (in library.csv but not in DJ software)
3. Add missing tracks to Rekordbox (via `pyrekordbox.add_content()`)
4. Add missing tracks to Traktor (via XML manipulation)
5. Update paths for moved tracks in Traktor
6. Add custom DJLIB tags where missing

**Output:** Updated Rekordbox/Traktor databases with all library tracks

### WORKFLOW 1: Scan UNSORTED

**Command:** `python -m djlib.cli scan --strict`

**Purpose:** Quality control for new tracks

**Steps:**
1. **Rekordbox Preparation** — Analyze files in Rekordbox (BPM/Key detection)
2. **Scan UNSORTED**:
   - Read Rekordbox DB → extract rekordbox_id
   - Read Traktor collection.nml → extract traktor_id
   - Validate Rekordbox DB analysis (was_analyzed_from_db)
   - Check TBPM/TKEY tags as fallback
   - Strict mode: reject files without Rekordbox confirmation
   - Tag files with DJLIB_TRACK_ID + rekordbox_id + traktor_id
   - Export to `unsorted.xlsx` with metadata proposals

**Output:** `data/unsorted.xlsx` with validated tracks ready for curation

### WORKFLOW 2: Enrich Online (Optional)

**Command:** `python -m djlib.cli enrich-online`

**Purpose:** Fetch metadata from online sources

**Sources:**
- **Beatport** (NEW): EDM-focused metadata with JWT auto-refresh
  - 100+ precise subgenres (progressive house, melodic techno, afro house)
  - High-resolution artwork (1400x1400px)
  - BPM/Key from Beatport's analysis
  - Weight: 10.0 (priority for EDM)
- MusicBrainz/AcoustID metadata lookup
- Last.fm popularity metrics
- SoundCloud (optional, with health check)

**Features:**
- Multi-source genre resolution with weights: Beatport 10.0 > Last.fm 6.0 > MB 3.0 > SoundCloud 2.0
- Per-source columns: `genres_beatport`, `genres_musicbrainz`, `genres_lastfm`, `genres_soundcloud`
- Popularity metrics: `pop_playcount`, `pop_listeners`
- Album artwork fetching (`--fetch-covers`): 4-source fallback

**Output:** Enriched `unsorted.xlsx` with online metadata

### WORKFLOW 3: Manual Curation (Excel)

**Manual step:** Edit `data/unsorted.xlsx`

**Actions:**
- Review and validate metadata proposals
- Select genre from dropdown (30 canonical genres from genres.yml)
- Select destination: library/reject/archive/mixes
- Mark `done = TRUE` for approved tracks

### WORKFLOW 4: Export & Auto-Sync

**Command:** `python -m djlib.cli apply`

**Purpose:** Move files and sync with DJ software

**Steps:**
1. Clean spam tags (musicdjs.club, chomikuj.pl) while preserving DJ software data
2. ALWAYS clears album tags (compilations not useful for DJs)
3. Move only `done = TRUE` tracks based on `destination` column
4. Generate final filenames with Camelot notation
5. **AUTO-SYNC with DJ software:**
   - Add new tracks to Rekordbox (via `pyrekordbox.add_content()`)
   - Add new tracks to Traktor (via XML manipulation)
   - Update paths for moved tracks in Traktor
6. Update library.csv, clear staging

**Output:** Organized library with DJ software automatically synchronized

### WORKFLOW 5: Analyze Audio (Essentia)

**Command:** `python -m djlib.cli analyze-audio`

**Purpose:** Extract audio features for ML training and future playlists

**Analysis:**
- Only analyzes approved tracks in LIBRARY (not rejected)
- `detect_bpm_essentia(path) -> bpm, conf, corrected_factor`
- `detect_key_essentia(path) -> key_camelot, strength`
- `compute_energy(path) -> energy_score (0..1), {lufs, dyn_complexity, onset_rate, spectral_*}`
- 50+ spectral/MFCC/chroma features
- Caching: (file_hash, algo_version) → result
- Optional: Write to ID3 tags (--write-tags flag)

**Output:** `LOGS/audio_analysis.sqlite` with cached features

### WORKFLOW 6: ML Dataset Export

**Command:** `python -m djlib.cli ml-export-training-dataset`

**Purpose:** Generate training dataset for ML models

**Features:**
- Combine library.csv with Essentia features
- Export to `data/training_dataset_full.csv`
- Columns: `tag_bpm`, `tag_key_camelot`, `ess_bpm`, `ess_key_camelot`, `ess_energy`, `ess_danceability`, etc.
- Labels: user-assigned genres from library

**Output:** `data/training_dataset_full.csv` ready for ML training

## 4) Essentia — Integration Details

### Installation (Development)

- **macOS**: Homebrew `brew install essentia` (or Conda). Some configurations require FFmpeg/FFTW.
- **Python**: Test import in CLI (`analyze-audio --check-env`) — clear report on what works/missing, with instructions.
- **Fallback (Optional)**: If Essentia unavailable, can (consciously) enable librosa/madmom backend, but with quality warning.

### Performance

- **Sampling**: Mono, 22.05 kHz, middle 30-60s; skip intro/outro.
- **Parallelism**: Analysis in processes/workers (CPU-bound), limit parallelism vs I/O.

### BPM Detection

- **Algorithm**: TempoTap/RhythmExtractor2013 → base tempo.
- **Harmonic Correction**: If < 80 → ×2, if > 180 → ÷2; then choose nearest.
- **Output**: `(bpm, confidence≈stability, correction_factor)`

### Key Detection

- **Algorithm**: KeyExtractor (HPCP) → (pitch class, mode) → Camelot A/B.
- **Output**: `(key_camelot, strength)`

### Energy Calculation

- **Metrics**: `loudnessEBUR128`, `DynamicComplexity`, `SpectralCentroid/Rolloff`, `OnsetRate` → normalize to `energy_score∈[0,1]`.

### 4.1) Default Parameters and Definitions

- **Decoding**: 1-channel (mono), `sr=22050`, loudness normalization to stable scale.
- **Analysis Windows**: Default 3×15s from positions 25%/50%/75% of length; result = median (BPM) / mean (Energy) / mode (Key weighted by strength).
- **BPM Target Range**: `[80, 180]`; correction 0.5×/2× until nearest value in range; log `correction_factor` (1.0, 0.5, 2.0).
- **BPM Confidence**: Derivative of inter-beat-interval stability (e.g., 1 − CV), from [0..1].
- **Key Strength**: Scale [0..1] from Essentia/calculated from HPCP (internal normalization).
- **Energy Score**: Composition of normalized components: `w1*norm(LUFS) + w2*norm(DynComplexity) + w3*norm(OnsetRate) + w4*norm(Centroid/Rolloff)`; default equal weights (calibratable).
- **Key→Camelot**: Mapping table (preference # vs b settable in config); A=minor, B=major.

### 4.2) Calibration and Stability

- **Energy Calibration**: Initial percentiles (P10/P90) on your library saved to `metrics.json`, so results comparable between sessions.
- **BPM/Key Stability**: If divergence between windows > thresholds (e.g., BPM differs >3%), log to `LOGS/unstable_analysis.csv` and lower confidence.
- **Unsupported/Problem Files** (decoder, silence, < 20s): Log to `LOGS/failed_decodes.csv`.

## 5) Future ML Features - Priority Order

**Current Status (November 2025):** All ML features below are NOT YET IMPLEMENTED. They represent the planned roadmap for future development.

### Priority 1: Genre Prediction (ML Model) - FIRST STEP, NOT YET IMPLEMENTED

**Status:** FUTURE - requires minimum 500+ labeled tracks in library before training makes sense

**Goal:** Automatic genre classification based on Essentia audio features

**Input:**

- Essentia features: BPM, Key, Energy, Danceability, Spectral features, MFCCs, etc.
- NO external metadata (pure audio analysis)

**Output:**

- Predicted genre from 30 canonical genres (genres.yml)
- Confidence score

**Use Case:**

- User: "Analyze new tracks and suggest genres"
- Model: Trained on user's accepted tracks from library
- Result: Accurate genre suggestions based on audio characteristics

**Implementation (FUTURE):**

- Train RandomForest/XGBoost on exported training dataset
- Features: `ess_bpm`, `ess_key`, `ess_energy`, `ess_danceability`, spectral, MFCCs
- Labels: User-curated genres from library.csv

**Prerequisites:**

- ✅ Essentia analysis pipeline (DONE)
- ✅ Training dataset export (DONE)
- ⏳ Minimum 500+ labeled tracks in library (in progress)
- ❌ ML model training code (TODO)

### Priority 2: Smart Playlist Generation (AI Assistant) - FUTURE, AFTER GENRE PREDICTION

**Status:** FUTURE - long-term goal, after genre prediction is working

**Goal:** Natural language playlist creation based on context/mood/energy

**Examples:**

- User: "4 hours for cocktail bar with light foot-tapping"
- User: "Peak hour bangers, high energy, 128-130 BPM"
- User: "Smooth opening set, warm vibes, nothing too intense"

**Input:**

- Natural language query (LLM parsing)
- Library metadata: genres, BPM, key, energy, tags
- Audio features from Essentia cache

**Output:**

- Generated playlist matching criteria
- Smooth transitions (key compatibility, energy flow)

**Implementation (FUTURE, AFTER GENRE PREDICTION):**

- LLM parses user intent → search criteria
- Filter library: BPM ranges, energy levels, genres, occasion_tags
- Optional: harmonic mixing (key compatibility)
- Export to M3U/Rekordbox playlist

**Note:** This is NOT folder organization - playlists are dynamic, context-based, and regenerated on demand.

**Prerequisites:**

- ✅ Essentia analysis pipeline (DONE)
- ✅ Clean library with metadata (in progress)
- ❌ Genre prediction working (TODO - Priority 1)
- ❌ LLM integration (TODO)
- ❌ Playlist generation engine (TODO)

### Deprecated: Bucket/Folder Assignment

**What's NOT happening:**

- ❌ NO automatic folder assignment (CLUB/OPENING/WARMUP/etc.)
- ❌ NO subfolder taxonomy based on musical characteristics
- ❌ NO "bucket" predictions that create folder structure

**Why:**

- Folder structure is pure logistics (Main Library by artist)
- Musical categorization belongs in metadata (genre) and playlists
- Context is fluid ("cocktail bar" vs "peak hour") - doesn't map to static folders

## 6) CLI and UX (Implemented Extensions)

### Commands (Current)

- `djlib.cli analyze-audio` — Analyze entire UNSORTED (with cache), progress and time metrics (DONE)
- `scripts/report_preview.py` — Preview metadata with per-source genres (DONE)
- **Debug Mode**: `--debug` writes features/justifications to LOGS/ (DONE)

**FUTURE Priority 1 (Genre Prediction - NOT YET IMPLEMENTED):**

- `djlib.cli predict-genre` — Predict genres based on Essentia features using trained ML model
- `djlib.cli train-genre-model` — Train genre classifier on accepted library tracks

**FUTURE Priority 2 (Smart Playlists - NOT YET IMPLEMENTED):**

- `djlib.cli generate-playlist "4h cocktail bar light vibes"` — AI-powered playlist generation

### 6.1) CLI Flags (Proposed)

- `analyze-audio [--workers N] [--recompute] [--window middle|segments=3x15s] [--target-bpm 80:180] [--check-env]`
  - `--check-env`: Quick test of Essentia/FFmpeg/FFTW import + versions.
  - `--recompute`: Ignore cache (e.g., after algorithm change).
  - `--workers`: CPU parallelism; default min(cores, 4).
- `report-preview [--compute-missing-only] [--with-breakdown]`
  - `--compute-missing-only`: Don't calculate BPM/Key/Energy if correct tags already exist.
  - `--with-breakdown`: Additional columns with energy components and debug BPM/Key.

**FUTURE Priority 1 (Genre Prediction - NOT YET IMPLEMENTED):**

- `predict-genre [--threshold 0.7] [--write-tags]` — Suggest genres with ML model (TODO)
- `train-genre-model [--min-samples 500]` — Train on accepted library tracks (TODO)

**FUTURE Priority 2 (Smart Playlists - NOT YET IMPLEMENTED):**

- `generate-playlist "<natural language query>" [--duration 240] [--format m3u]` — AI playlist generation (TODO)

## 7) Caching and Algorithm Versioning

### File Identifier

- **audio_id**: Based on chromaprint (if available) or fast xxhash of first X MB + (size, mtime).

### Cache Key

- `(audio_id, algo_version, config_hash)`

### Versioning

- **algo_version**: Increment on algorithm changes; **config_hash**: Hash of important parameters (BPM range, windows, energy weights, # vs b preference).

### Format

- **SQLite (Recommended)** — table `audio_analysis`:
  - `audio_id TEXT PRIMARY KEY`
  - `algo_version INT`
  - `config_hash TEXT`
  - `bpm REAL`, `bpm_conf REAL`, `bpm_corr REAL`
  - `key_camelot TEXT`, `key_strength REAL`
  - `lufs REAL`, `dyn_complex REAL`, `onset_rate REAL`, `spec_centroid REAL`, `spec_rolloff REAL`
  - `energy REAL`, `energy_var REAL`
  - `analyzed_at TEXT`

### Cache API

- get/set with version/config validation; `--recompute` bypasses cache.

## 8) Quality: Metrics and Tests

### Unit Tests

- Parsing, Camelot conversions, 0.5×/2× corrections, genre classification, noise filters.

### Integration Tests

- Several audio samples (short fragments) + snapshot results.

### Quality Metrics

- Compare with reference (if you have "ground truth" file) → report to `metrics.json`.

### 8.1) Tests Without Real Files

- **Generate Synthetic Samples**: Audio (e.g., metronome for 90/120/128 BPM; sine/wavetable for selected keys).
- **Integration Tests**: Check BPM correction (×2/÷2), Key→Camelot mapping correctness, energy stability.
- **Don't Include Real Music** in repo.

## 9) Licenses and Distribution

### Essentia

- Non-commercial mode OK; if Open Source — respect AGPL or consider commercial option in future.

### ML Models (If Used)

- License audit for each model (autotagging), or BYO model by user.

## 10) Roadmap — Execution Steps (Progress Markers)

### Phase A (BPM/Key/Energy + Preview) – PARTIAL DONE (some Energy still needs calibration)

1. `djlib/audio/essentia_backend.py`: BPM/Key/Energy detectors + cache. (IN PROGRESS)
2. Integration with `report_preview.py` (columns + quality indicators, no timing regressions). (PARTIAL DONE)
3. CLI `analyze-audio` + logs.

### Phase B (Bucket v0 — Rules)

4. `bucketing/rules.py` + starter `rules.yml` (BPM ranges, energy, optionally styles from taxonomy_map).
5. Add to Preview `bucket_suggest` + `bucket_confidence`. (PENDING)

### Phase C (Auto-Bucket v0.1 — ML)

6. `bucketing/simple_ml.py` (RandomForest) — per appendix; features from audio.
7. `assign_buckets.py` + `metrics.json` + tests. (PENDING)

### Phase D (Hybrid — Optional)

8. `hybrid_model.py` (SBERT + features), compare with v0.1. (FUTURE)

## 11) "Ready" Criteria (Definition of Done) – Updated

### DoD A

- Preview CSV shows detected BPM/Key/Energy on entire UNSORTED, per-source genres; analysis time acceptable; cache works, no I/O errors.

### DoD B

- Bucket rules give sensible proposals on your data, editable in YAML; acceptance report ≥ established threshold.

### DoD C

- ML v0.1 achieves ≥ X% accuracy on your dataset, metrics saved and reproducible.

---

## 12) Completed Elements Beyond Original Plan

### Rekordbox Integration (NEW)

- **pyrekordbox 0.4.4**: Rekordbox6Database integration
- **DB-First Priority**: Rekordbox DB authoritative for BPM/Key in UNSORTED
- **Strict Mode**: `scan --strict` enforces DB confirmation, rejects tag-only files
- **Graceful Degradation**: Falls back to TBPM/TKEY tags if DB unavailable

### Multi-Source Genre Enrichment

- **MB / Last.fm / SoundCloud** with weights.
- **Per-Source Columns**: `genres_*` + popularity (`pop_playcount`, `pop_listeners`).
- **Interactive Prompt**: Invalid SoundCloud client ID + `--skip-soundcloud` flag.

### Filename Parsing Enhancements

- **Multiple Parentheses**: Combined as list in `version_suggest`.
- **Remix-Aware SoundCloud**: Pass `version` from CLI/resolver, filter Extended/Radio/Remix tokens.

### UNSORTED Workflow

- **Excel Staging**: `unsorted.xlsx` with done=TRUE approval workflow.
- **Strict Mode**: Quality control in UNSORTED, flexibility after moves.

### ML Training Export (NEW)

- **Command**: `ml-export-training-dataset`
- **Output**: `data/training_dataset_full.csv` with Essentia features + user buckets
- **Features**: `ess_bpm`, `ess_key_camelot`, `ess_energy`, `ess_danceability`, etc.
- **Use Case**: Train RandomForest/XGBoost classifiers, feature importance analysis

## 13) Backlog Additions (Proposed)

- **Afro House Heuristic**: Pattern matching (e.g., "Karibu Remix") → boost weight in club bucketing.
- **Persist SoundCloud Decision**: User choice to skip SC in `enrich_status.json`.
- **Energy Calibration**: Percentiles + visualization in report.
- **ML Bucketing v0.1**: Accuracy, precision, recall per bucket metrics.
- **SoundCloud Response Cache**: Cache + soft rate-limit handling.

---

If you confirm direction, next steps: finalize Energy + bucket rules v0, then ML module.

## 14) Future Feature Examples (NOT CURRENTLY IMPLEMENTED - ALL TODO)

**Important:** All features below are planned for future development. None are implemented yet.

### Priority 1: Genre Prediction Model (FIRST STEP - TODO)

**Training command (future, not yet implemented):**

```bash
python -m djlib.cli train-genre-model --min-samples 500
# Trains on library.csv + Essentia features
# Outputs: models/genre_classifier.pkl + metrics.json
```

**Prediction command (future, not yet implemented):**

```bash
python -m djlib.cli predict-genre --threshold 0.7
# Suggests genres for unsorted.xlsx tracks
# Updates genre_suggest column with ML predictions
```

**Model features (when implemented):**

- Input: Pure Essentia audio features (BPM, Key, Energy, Spectral, MFCCs)
- Output: One of 30 canonical genres from genres.yml
- Confidence threshold: Skip low-confidence predictions

**Timeline:** After collecting 500+ labeled tracks in library

### Priority 2: Smart Playlist Generation (AI-Powered, AFTER GENRE PREDICTION)

**Example queries (future, not yet implemented):**

```bash
# Context-based playlist
python -m djlib.cli generate-playlist \
  "4 hours for cocktail bar with light foot-tapping" \
  --format m3u --output ~/playlists/cocktail-2025-11-28.m3u

# Energy-based playlist
python -m djlib.cli generate-playlist \
  "peak hour bangers, 128-130 BPM, high energy house and techno" \
  --duration 120

# Mood-based playlist
python -m djlib.cli generate-playlist \
  "smooth opening set, warm vibes, nothing too intense"
```

**How it works (future, after genre prediction is working):**

1. LLM parses natural language → criteria (BPM range, energy level, genres, mood)
2. Query library: filter by criteria
3. Optional: harmonic mixing (key compatibility)
4. Generate playlist with smooth transitions
5. Export to M3U or Rekordbox XML

**Note:** Playlists are DYNAMIC and CONTEXT-BASED - not static folder structures.

---

**Last Updated**: November 2025 (Rekordbox Integration + ML Export)  
**Version**: 2.2
