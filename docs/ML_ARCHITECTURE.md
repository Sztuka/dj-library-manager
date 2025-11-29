# ML Architecture & Future Integration

**Status:** Phase 1 Complete (Feature Extraction)  
**Date:** November 2025

---

## Overview

The ML pipeline is designed for **future** playlist generation and context-based recommendations.  
**Current state:** Feature extraction only (Essentia cache). No model training or prediction in the core workflow.

---

## Architecture Principles

1. **Library cleaner FIRST**: Core workflow (scan → enrich → apply) doesn't depend on ML
2. **Features as cache**: Essentia analysis → SQLite cache, NOT written to tags
3. **Stable track IDs**: Every track gets a unique `track_id` for feature correlation
4. **Separation of concerns**: ML training/prediction is separate from library organization

---

## Current Implementation

### Track ID Schema

Every scanned track gets a **stable track_id**:

```python
track_id = f"{file_hash[:12]}_{timestamp}"
# Example: "a3f2c8d91b4e_1732618234"
```

**Properties:**

- **Unique**: file_hash ensures no collisions
- **Stable**: Same file always gets same hash
- **Traceable**: Can correlate across moves/renames

**Usage:**

- Primary key in `library.csv` and `unsorted.xlsx`
- Foreign key in `audio_features` cache
- Used for path mapping in Rekordbox/Traktor sync

### Audio Features Cache

**Location:** `LOGS/audio_analysis.sqlite`

**Schema:**

```sql
CREATE TABLE audio_features (
    file_path TEXT PRIMARY KEY,
    file_hash TEXT,
    bpm REAL,
    key TEXT,
    key_camelot TEXT,
    energy REAL,
    danceability REAL,
    -- 50+ Essentia features
    computed_at TEXT,
    essentia_version TEXT
);
```

**Access:**

```python
from djlib.audio.cache import get_analysis
features = get_analysis(file_path)
# Returns: {bpm, key, energy, danceability, spectral_*, mfcc_*, ...}
```

**Note:** BPM/Key from Essentia are NOT used in the main workflow.  
Rekordbox tags are the source of truth for playable metadata.

### ML Dataset Export

**Command:**

```bash
python -m djlib.cli ml-export-training-dataset --out data/training_dataset.csv
```

**Output CSV columns:**

- `track_id` (unique identifier)
- `genre_canonical` (from genres.yml resolution)
- `destination` (library/reject/archive)
- `bpm`, `key`, `energy`, `danceability` (from Essentia cache)
- `spectral_centroid`, `spectral_rolloff`, `mfcc_0..12` (Essentia features)
- `pop_playcount`, `pop_listeners` (Last.fm popularity)

**Purpose:** Training external ML models for:

- Genre classification refinement
- Energy/context prediction (cocktail vs club)
- Playlist similarity clustering

---

## Future Integration Points

### Phase 2: Context Classification (NOT IMPLEMENTED YET)

**Goal:** Predict track context (cocktail, pool party, club main room, etc.)

**Approach:**

1. User labels tracks with `occasion_tags` in `unsorted.xlsx`
2. Export training dataset with Essentia features + occasion labels
3. Train classifier (external, not in repo)
4. Store predictions in `library.csv` as `context_scores` JSON field

**Example:**

```json
{
  "context_scores": {
    "cocktail": 0.85,
    "club": 0.15,
    "pool_party": 0.62
  }
}
```

**Integration:** Search/filter in future UI, NOT used for folder placement.

### Phase 3: Smart Playlists (NOT IMPLEMENTED YET)

**Goal:** Generate playlists based on:

- Energy curve (warmup → peak → cooldown)
- Harmonic mixing (Camelot key compatibility)
- BPM progression
- Context scores

**Approach:**

1. Define playlist rules (JSON schema)
2. Query `library.csv` with filters (genre, energy, context)
3. Use `audio_features` cache for similarity calculations
4. Export as M3U/Rekordbox playlist XML

**NOT in scope:** Auto-folder organization. Playlists are separate from library structure.

### Phase 4: Rekordbox/Traktor Playlist Sync (NOT IMPLEMENTED YET)

**Goal:** Sync generated playlists back to DJ software

**Approach:**

1. Generate playlist (Phase 3)
2. Map `track_id` → external path
3. Create playlist in Rekordbox DB or Traktor NML
4. Backup before writing, atomic transactions

**Safety:** Read-only by default, explicit `--write` flag required.

**Note:** Basic Rekordbox/Traktor integration (WORKFLOW 0, 1, 4) is already implemented for track sync. This phase refers specifically to playlist sync features.

---

## Data Flow Diagram

```
┌──────────────┐
│ UNSORTED/    │
│ (new files)  │
└──────┬───────┘
       │
       ├─ scan (Rekordbox validation)
       │
       v
┌──────────────────┐
│ unsorted.xlsx    │
│ - track_id       │
│ - metadata       │  ┌────────────────────┐
│ - user edits     │  │ Essentia analysis  │
│ - genre          │  │ (optional)         │
└──────┬───────────┘  └────────┬───────────┘
       │                       │
       ├─ done=TRUE            v
       │              ┌─────────────────┐
       v              │ audio_features  │
┌──────────────┐     │ (SQLite cache)  │
│ LIBRARY/     │     └─────────────────┘
│ {Artist}/    │
│ {filename}   │              │
└──────┬───────┘              │
       │                      │
       v                      v
┌──────────────────────────────────┐
│ library.csv                      │
│ - track_id (PK)                  │
│ - final_path                     │
│ - genre_canonical                │
│ - destination                    │
│ - (future: context_scores)       │
└──────────────────────────────────┘
       │
       ├─ (Future) ML training
       │
       v
┌──────────────────┐
│ ML models        │
│ (external)       │
└──────┬───────────┘
       │
       ├─ (Future) Predict context
       │
       v
┌──────────────────┐
│ Smart playlists  │
│ (M3U/XML export) │
└──────────────────┘
```

---

## Migration Notes

**From old ML bucketing system:**

❌ **Removed:**

- `djlib/bucketing/simple_ml.py` - ML bucket prediction
- `djlib/ml/models.py` - Old FMA-based training
- Bucket → folder auto-assignment

✅ **Kept:**

- `djlib/audio/` - Essentia feature extraction (cache-only)
- `djlib/ml/export_dataset.py` - Training dataset export
- Track IDs and feature correlation

**Why:** Folders are logistics, not ML outputs. Playlists are the right place for ML-driven organization.

---

## Developer Guide

### Adding New Features to Cache

1. Update `djlib/audio/features.py`:

```python
def extract_features(file_path: Path) -> Dict[str, Any]:
    # ... existing Essentia extraction

    # Add your feature
    features["my_feature"] = compute_my_feature(audio)
    return features
```

2. Update cache schema in `djlib/audio/cache.py` (add column)
3. Features automatically available in `ml-export-training-dataset`

### Training External Models

1. Export dataset:

```bash
python -m djlib.cli ml-export-training-dataset \
    --out data/training.csv \
    --require-both-labels  # Only tracks with genre+destination
```

2. Train model (external, e.g., scikit-learn, PyTorch):

```python
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

df = pd.read_csv("data/training.csv")
X = df[feature_columns]
y = df["genre_canonical"]

model = RandomForestClassifier()
model.fit(X, y)
```

3. **(Future)** Store predictions in `library.csv` via new CLI command

---

## References

- `docs/ARCHITECTURE_EN.md` - Core system architecture
- `djlib/audio/README.md` - Essentia backend details
- `djlib/legacy/README.md` - Deprecated bucketing system
