# Future ML Features – Plan + Roadmap

**⚠️ ALL FEATURES BELOW ARE FUTURE/TODO:** This document describes future ML enhancements that are NOT YET IMPLEMENTED. The current system (November 2025) uses simple logistics folders (Main Library by artist, Reject, Archive) with genre classification in metadata.

## Priority 1: Genre Prediction (FIRST STEP - NOT YET IMPLEMENTED)

### Goal

Automatic genre classification based purely on audio features (Essentia analysis).

**Status:** TODO - not yet implemented, requires 500+ labeled tracks before training

### Approach (FUTURE)

**ML Genre Classifier:**

- Input: Essentia features (BPM, Key, Energy, Danceability, Spectral, MFCCs)
- Output: One of 30 canonical genres from genres.yml + confidence
- Model: RandomForest or XGBoost trained on user's library
- Training data: `ml-export-training-dataset` output (infrastructure exists)

### Use Case

```python
# User workflow
1. Scan new tracks → unsorted.xlsx
2. Run: djlib.cli predict-genre
3. Review ML suggestions in genre_suggest column
4. Accept or correct manually
5. Model learns from corrections
```

## Priority 2: Smart Playlist Generation (AFTER PRIORITY 1 - FUTURE)

### Objective

Natural language playlist generation: "4 hours for cocktail bar with light foot-tapping"

**Status:** FUTURE - long-term goal, only after genre prediction is working

### Approach

**AI Playlist Assistant:**

- LLM parses natural language query → criteria
- Filters library: BPM, energy, genres, occasion_tags
- Optional: harmonic mixing (key compatibility)
- Output: M3U or Rekordbox XML playlist

### Use Cases

```bash
# Context-based
generate-playlist "cocktail bar, chill vibes, 4 hours"

# Energy-based
generate-playlist "peak hour bangers, 128-130 BPM"

# Mood-based
generate-playlist "smooth opening, warm, not too intense"
```

### NOT in Scope

- ❌ Folder organization (CLUB/WARMUP/etc.)
- ❌ Static bucket assignment
- ❌ Subfolder taxonomy

**Why:** Context is fluid - same track fits "cocktail bar" AND "beach party" depending on time/mood. Playlists are dynamic, folders are static logistics.

## Implementation Plan

### Phase 1: Genre Classifier (Priority 1 - TODO)

**Prerequisites:**

1. ✅ Export training dataset (`ml-export-training-dataset`) - DONE
2. ⏳ Collect 500+ labeled tracks in library - IN PROGRESS

**Implementation (TODO):**

1. [ ] Implement `djlib/ml/genre_model.py`:
   - `train_genre_model()` - Train RandomForest on Essentia features
   - `predict_genres()` - Predict for unsorted tracks
   - `evaluate_model()` - Accuracy, F1, confusion matrix
2. [ ] CLI commands:
   - `train-genre-model --min-samples 500`
   - `predict-genre --threshold 0.7 --write-to-xlsx`
3. [ ] Tests + metrics.json
4. [ ] Documentation + usage examples

**Timeline:** When library reaches 500+ tracks

### Phase 2: Playlist Generator (Priority 2 - FUTURE, AFTER PHASE 1)

## Roadmap

### ✅ Phase 0 – Data Pipeline (DONE)

- ✅ Essentia audio analysis with caching
- ✅ Multi-source metadata enrichment
- ✅ Training dataset export (`ml-export-training-dataset`)

### 🎯 Phase 1 – Genre Classifier (Priority 1, TODO)

- [ ] Implement `GenreClassifier` in `djlib/ml/genre_model.py`
- [ ] Feature engineering: normalize Essentia features
- [ ] Train RandomForest/XGBoost on exported dataset
- [ ] CLI: `train-genre-model` + `predict-genre`
- [ ] Tests + metrics.json (accuracy, f1, confusion matrix)
- [ ] Documentation + examples

**Success Criteria:**

- Accuracy ≥ 75% on 30-genre classification
- Confidence scores reliable (calibrated probabilities)
- Model improves with user corrections

### 🔮 Phase 2 – Smart Playlists (Priority 2, FUTURE)

- [ ] LLM query parser (extract criteria from natural language)
- [ ] Library search engine (BPM, energy, genre, tags filters)
- [ ] Harmonic mixing engine (key compatibility, smooth transitions)
- [ ] Playlist export (M3U, Rekordbox XML)
- [ ] CLI: `generate-playlist "<query>"`
- [ ] Web UI (optional, stretch goal)

**Success Criteria:**

- Natural language queries work for common scenarios
- Playlists flow smoothly (energy curves, key transitions)
- User satisfaction in blind tests

---

## 🔧 Wymagania techniczne (v0.1)

```text
scikit-learn==1.3.0
pandas>=1.5
numpy>=1.24
```

## Dependencies

### Phase 1 (Genre Classifier)

```text
scikit-learn>=1.3.0
pandas>=2.0
numpy>=1.24
```

### Phase 2 (Smart Playlists, FUTURE)

```text
openai>=1.0  # or anthropic, for LLM query parsing
sentence-transformers>=2.2.2  # optional, for semantic search
```

## Training Data Format

**Input:** `data/training_dataset_full.csv` (from `ml-export-training-dataset`)

Key columns:

- Essentia features: `ess_bpm`, `ess_key_camelot`, `ess_energy`, `ess_danceability`, spectral features, MFCCs
- Label: `genre` (one of 30 from genres.yml)

## Model Metrics (`models/genre_metrics.json`)

```json
{
  "accuracy": 0.78,
  "macro_f1": 0.75,
  "per_genre": {
    "House": {"precision": 0.82, "recall": 0.79, "f1": 0.80},
    "Techno": {"precision": 0.85, "recall": 0.81, "f1": 0.83},
    ...
  },
  "confusion_matrix": "..."
}
```

## Prediction Output (`unsorted.xlsx` update)

New/updated columns:

- `genre_ml_predict`: Predicted genre
- `genre_ml_confidence`: Confidence score (0-1)
- `genre_suggest`: Fusion of ML + online sources (weighted)

## Edge Cases

- Low confidence (<0.7): Don't update `genre_suggest`, keep online sources
- Unknown features: Log to `LOGS/ml_prediction_errors.csv`
- Model not trained: Graceful fallback (show warning, skip ML predictions)
- Użytkownik otwiera plik CSV (np. w Excelu lub terminalu) i edytuje jeśli trzeba
- Poprawki zapisywane jako `exports/feedback.csv`
- Komenda CLI `python assign_buckets.py --feedback` retrainuje model z feedbacku

## 🎯 Ewaluacja UX (w CLI)

- Analiza `feedback.csv` pozwala wyliczyć % poprawionych predykcji
- Celem jest: **maks. 20% bucketów wymagających korekty** (`accept_ratio >= 80%`)
- Można wypisywać raport do terminala: `Correct: 41, Incorrect: 9, Accuracy: 82%`

## 🧠 Debug mode (opcjonalny dev tryb)

- `--debug` flag: wypisuje cechy wejściowe tracka + uzasadnienie predykcji do stdout
- Pomaga testować modele z komentarzem: „dlaczego bucket = X?”

---

## Instrukcje dla GitHub Copilot (VSCode Grok Mode)

- Pliki kodu są w `djlib/bucketing/`
- Klasa assignera ma: `train()`, `predict()`, `export_predictions_to_csv()` i `learn_from_feedback()`
- Cechy utworu = `{bpm, key, genre_1..3, artist, title}`
- Używamy `RandomForestClassifier` z `scikit-learn` na start (v0.1)
- Embeddingi SBERT są opcjonalne (v0.3) – tekst źródłowy jako `f"{artist} - {title}, {genres}, BPM {bpm}, key {key}"`
- Feedback to CSV z `track_id, correct_bucket`
- Wszystko działa w terminalu – użytkownik edytuje pliki CSV ręcznie
- Kod rozwijany bez branchy, wszystko w `main/dev`, commituj etapami wg roadmapy

---

Ten dokument to pełna specyfikacja techniczna modułu auto-bucketowania w DJ Library Manager. Wystarczy jako pojedyncze źródło wiedzy dla devów i narzędzi AI (Copilot, Grok).
