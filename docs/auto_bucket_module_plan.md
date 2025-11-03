# Moduł automatycznego przypisywania utworów do bucketów – Plan + Roadmap

## Cel modułu
Automatyzacja przypisywania utworów do "bucketów" (np. warm-up, banger, deep) na podstawie metadanych i/lub cech audio, z możliwością uczenia się preferencji użytkownika. Moduł generuje CSV z propozycjami bucketów, które użytkownik może zatwierdzić lub poprawić.

## Główne funkcje
- Analiza danych utworu: tytuł, artysta, gatunki (z 3 źródeł), BPM, tonacja, fingerprint
- Predykcja bucketu dla każdego utworu
- Eksport predykcji do pliku CSV
- Możliwość testowania skuteczności podejścia
- Możliwość dostrajania modelu (uczenie z akceptowanych/zmienionych etykiet)

## Podejście główne: klasyczny ML (v0.1), hybryda z embeddingiem (v0.2+)

### `SimpleMLBucketAssigner` (v0.1 MVP)
**Opis:**
Klasyfikator RandomForest trenowany na cechach BPM, key, gatunkach (tokeny lub one-hot). Trenowany na lokalnym zbiorze z feedbackiem użytkownika.

**Architektura:**
- `SimpleMLBucketAssigner` (w `bucketing/simple_ml.py`)
- Używa `scikit-learn==1.3.0`
- Input: dict z cechami tracka `{bpm, key, genre_tokens}`
- Output: `predicted_bucket`
- CSV export: `bucket_predictions.csv`

### (Opcjonalnie v0.2+) `HybridBucketAssigner`
**Opis:**
Rozszerzenie modelu o embedding tekstowy (SBERT – `all-MiniLM-L6-v2`). Opis utworu przekształcany na wektor, łączony z cechami numerycznymi, klasyfikowany przez MLP lub XGBoost.

**Architektura:**
- `HybridBucketAssigner` (w `bucketing/hybrid_model.py`)
- Użycie `sentence-transformers`, `scikit-learn`, `xgboost`

## Lokalizacja w repozytorium
```
djlib/
└── bucketing/
    ├── __init__.py
    ├── base.py              # interfejs wspólny
    ├── simple_ml.py         # v0.1 model
    └── hybrid_model.py      # v0.2 opcjonalnie
```

## Interfejs klas
```python
class BucketAssigner:
    def train(self, labeled_tracks: List[Dict]): ...
    def predict(self, track: Dict) -> str: ...
    def export_predictions_to_csv(self, tracks: List[Dict], path: str): ...
    def learn_from_feedback(self, feedback_csv_path: str): ...  # v0.2
```

## TO-DO (Roadmap v0.1 - v0.3)

### ✅ v0.1 – MVP (Simple ML Bucket Assigner)
- [ ] Utwórz klasę `SimpleMLBucketAssigner`
- [ ] Ekstrakcja cech: BPM, key, genre tokens
- [ ] Parser CSV wejściowego (z bucketami)
- [ ] Trenowanie modelu (RandomForestClassifier)
- [ ] Funkcja `predict()` dla jednego lub batcha tracków
- [ ] Eksport CSV z wynikami
- [ ] Testy jednostkowe w `tests/test_simple_ml.py`
- [ ] Pomiar metryk (accuracy, f1), zapis do `metrics.json`
- [ ] README z wynikami

### 🔁 v0.2 – Feedback loop (personalizacja)
- [ ] Parser feedbacku `track_id, true_bucket`
- [ ] Aktualizacja zbioru uczącego + retrain
- [ ] Ocena poprawy (przed/po)

### 🧠 v0.3 – Hybryda z SBERT embeddingiem
- [ ] Implementacja SBERT (`all-MiniLM-L6-v2`)
- [ ] Generowanie embeddingu na bazie `{artist} - {title}, genre, bpm, key`
- [ ] Concatenacja z cechami numerycznymi
- [ ] Nowy klasyfikator (MLP / RF)
- [ ] Porównanie skuteczności z modelem v0.1

---

## 🔧 Wymagania techniczne (v0.1)
```
scikit-learn==1.3.0
pandas>=1.5
numpy>=1.24
```

### (v0.2+):
```
sentence-transformers==2.2.2
xgboost>=1.7
```

## 📁 Format pliku treningowego `training.csv`
| track_id | artist | title | bpm | key | genre_1 | genre_2 | genre_3 | fingerprint | bucket |
|----------|--------|-------|-----|-----|---------|---------|---------|-------------|--------|

## 📁 Format feedbacku `feedback.csv`
| track_id | correct_bucket |
|----------|----------------|

## 📈 Metryki modelu (plik `metrics.json`)
```json
{
  "accuracy": 0.86,
  "macro_f1": 0.83,
  "confusion_matrix": [[23, 4, 1], [3, 25, 2], [1, 2, 29]]
}
```

## 🪵 Logowanie predykcji
- Każda predykcja zapisywana w `bucket_predictions.csv`
- Format: `track_id, predicted_bucket, probability` (jeśli klasyfikator wspiera)
- Opcjonalnie: `explanation` (np. top3 cechy decyzyjne z RandomForest)

## ⚠️ Obsługa przypadków brzegowych i fallbacków
- Jeśli BPM = 0 lub brak: przypisz `bucket = 'unsure'`
- Jeśli brak gatunków: bucket = 'unsure'
- Jeśli klasyfikator zwraca niskie prawdopodobieństwo (<0.5): bucket = 'unsure'
- Edge case'y logujemy do `logs/low_confidence.csv`

## 💾 Model persistence
- Model zapisujemy do `models/bucket_model.pkl`
- Każde `train()` lub `learn_from_feedback()` nadpisuje plik
- TODO: rozważyć wersjonowanie hash + timestamp przy dużej liczbie iteracji

## 🔌 Integracja CLI z aplikacją DJ Library Manager
- `assign_buckets.py` uruchamiany w terminalu przetwarza tracki i zapisuje `exports/bucket_predictions.csv`
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

