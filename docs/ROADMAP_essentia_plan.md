# DJ Library Manager — Roadmap (Essentia‑first, non‑commercial)

Dokument opisuje plan przejścia na lokalną analizę audio (Essentia) jako źródło prawdy dla BPM i Key, z rozszerzeniem o Energy i cechy niskopoziomowe, oraz integrację z modułem Auto‑Bucket. Zakładamy tryb niekomercyjny (prywatny / potencjalnie open‑source w przyszłości), dlatego optymalizujemy jakość i nie ograniczamy się copyleftem.

## 1) Założenia i cele (aktualizacja 2025 Q4)

- Priorytet jakości: BPM i Key z Essentia (TempoTap/RhythmExtractor, KeyExtractor) → rezygnujemy z Traktora.
- Energia + cechy audio: lokalne metryki (LUFS, Dynamic Complexity, Spectral Centroid/Rolloff, Onset Rate) → baza do „energy score”.
- Bucketowanie: najpierw deterministyczne reguły (v0), potem klasyczny ML (v0.1), opcjonalnie hybryda z embeddingiem (v0.3).
- Sieć (MB/Last.fm/SoundCloud*): pozostaje pomocnicza; główne decyzje (BPM/Key/Energy/Bucket) oparte o audio. (*SoundCloud opcjonalny, z health check i możliwością pominięcia.)
- Caching, powtarzalność i audyt: analiza deterministyczna, cache po hash pliku + wersja algorytmu.

## 2) Architektura modułów

```
djlib/
  audio/                # NOWE: analiza audio
    __init__.py
    essentia_backend.py # wywołania Essentia (BPM/Key/Energy, cechy)
    features.py         # normalizacja, sampling, korekty 0.5×/2×, energy score
    cache.py            # cache SQLite/JSON + wersjonowanie algorytmu
  tags.py               # odczyt tagów (bpm, key, …)
  metadata/             # MB/LFM/SoundCloud (opcjonalne)
  genre_resolver.py     # z filtrami noise + multi-source wagi (MB/LFM/SP/SC)
  bucketing/
    base.py             # interfejsy
    rules.py            # v0 reguły deterministyczne bucketowania
    simple_ml.py        # v0.1 RandomForest (wg zał. planu)
    hybrid_model.py     # v0.3 (opcjonalnie)
  cli.py                # komendy CLI (analyze-audio, report-preview, assign-buckets, …)
  csvdb.py              # CSV (library.csv)
  config.py             # ścieżki, ustawienia
scripts/
  report_preview.py     # rozszerzymy o bpm_detected/key_detected/energy + per-source genres (DONE)
  assign_buckets.py     # CLI do bucketowania (v0.1)
```

## 3) Pipeline end‑to‑end

1. Konfiguracja (LIB_ROOT/INBOX) — jak dziś.
2. Scan (scan INBOX) → `library.csv` (tagi bazowe).
3. Analyze Audio (NOWE):
   - `detect_bpm_essentia(path) -> bpm, conf, corrected_factor`
   - `detect_key_essentia(path) -> key_camelot, strength`
   - `compute_energy(path) -> energy_score (0..1), {lufs, dyn_complexity, onset_rate, spectral_*}`
   - Caching: (file_hash, algo_version) → result
4. Preview / CSV (rozszerzone – STATUS: częściowo DONE):
   - `tag_bpm`, `bpm_detected`, `bpm_confidence`, `bpm_correction`
   - `tag_key_camelot`, `key_detected_camelot`, `key_strength`
   - `energy_score` + składowe

- `genre_main/sub*` (opcjonalnie), `bucket_suggest`
- per-source gatunki: `genres_musicbrainz`, `genres_lastfm`, `genres_soundcloud` (DONE)

**Uwaga:** CLI `round-1` orkiestruje kroki 2–4 automatycznie (wymuszony `scan` → analiza → enrichment) i pomija eksport XLSX, jeśli nie ma czego pokazać.

5. Bucket v0 (reguły deterministyczne):
   - Mapuj na podstawie BPM (zakresy), Key (tryb A/B), Energy (progi), perkusyjności i prostych heurystyk.
6. Auto‑Bucket v0.1 (ML, wg załącznika):
   - Cechy: `{bpm_detected, key_detected, energy_score, genre tokens}`
   - Model: RandomForest + metryki + eksport `bucket_predictions.csv`
7. Feedback i ewaluacja (później):
   - `feedback.csv` → retrain, `metrics.json`, akceptacja ≥ 80%.

## 4) Essentia — szczegóły integracji

- Instalacja (dev):
  - macOS: Homebrew `brew install essentia` (lub Conda). W niektórych konfiguracjach wymagany FFmpeg/FFTW.
  - Python: test importu w CLI (`analyze-audio --check-env`) — jasny raport co działa/co brakuje, z instrukcją.
  - Fallback (opcjonalny): jeśli Essentia niedostępna, można (świadomie) włączyć backend librosa/madmom, ale z ostrzeżeniem jakościowym.
- Wydajność:
  - Sampling: mono, 22.05 kHz, środkowe 30–60 s; skip intro/outro.
  - Równoległość: analiza w procesach/workerach (CPU‑bound), limit równoległości vs I/O.
- BPM:
  - TempoTap/RhythmExtractor2013 → tempo podstawowe.
  - Korekta harmoniczna: jeśli < 80 → ×2, jeśli > 180 → ÷2; następnie wybór najbliższej.
  - Zwracamy `(bpm, confidence≈stability, correction_factor)`.
- Key:
  - KeyExtractor (HPCP) → (pitch class, mode) → Camelot A/B.
  - Zwracamy `(key_camelot, strength)`.
- Energy:
  - `loudnessEBUR128`, `DynamicComplexity`, `SpectralCentroid/Rolloff`, `OnsetRate` → normalizacja do `energy_score∈[0,1]`.

### 4.1) Parametry domyślne i definicje

- Dekodowanie: 1‑kanał (mono), `sr=22050`, normalizacja głośności do stabilnej skali.
- Okna analizy: domyślnie 3×15 s z pozycji 25%/50%/75% długości; wynik = mediana (BPM) / średnia (Energy) / tryb (Key z wagą siły).
- BPM target range: `[80, 180]`; korekta 0.5×/2× aż do najbliższej wartości w zakresie; log `correction_factor` (1.0, 0.5, 2.0).
- BPM confidence: pochodna stabilności inter‑beat‑interval (np. 1 − CV), z [0..1].
- Key strength: skala [0..1] z Essentia/wyliczona z HPCP (normalizacja wewnętrzna).
- Energy score: złożenie znormalizowanych składowych: `w1*norm(LUFS) + w2*norm(DynComplexity) + w3*norm(OnsetRate) + w4*norm(Centroid/Rolloff)`; domyślnie równe wagi (kalibrowalne).
- Key→Camelot: tabela mapowania (preferencja # vs b ustawialna w config); A=minor, B=major.

### 4.2) Kalibracja i stabilność

- Kalibracja Energy: wstępne percentyle (P10/P90) na Twojej bibliotece zapisujemy do `metrics.json`, by wyniki były porównywalne między sesjami.
- Stabilność BPM/Key: jeśli rozjazd między oknami > progów (np. BPM różni się >3%), log do `LOGS/unstable_analysis.csv` i niższa confidence.
- Niewspierane/pliki problematyczne (dekoder, cisza, < 20 s): log do `LOGS/failed_decodes.csv`.

## 5) Bucketowanie — ścieżki (bez zmian funkcjonalnych w tej aktualizacji)

- v0 (Reguły):
  - Przykład: `house|tech house` + `120–128 BPM` + `energy≥0.6` → `READY TO PLAY/HOUSE BANGERS`;
    `downbeat|electronica` + `70–100 BPM` + `energy≤0.4` → `CHILL/OPENING`.
  - Reguły trzymamy w `bucketing/rules.py`, konfigurowalne w YAML.
- v0.1 (ML):
  - Zgodnie z dokumentem `auto_bucket_module_plan.md` i `auto_bucket_todo_list.md` — integracja cech z audio.
- v0.3 (Hybryda):
  - SBERT embedding tekstu + cechy audio, klas. MLP/XGBoost.

## 6) CLI i UX (rozszerzenia wdrożone)

- `djlib.cli analyze-audio` — analiza całej INBOX (z cache), progres i metryki czasu.
- `scripts/report_preview.py` — kolumny: `bpm_detected`, `bpm_confidence`, `bpm_correction`, `key_detected_camelot`, `key_strength`, `energy_score`, per-source genres (DONE).
- `scripts/assign_buckets.py` — predykcja bucketów (v0.1), eksport `bucket_predictions.csv`.
- Tryb `--debug`: zapis cech/uzasadnień do LOGS/.

### 6.1) Flagi CLI (proponowane)

- `analyze-audio [--workers N] [--recompute] [--window middle|segments=3x15s] [--target-bpm 80:180] [--check-env]`
  - `--check-env`: szybki test importu Essentia/FFmpeg/FFTW + wersje.
  - `--recompute`: ignoruj cache (np. po zmianie algorytmu).
  - `--workers`: równoległość CPU; domyślnie min(liczba rdzeni, 4).
- `report-preview [--compute-missing-only] [--with-breakdown]`
  - `--compute-missing-only`: nie licz BPM/Key/Energy jeśli poprawne tagi już istnieją.
  - `--with-breakdown`: dodatkowe kolumny ze składowymi energy i debug BPM/Key.
- `assign-buckets [--rules path.yml] [--model models/bucket_model.pkl] [--debug]`

## 7) Caching i wersjonowanie algorytmu

- Identyfikator pliku: `audio_id` oparty o chromaprint (jeśli dostępny) lub szybki xxhash pierwszych X MB + (size, mtime).
- Klucz cache: `(audio_id, algo_version, config_hash)`.
- `algo_version`: inkrement przy zmianach algorytmu; `config_hash`: hasz istotnych parametrów (zakres BPM, okna, wagi energy, preferencja #/b).
- Format: SQLite (zalecany) — tabela `audio_analysis`:

  - `audio_id TEXT PRIMARY KEY`
  - `algo_version INT`
  - `config_hash TEXT`
  - `bpm REAL`, `bpm_conf REAL`, `bpm_corr REAL`
  - `key_camelot TEXT`, `key_strength REAL`
  - `lufs REAL`, `dyn_complex REAL`, `onset_rate REAL`, `spec_centroid REAL`, `spec_rolloff REAL`
  - `energy REAL`, `energy_var REAL`
  - `analyzed_at TEXT`

- API cache: get/set z walidacją wersji/konfiguracji; `--recompute` pomija cache.

## 8) Jakość: metryki i testy

- Testy jednostkowe: parsowanie, konwersje Camelot, korekty 0.5×/2×, mapping do bucketów, filtry noise.
- Testy integracyjne: kilka próbek audio (krótkie fragmenty) + snapshot wyników.
- Metryki jakości: porównanie z referencją (jeśli masz plik z „prawdą”) → raport do `metrics.json`.

### 8.1) Testy bez prawdziwych plików

- Generuj syntetyczne próbki audio (np. metronom dla 90/120/128 BPM; sinus/wavetable dla wybranych tonacji).
- W testach integracyjnych: sprawdź korekcję BPM (×2/÷2), poprawność mapowania Key→Camelot, stabilność energy.
- Nie wrzucamy prawdziwej muzyki do repo.

## 9) Licencje i dystrybucja

- Essentia: w trybie niekomercyjnym OK; w razie Open Source — respektujemy AGPL lub rozważamy opcję komercyjną w przyszłości.
- Modele ML (jeśli użyjemy): audyt licencji każdego modelu (autotagging), lub BYO model przez użytkownika.

## 10) Roadmap — kroki wykonawcze (progress markers)

- Faza A (BPM/Key/Energy + Preview) – PARTIAL DONE (część Energy jeszcze do kalibracji)
  1. `djlib/audio/essentia_backend.py`: detektory BPM/Key/Energy + cache. (IN PROGRESS)
  2. Integracja z `report_preview.py` (kolumny + wskaźniki jakości, bez regresji czasów). (PARTIAL DONE)
  3. CLI `analyze-audio` + logi.
- Faza B (Bucket v0 — reguły) 4. `bucketing/rules.py` + starter `rules.yml` (zakresy BPM, energy, opcjonalnie style z taxonomy_map). 5. Dodanie do Preview `bucket_suggest` + `bucket_confidence`. (PENDING)
- Faza C (Auto‑Bucket v0.1 — ML) 6. `bucketing/simple_ml.py` (RandomForest) — jak w załączniku; cechy z audio. 7. `assign_buckets.py` + `metrics.json` + testy. (PENDING)
- Faza D (Hybryda — opcjonalnie) 8. `hybrid_model.py` (SBERT + cechy), porównanie z v0.1. (FUTURE)

## 11) Kryteria „gotowości” (Definition of Done) – aktualizacja

- DoD A: Preview CSV pokazuje wykryty BPM/Key/Energy na całym INBOX, per-source genres; czas analizy akceptowalny; cache działa, brak błędów I/O.
- DoD B: Reguły bucketów dają sensowne propozycje na Twoich danych, edytowalne w YAML; raport akceptacji ≥ ustalonego progu.
- DoD C: ML v0.1 osiąga ≥ X% accuracy na Twoim zbiorze, metryki zapisane i reprodukowalne.

---

## 13) Nowe elementy zrealizowane poza pierwotnym planem

- Multi-source genre enrichment (MB / Last.fm / SoundCloud) z wagami.
- Per-source kolumny `genres_*` + popularność (`pop_playcount`, `pop_listeners`).
- Interaktywny prompt przy nieważnym SoundCloud client id + flaga `--skip-soundcloud`.
- Wielokrotne nawiasy w nazwie pliku → łączone jako lista w `version_suggest`.
- Remix-aware SoundCloud zapytania (przekazywanie `version` z CLI/resolvera, filtrowanie Extended/Radio/Remix tokenów).
- `round-1` jako w pełni zautomatyzowany pipeline: zawsze rozpoczyna od `scan`, posiada `--skip-scan`, oraz blokadę eksportu pustych XLSX.

## 14) Backlog dodatków (proponowane)

- Heurystyka afro house (np. wzorzec "Karibu Remix") → podbicie wagi przy bucketowaniu klubowym.
- Persist decyzji użytkownika o pominięciu SoundCloud w `enrich_status.json`.
- Kalibracja Energy (percentyle) + wizualizacja w raporcie.
- ML bucketowanie v0.1 + metryki (accuracy, precision, recall per bucket).
- Cache dla SoundCloud odpowiedzi + obsługa soft rate-limit.

---

Jeśli potwierdzasz kierunek, kolejne kroki: finalizacja Energy + reguły bucketów v0, potem moduł ML.

## 12) Starter `rules.yml` (przykład)

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
