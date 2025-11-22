## Feature sets for ML models

The command:

    python -m djlib.cli ml-export-training-dataset

produces `data/training_dataset_full.csv`. This file joins:

- Essentia audio features (flattened payload from `audio_analysis`)
- Final labels from the library:
  - `genre_label`  ← derived from `library.genre`
  - `bucket_label` ← derived from `library.target_subfolder`

We use two different feature subsets for two separate models.

### Genre model (predicts genre_label)

Goal: predict the final track genre used for audio tags.

Input `X_genre`:
- only audio features from Essentia, e.g.:
  - rhythm: `rhythm.bpm` / `bpm_corr` / `bpm_conf`, `rhythm.onset_rate`, `rhythm.danceability`
  - tonal/key: `tonal.key_edma.*` (mapped to `key_camelot`, `key_strength`), aggregated `tonal.hpcp` (HPCP/chroma), `tonal.tonnetz` if available
  - energy / loudness: `lowlevel.spectral_energy`, `energy_score_from_metrics`, `lowlevel.loudness_ebu128.integrated`, `lowlevel.dynamic_complexity`
  - spectral shape: `lowlevel.spectral_centroid_*`, `lowlevel.spectral_rolloff_*`, `lowlevel.spectral_contrast_*`, `lowlevel.spectral_flux_*`, `lowlevel.spectral_flatness_db_*`, `lowlevel.hfc_*`
  - MFCC: mean/std (and kurtosis/skewness if present) of `lowlevel.mfcc`

We treat “all numeric Essentia-derived columns” as valid inputs for the genre model and do not use any external metadata here.

Target `y_genre`:
- `genre_label` (final cleaned genre coming from the library’s `genre` column)

At inference time the genre model receives only Essentia features and outputs a suggested genre.

### Bucket model (predicts bucket_label / target_subfolder)

Goal: predict the final bucket / target_subfolder used for organising and playing tracks (e.g. `READY TO PLAY/CLUB/AFRO HOUSE`, `REVIEW/...`).

Input `X_bucket`:
- all audio features used by the genre model (Essentia features)
- plus simple DJ/business metadata from the library, e.g.:
  - `genre_label` (either predicted by the genre model or manually corrected)
  - `bpm` / `bpm_corr`
  - `must_play`
  - `year_suggest` (and/or derived decade)
  - `duration_suggest`
  - in the future: `pop_playcount`, `pop_listeners`, `occasion_tags`, etc.

The bucket model is therefore “Essentia + context”: same audio representation as the genre model, enriched with whatever local metadata we have about the track.

Target `y_bucket`:
- `bucket_label` (final bucket/folder derived from `library.target_subfolder`)

In the future inference pipeline we will typically run:
1. Genre model → suggest `genre_label`
2. Bucket model → predict `bucket_label` given Essentia features + the (possibly corrected) `genre_label` and other metadata.
