# Auto-Bucket TODO (lokalne buckety + przyszłościowa architektura)

## Teraz (użytkownik lokalny)

- [ ] CLI: `ml-train-local` – trenowanie modelu na zaakceptowanych bucketach użytkownika (CSV → cechy Essentia → RF). [NOW]
- [ ] Progi bezpieczeństwa predykcji – nie sugerować nic przy conf < 0.40; `--suggest-threshold` 0.65, `--hard-threshold` 0.85. [NOW]
- [ ] `enrich-online`: zapisywać popularność z Last.fm (`pop_playcount`, `pop_listeners`) do CSV (jeśli API Key). [NOW]
- [ ] Raport z treningu: `ml_bucket_metrics.json` + confusion matrix (dołączone klasy). [SOON]

## Wkrótce (lepsze cechy i hybryda reguły+ML)

- [ ] Dodać cechy rytmiczne (tempo confidence, beat histogram) do wektora ML. [SOON]
- [ ] Ensemble: reguły (`placement.decide_bucket`) + ML + głosy gatunków (MB/LFM/SoundCloud) – łączenie z wagami. [SOON]
- [ ] Wykorzystać popularność: progi singalong/party dance oraz decade buckety (80s/90s/…): (rok w metadanych + popularność). [SOON]

## Przyszłościowo (multi-user)

- [ ] Rozdzielić: (A) predykcję tagów/genres (multi-label) vs. (B) mapowanie `genres → buckety` per-user (taxonomy_map.yml). [FUTURE]
- [ ] Kalibracja confidence (isotonic/Platt) per-user. [FUTURE]
- [ ] Feedback loop: CLI do zbierania poprawek i incremental retraining (z wersjonowaniem modelu). [FUTURE]

## Popularność – skąd dane?

- Last.fm API – track.getInfo: `playcount`, `listeners` (już wspieramy, wymaga API key w configu).
- YouTube – liczba wyświetleń dla oficjalnego audio/clipu (opcjonalne; scraping/YouTube Data API v3).
- (Mniej priorytetowe) Wikipedia/Chart archives (peak positions), Discogs (liczby „have/want”), TheAudioDB.

Heurystyka przykładowa: jeśli `year ∈ [1980..1989]` i `playcount` lub `listeners` > próg — preferuj `OPEN FORMAT/80s`; bardzo wysokie wartości → `OPEN FORMAT/PARTY DANCE`/`POLISH SINGALONG` (po filtrze języka/pochodzenia).
<parameter name="filePath">/Users/sztuka/Projects/dj-library-manager/docs/auto_bucket_todo_list.md
