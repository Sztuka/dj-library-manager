# ML Pipeline - Training Dataset Export

**Note:** This document describes ML training dataset export for **genre classification** models. The bucket/playlist organization features are FUTURE enhancements (see ROADMAP_EN.md).

## Feature sets for ML models

The command:

    python -m djlib.cli ml-export-training-dataset

produces `data/training_dataset_full.csv`. This file joins:

- Essentia audio features (flattened payload from `audio_analysis`)
- Final labels from the library:
  - `genre_label` ← derived from `library.genre` (30 canonical genres from genres.yml)
  - `bucket_label` ← FUTURE: will be derived from smart playlists (currently not used)

We use different feature subsets for different model types.

### Genre model (predicts genre_label) - CURRENT FOCUS

Goal: predict the final track genre used for audio tags.

Input `X_genre`:

- only audio features from Essentia, e.g.:
  - rhythm: `rhythm.bpm` / `bpm_corr` / `bpm_conf`, `rhythm.onset_rate`, `rhythm.danceability`
  - tonal/key: `tonal.key_edma.*` (mapped to `key_camelot`, `key_strength`), aggregated `tonal.hpcp` (HPCP/chroma), `tonal.tonnetz` if available
  - energy / loudness: `lowlevel.spectral_energy`, `energy_score_from_metrics`, `lowlevel.loudness_ebu128.integrated`, `lowlevel.dynamic_complexity`
  - spectral shape: `lowlevel.spectral_centroid_*`, `lowlevel.spectral_rolloff_*`, `lowlevel.spectral_contrast_*`, `lowlevel.spectral_flux_*`, `lowlevel.spectral_flatness_db_*`, `lowlevel.hfc_*`
  - MFCC: mean/std (and kurtosis/skewness if present) of `lowlevel.mfcc`

We treat "all numeric Essentia-derived columns" as valid inputs for the genre model and do not use any external metadata here.

Target `y_genre`:

- `genre_label` (final cleaned genre from the library's `genre` column - one of 30 canonical genres from genres.yml)

At inference time the genre model receives only Essentia features and outputs a suggested genre.

### Bucket/Playlist model (FUTURE) - predicts smart playlist assignments

**Status:** FUTURE ENHANCEMENT - not currently implemented. Current system uses simple destination folders (library/reject/archive/mixes).

Goal: predict smart playlist assignments for track organization (e.g. "warm-up", "peak hour", "chill closing").

Input `X_bucket` (FUTURE):

- all audio features used by the genre model (Essentia features)
- plus DJ/business metadata from the library:
  - `genre_label` (predicted or manually set)
  - `bpm` / `key_camelot`
  - `must_play`
  - `year`
  - `duration`
  - `occasion_tags`, `pop_playcount`, `pop_listeners`, etc.

The bucket model would be "Essentia + context": same audio representation as the genre model, enriched with metadata.

Target `y_bucket` (FUTURE):

- `bucket_label` or `playlist_label` (smart playlist assignment)

In future inference pipeline:

1. Genre model → suggest `genre_label`
2. Bucket/playlist model → predict playlist assignment given Essentia features + genre + metadata
