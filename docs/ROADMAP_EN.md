# DJ Library Manager — Roadmap (Rekordbox + Essentia, Non-Commercial)

This document describes the roadmap for Rekordbox-first architecture with Essentia audio analysis as cache/alternative source for BPM and Key, extended with Energy and low-level features, and integration with the Auto-Bucket module. We assume non-commercial mode (private / potentially open-source in the future), optimizing for quality without copyleft restrictions.

## 1) Assumptions and Goals (Updated November 2025)

- **Rekordbox as Source of Truth**: BPM and Key from Rekordbox DB (TBPM/TKEY tags) take priority in UNSORTED folder
- **Strict Mode Enforcement**: `scan --strict` requires Rekordbox DB confirmation for quality control
- **Essentia as Cache/Alternative**: Local metrics (BPM/Key/Energy) cached for non-Rekordbox files or after moves
- **Energy + Audio Features**: Local metrics (LUFS, Dynamic Complexity, Spectral Centroid/Rolloff, Onset Rate) → basis for "energy score"
- **Bucketing**: Deterministic rules first (v0), then classic ML (v0.1), optionally hybrid with embeddings (v0.3)
- **Online Sources (MB/Last.fm/SoundCloud\*)**: Remain auxiliary; main decisions (BPM/Key/Energy/Bucket) based on audio. (\*SoundCloud optional, with health check and skip option)
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
  metadata/             # MB/LFM/SoundCloud (optional)
    genre_resolver.py   # Noise filters + multi-source weights (MB/LFM/SC)
    mb_client.py        # MusicBrainz client
    lastfm.py           # Last.fm client
    soundcloud.py       # SoundCloud client (optional)
  bucketing/
    base.py             # Interfaces
    rules.py            # v0 deterministic bucketing rules
    simple_ml.py        # v0.1 RandomForest (per plan)
    hybrid_model.py     # v0.3 (optional)
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

### UNSORTED Folder (Quality Control)

1. **Configuration** (LIB_ROOT/UNSORTED) — as today
2. **Rekordbox Preparation** — Analyze files in Rekordbox (BPM/Key detection)
3. **Scan UNSORTED** (`scan --strict`):
   - Validate Rekordbox DB analysis (was_analyzed_from_db)
   - Check TBPM/TKEY tags as fallback
   - Strict mode: reject files without Rekordbox confirmation
   - Export to `unsorted.xlsx` with proposals
4. **Analyze Audio** (Essentia cache - OPTIONAL):
   - `detect_bpm_essentia(path) -> bpm, conf, corrected_factor`
   - `detect_key_essentia(path) -> key_camelot, strength`
   - `compute_energy(path) -> energy_score (0..1), {lufs, dyn_complexity, onset_rate, spectral_*}`
   - Caching: (file_hash, algo_version) → result
   - Write to ID3 tags (--write-tags flag)
5. **Enrich Online** (`enrich-online`):
   - MusicBrainz/AcoustID metadata lookup
   - Multi-source genre resolution (MB/Last.fm/SoundCloud)
   - Per-source columns: `genres_musicbrainz`, `genres_lastfm`, `genres_soundcloud` (DONE)
   - Popularity metrics: `pop_playcount`, `pop_listeners`
   - Album artwork fetching (`--fetch-covers`): 3-source fallback (Cover Art Archive → Last.fm → SoundCloud)
6. **Manual Review** (Excel `unsorted.xlsx`):
   - User validates metadata proposals
   - Selects bucket from taxonomy dropdown
   - Marks `done = TRUE` for approved tracks
7. **Apply Decisions** (`apply`):
   - Clean spam tags (musicdjs.club, chomikuj.pl, p2pdl.com) while preserving DJ software data
   - Move only `done = TRUE` tracks to LIBRARY
   - Generate final filenames with Camelot notation
   - Update paths, clear staging

### LIBRARY Folder (After Move)

8. **ML Training Export** (`ml-export-training-dataset`):
   - Combine accepted tracks with Essentia features
   - Export to `data/training_dataset_full.csv`
   - Features: `ess_bpm`, `ess_key_camelot`, `ess_energy`, `ess_danceability`, etc.
   - Labels: user-assigned buckets from LIBRARY

### Bucketing (Future)

9. **Bucket v0** (deterministic rules):
   - Map based on BPM (ranges), Key (mode A/B), Energy (thresholds), percussiveness
10. **Auto-Bucket v0.1** (ML):

- Features: `{bpm_detected, key_detected, energy_score, genre_tokens}`
- Model: RandomForest + metrics + export `bucket_predictions.csv`

11. **Feedback & Evaluation**:

- `feedback.csv` → retrain, `metrics.json`, acceptance ≥ 80%

**Note:** Meta-commands `round-1`/`round-2` temporarily disabled; will return as orchestrator after UNSORTED workflow stabilization.

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

## 5) Bucketing — Paths (No Functional Changes in This Update)

### v0 (Rules)

- **Example**: `house|tech house` + `120-128 BPM` + `energy≥0.6` → `READY TO PLAY/HOUSE BANGERS`;
  `downbeat|electronica` + `70-100 BPM` + `energy≤0.4` → `CHILL/OPENING`.
- **Rules**: Kept in `bucketing/rules.py`, configurable in YAML.

### v0.1 (ML)

- Per document `auto_bucket_module_plan.md` and `auto_bucket_todo_list.md` — integrate audio features.

### v0.3 (Hybrid)

- **SBERT** text embedding + audio features, MLP/XGBoost classifier.

## 6) CLI and UX (Implemented Extensions)

### Commands

- `djlib.cli analyze-audio` — Analyze entire UNSORTED (with cache), progress and time metrics.
- `scripts/report_preview.py` — Columns: `bpm_detected`, `bpm_confidence`, `bpm_correction`, `key_detected_camelot`, `key_strength`, `energy_score`, per-source genres (DONE).
- `scripts/assign_buckets.py` — Predict buckets (v0.1), export `bucket_predictions.csv`.
- **Debug Mode**: `--debug` writes features/justifications to LOGS/.

### 6.1) CLI Flags (Proposed)

- `analyze-audio [--workers N] [--recompute] [--window middle|segments=3x15s] [--target-bpm 80:180] [--check-env]`
  - `--check-env`: Quick test of Essentia/FFmpeg/FFTW import + versions.
  - `--recompute`: Ignore cache (e.g., after algorithm change).
  - `--workers`: CPU parallelism; default min(cores, 4).
- `report-preview [--compute-missing-only] [--with-breakdown]`
  - `--compute-missing-only`: Don't calculate BPM/Key/Energy if correct tags already exist.
  - `--with-breakdown`: Additional columns with energy components and debug BPM/Key.
- `assign-buckets [--rules path.yml] [--model models/bucket_model.pkl] [--debug]`

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

- Parsing, Camelot conversions, 0.5×/2× corrections, bucket mapping, noise filters.

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

## 14) Starter `rules.yml` (Example)

```yaml
version: 1
defaults:
  target_bpm_range: [80, 180]
  energy_thresholds:
    low: 0.35
    mid: 0.55
    high: 0.70

rules:
  - name: HOUSE_BANGERS
    when:
      bpm: [120, 130]
      energy_min: high
      styles_any: [house, tech house, electro house]
    then:
      bucket: READY TO PLAY/HOUSE BANGERS
      confidence: 0.8

  - name: CHILL_OPENING
    when:
      bpm: [70, 100]
      energy_max: low
      styles_any: [downbeat, electronica, chillout]
    then:
      bucket: CHILL/OPENING
      confidence: 0.75

  - name: HIPHOP_WARM
    when:
      bpm: [80, 110]
      styles_any: [hip hop]
      key_mode_any: [A]
    then:
      bucket: HIPHOP/WARMUP
      confidence: 0.7

resolution:
  tie_breaker: [confidence, energy, bpm_proximity]
  fallback_bucket: REVIEW QUEUE/UNSURE
```

---

**Last Updated**: November 2025 (Rekordbox Integration + ML Export)  
**Version**: 2.2
