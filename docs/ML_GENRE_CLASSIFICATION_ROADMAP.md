# ML Genre Classification — Roadmap

**Data:** 2026-02-09  
**Cel:** Zastąpić obecne API-based genre resolution (Beatport/Last.fm/MB/SoundCloud)
klasyfikacją opartą o analizę audio (Essentia features + ML model).

---

## Motywacja

Obecny system `genre_resolver.resolve()` opiera się na 4 zewnętrznych API:

| Źródło     | Problem                                                        |
| ---------- | -------------------------------------------------------------- |
| Beatport   | OAuth tokeny wygasają, rate limit, brak nie-elektronicznych     |
| Last.fm    | Tagowanie zależy od popularności — niszowe tracki mają noise    |
| MusicBrainz| Ubogi tagging gatunkowy, głównie szerokie kategorie             |
| SoundCloud | Scraping, brak oficjalnego API, tagowanie per-uploader          |

Problemy wspólne:
- **Kruchy łańcuch zależności** — każde API może zmienić format/limity
- **Blind spots** — afrobeats, alté, amapiano, regionalne gatunki słabo pokryte
- **Brak spójności** — każde źródło ma inną taksonomię
- **Czas** — enrichment jednego tracka to 5-15 requestów HTTP

Audio-based ML classification jest:
- **Self-contained** — zero zależności od zewnętrznych serwisów
- **Spójna** — jeden model, jedna taksonomia (genres.yml)
- **Szybka** — inference <1s/track vs 5-15s API chain
- **Skalowalna** — działa na dowolnej bibliotece bez rate limitów

---

## Stan obecny — co już jest zaimplementowane

### Audio feature extraction (WORKFLOW 4) ✅
- Plik: `djlib/audio/essentia_backend.py` (602 linii)
- **~80 features** per track:
  - Rytm: `bpm`, `bpm_conf`, `onset_rate`, `danceability`
  - Tonalność: `key_camelot`, `key_strength`, `chroma_0..11`, `tonnetz`
  - Timbr: `mfcc_0..12` (mean + std), `spec_centroid`, `spec_rolloff`, `spec_bandwidth`
  - Dynamika: `lufs`, `dyn_complex`, `energy`, `energy_var`
  - Tekstura: `spec_contrast`, `spec_flux`, `spec_flatness`, `hfc`, `zero_crossing_rate`
- Dwie ścieżki: Python Essentia (preferowana) lub CLI fallback (binary/Docker)
- Stan: **zaimplementowane, nigdy nie uruchomione**

### Analysis cache (SQLite) ✅
- Plik: `djlib/audio/cache.py`
- Lokalizacja: `LOGS/audio_analysis.sqlite`
- Schema: `audio_id` (SHA256) + ~15 typed columns + JSON `extras` blob
- Stan: **zaimplementowane, puste** (brak uruchomienia WORKFLOW 4)

### Dataset export (WORKFLOW 5) ✅
- Plik: `djlib/ml/export_dataset.py`
- Output: `data/training_dataset_full.csv`
- Łączy: features z audio cache + labels z `library.csv` (genre, target_subfolder)
- Kolumny: `ess_*` (features), `genre_label`, `bucket_label`, metadata
- Stan: **zaimplementowane, produkuje pusty CSV** (brak danych w cache)

### Model configs (placeholder) ⬜
- Plik: `djlib/ml/models.py`
- `GenreModelConfig` → `models/genre_model.pkl`
- `BucketModelConfig` → `models/bucket_model.pkl`
- Stan: **dataclassy only, brak kodu treningowego i predykcji**

### Historyczny model (usunięty) 🗑️
- Poprzedni model FMA-based: 48% accuracy na 6 klasach, 1200 samples
- Usunięty — został tylko `models/ml_bucket_metrics.json` z metrykami
- Nieużyteczny jako baseline (za mały zbiór, za mało klas)

---

## Plan implementacji

### Krok 1: Feature extraction — uruchomienie Essentia

**Wymagania:**
- Essentia zainstalowane (TOOLS — Install Essentia)
- Pliki audio dostępne na dysku

**Akcja:**
```bash
# Sprawdź środowisko
.venv/bin/python -m djlib.cli analyze-audio --check-env

# Uruchom analizę na całej bibliotece
.venv/bin/python -m djlib.cli analyze-audio
```

**Efekt:** `LOGS/audio_analysis.sqlite` zapełniony ~80 features per track.

**Szacowany czas:** ~2-5s per track (zależy od CPU). 1000 tracków ≈ 30-80 min.

---

### Krok 2: Dataset export

**Wymaganie:** Krok 1 zakończony.

**Akcja:**
```bash
.venv/bin/python -m djlib.cli ml-export-training-dataset
```

**Efekt:** `data/training_dataset_full.csv` z features + labels.

**Uwagi:**
- Labels pochodzą z `library.csv` → kolumna `genre`
- Tylko tracki z ZARÓWNO audio features I genre label trafią do datasetu
- Należy sprawdzić ile tracków ma ręcznie zweryfikowany genre vs auto-assigned

---

### Krok 3: Model training — NOWY KOD

**Do zaimplementowania w `djlib/ml/train.py`:**

```python
# Pseudokod — docelowa architektura
def train_genre_model(dataset_path: Path, genres_yml: Path) -> Path:
    """Train LightGBM genre classifier on Essentia features.
    
    Labels: genres.yml canonical labels (50 genres)
    Features: ~80 Essentia features (prefixed ess_*)
    Output: models/genre_model.pkl
    """
    df = pd.read_csv(dataset_path)
    
    # 1. Filter: only rows with valid genre_label
    # 2. Map genre_label → genres.yml canonical form
    # 3. Drop genres with < N samples (minimum viable class)
    # 4. Feature selection: ess_* columns
    # 5. Stratified train/test split (80/20)
    # 6. Train LightGBM multiclass classifier
    # 7. Per-genre precision/recall/F1 report
    # 8. Confusion matrix analysis
    # 9. Save model + metrics + label encoder
    
    return model_path
```

**Kluczowe decyzje:**
- **Algorytm:** LightGBM (szybki, dobry z tabular data, handles class imbalance)
- **Alternatywa:** XGBoost lub RandomForest jako baseline do porównania
- **Class imbalance:** Oversampling (SMOTE) lub class_weight='balanced'
- **Minimum samples per class:** 10-20 tracków per genre minimum
- **Feature engineering:** Rozważyć PCA na MFCCs, ratio features (centroid/rolloff)

**Metryki do śledzenia:**
- Weighted F1-score (uwzględnia class imbalance)
- Per-genre accuracy (które gatunki model myli?)
- Confusion matrix (np. czy myli afro house z tech house?)
- Top-2 accuracy (czy poprawny genre jest w top 2 predykcjach?)

---

### Krok 4: Rozszerzenie zbioru treningowego

**Źródła dodatkowych danych:**
1. **Biblioteki znajomych** — ten sam pipeline:
   - Skopiuj ich pliki audio (lub przeanalizuj remote)
   - `analyze-audio` → ich features do cache
   - Potrzebują zlabelowanego CSV z genre per track
   - `ml-export-training-dataset` → merge z naszym datasetem

2. **Iteracyjne labelowanie:**
   - Model predykuje genre na nowych trackach
   - Human review low-confidence predictions
   - Poprawione labels wracają do training set → retrain

**Format danych od znajomych:**
```csv
file_path,artist,title,genre
/path/to/track.mp3,Artist Name,Title,afro house
```
Labels MUSZĄ mapować się na `genres.yml` canonical forms.

---

### Krok 5: Integracja z genre_resolver

**Nowy scorer w `genre_resolver.py`:**

```python
def _score_essentia(
    file_path: str | None,
    scores: Dict[str, float],
) -> Optional[SourceScore]:
    """Score genre from audio features using trained ML model."""
    if file_path is None:
        return None
    
    from djlib.ml.models import load_genre_model
    model = load_genre_model()  # cached singleton
    
    from djlib.audio.cache import get_analysis
    features = get_analysis(file_path)
    if features is None:
        return None
    
    probabilities = model.predict_proba(features)
    # Top-N genres with their probabilities as scores
    ...
    return SourceScore(source="essentia", weight=WEIGHT_ESSENTIA, tags=local)
```

**Integracja z `resolve()`:**
- Nowe source name: `"essentia"` w `ALL_SOURCES`
- Waga do kalibracji: zacząć od `WEIGHT_ESSENTIA = 8.0` (podobnie do Beatport)
- `sources=` parameter pozwala włączać/wyłączać per-call
- Docelowo: `sources={"essentia"}` jako jedyne source

**Fazy wdrożenia:**
1. **Hybrid:** Essentia + API razem (Essentia jako dodatkowy głos w scoring)
2. **Essentia-primary:** `WEIGHT_ESSENTIA = 15.0`, API jako fallback
3. **Essentia-only:** Wyłączenie API, `sources={"essentia"}`

---

## Ryzyka i mitygacje

| Ryzyko | Mitygacja |
| ------ | --------- |
| Za mało labeled data na start | Zacząć od gatunków z >20 trackami, resztę zostawić API |
| Class imbalance (80% house/techno) | SMOTE / class_weight + stratified split |
| Model myli podobne gatunki | Hierarchical: najpierw category (electronic/urban/pop), potem subgenre |
| Essentia features nie wystarczą | Dodać audio embeddings (CLAP/MusicGen) jako alternatywę |
| Overfitting na małym zbiorze | Cross-validation 5-fold, early stopping |
| Drift po dodaniu danych znajomych | Walidacja na holdout set z NASZEJ biblioteki |

---

## LLM jako uzupełnienie (opcjonalne)

Niezależnie od audio ML, LLM może służyć jako **fallback dla edge cases**:

**Use case:** Model daje niską confidence LUB generyczny tag (dance, electronic).

**Implementacja:**
```python
def _score_llm(artist: str, title: str, version: str) -> Optional[SourceScore]:
    """Ask LLM for genre classification when other sources fail."""
    # Only called when confidence < threshold
    # Constrained output: must pick from genres.yml labels
    # Cached per (artist, title) — one-time cost
    # Cost: ~$0.005/track with GPT-4o-mini
```

**Zalety:** Zna artystów, sceny, kontekst kulturowy (rozwiązuje case Amaarae/afrobeats).  
**Wady:** Halucynacje, koszt, zależność od API.  
**Rekomendacja:** Traktować jako dodatkowe source w scoring, nie wyrocznię.

---

## Priorytety

| Priorytet | Krok | Zależność | Szacowany effort |
| --------- | ---- | --------- | ---------------- |
| **P0** | Krok 1: Uruchom Essentia | Instalacja Essentia | 1-2h (czas analizy) |
| **P0** | Krok 2: Export dataset | Krok 1 | 5 min |
| **P1** | Krok 3: Training code | Krok 2 | 1-2 dni dev |
| **P2** | Krok 4: Dane znajomych | Krok 3 | Zależy od dostępności |
| **P2** | Krok 5: Integracja | Krok 3 | 0.5 dnia dev |
| **P3** | LLM fallback | Krok 5 | 0.5 dnia dev |

---

## Zależności techniczne

```
lightgbm          # gradient boosting classifier
scikit-learn       # preprocessing, metrics, train_test_split
pandas             # dataset manipulation
joblib             # model serialization (already in sklearn)
# Optional:
imbalanced-learn   # SMOTE for class imbalance
matplotlib         # confusion matrix visualization
```
