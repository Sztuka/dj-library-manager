# DJ Library Manager (MVP)

Quick start (local BPM/Key without Traktor):

See `docs/INSTALL.md` for installing Essentia (optional but recommended)

- Run tasks:
  - STEP 0 — Setup: create venv & install deps
  - TOOLS — Check audio env (verifies Essentia)
  - STEP 2 — Analyze audio (cache)
  - STEP 3 — Auto-decide (rules.yml) — only empty
  - STEP 4 — Apply decisions (dry-run)
  - STEP 5 — Apply decisions
- Generate a preview: `python scripts/report_preview.py` (adds detected bpm/key/energy columns)

**NEW: Local audio analysis with tag writing** (alternative to Traktor):

- Run `python -m djlib.cli sync-audio-metrics --write-tags` to analyze BPM/Key/Energy locally and write them to file tags
- This creates complete metadata in both cache and ID3 tags for DJ software compatibility

## Aktualny end‑to‑end workflow (rozszerzony)

Źródła gatunków online: MusicBrainz, Last.fm, (opcjonalnie) SoundCloud.

Nowe kolumny CSV: `genres_musicbrainz`, `genres_lastfm`, `genres_soundcloud`, `pop_playcount`, `pop_listeners`.

Flagi dla wzbogacania:

- `--force-genres` – nadpisuje istniejące kolumny `genres_*` oraz `genre_suggest` jeśli pojawiły się nowe / lepsze dane.
- `--skip-soundcloud` – pomija zapytania do SoundCloud (przydatne jeśli chwilowo brak ważnego `client_id`).

SoundCloud:

- Wymaga `SOUNDCLOUD_CLIENT_ID` w env albo w `config.local.yml`.
- Health check na początku `enrich-online` wykrywa: `ok`, `invalid` (403), `missing`, `rate-limit`, `error`.
- Jeśli `invalid` / `missing` i brak `--skip-soundcloud` → interaktywne pytanie czy kontynuować bez SoundCloud.

Wielokrotne nawiasy w nazwie pliku (np. `Artist - Title (Karibu Remix) (Extended Edit).mp3`) są łączone w `version_suggest`: `Karibu Remix, Extended Edit`.

Workflow:

1. Skopiuj nowe pliki do folderu inbox (domyślnie `~/Unsorted` lub skonfigurowany).
2. **W Traktorze** zrób Analyze (BPM + Key), potem **Write Tags to File**.  
   **LUB** użyj lokalnej analizy: `python -m djlib.cli sync-audio-metrics --write-tags`
3. Uruchom `scripts/scan_inbox.py` – powstanie/uzupełni się `library.csv`.
4. Otwórz `library.csv` i uzupełnij `target_subfolder`, np.:
   - `READY TO PLAY/CLUB/AFRO HOUSE`
   - `READY TO PLAY/OPEN FORMAT/PARTY DANCE`
   - `REVIEW QUEUE/UNDECIDED`
   - `REJECT`
5. Uruchom `scripts/apply_decisions.py` – pliki zostaną:
   - ponazywane: `Artist - Title (VersionInfo) [Key BPM].ext`
   - przeniesione do wskazanych folderów

## Multi-source genre enrichment

Komenda: `python -m djlib.cli enrich-online` (alias task "ROUND — 1")

Źródła i wagi w resolverze: Last.fm (6.0), MusicBrainz (3.0), SoundCloud (2.0). Wynik agregowany trafia do `genre_suggest`, a surowe listy do odpowiednich kolumn `genres_*`.

Przykład wymuszenia ponownego pobrania gatunków i pominięcia SoundCloud:

```bash
python -m djlib.cli enrich-online --force-genres --skip-soundcloud
```

## Runda zautomatyzowana

Szybki pipeline (scan + analyze + enrich + predict + export): task: `ROUND — 1) Analyze+Enrich+Predict+Export`.

- Runda zaczyna się od `scan`, aby odświeżyć `library.csv`. Jeśli jesteś pewny, że stan CSV jest aktualny, dodaj `--skip-scan`.
- Eksport XLSX zostanie świadomie pominięty (z komunikatem), jeśli po analizie nie ma wierszy do zaprezentowania.

Druga runda (import decyzji, apply, trening lokalny ML + QA): `ROUND — 2) Import+Apply+Train+QA`.

## XLSX export / import (podgląd + akceptacja)

Eksporter dodaje wszystkie kolumny, w tym `genres_*`, miary popularności i listę dozwolonych targetów jako dropdown. Zmiany w arkuszu można później zaimportować (ROUND 2) do CSV.

## Afro house / remix heuristics (planowane)

Plan: dodatkowe heurystyki dla afro house jeśli `version_suggest` zawiera wzorce typu `Karibu Remix`. (Jeszcze nie wdrożone.)

## Lokalne trenowanie ML + pętla feedback (Twoje buckety)

Chcesz, by ML proponował kubełki z Twojej taksonomii (`taxonomy.local.yml`) – nie z ogólnych FMA. Workflow:

1. Zadbaj o dane wejściowe

- Analiza audio (Essentia): `analyze-audio` (lub automatycznie podczas treningu).
- Gatunki z zewnątrz: `enrich-online` – pobiera gatunki (MB/Last.fm/SoundCloud*) i zapisuje do CSV (pole `genre_suggest`).
  *SoundCloud można pominąć flagą `--skip-soundcloud`.
- Popularność (opcjonalnie): `enrich-online` zapisze `pop_playcount` i `pop_listeners` z Last.fm (jeśli API KEY ustawiony).

2. Trening lokalnego modelu (na zaakceptowanych bucketach)

- Komenda: `ml-train-local`
- Zbiera wiersze z ustawionym `target_subfolder`, zapewnia analizę, buduje cechy (~80+) i trenuje model RF.
- Filtruje rzadkie klasy (`--min-per-class`, domyślnie 20). Model zapisuje się do `models/local_trained_model.pkl`.

3. Predykcja i bezpieczne progi

- `ml-predict` – użyj lokalnego modelu: `--model models/local_trained_model.pkl`.
- Progi: `--hard-threshold 0.85`, `--suggest-threshold 0.65`; zalecamy w ogóle nie sugerować przy conf < 0.40.

4. Feedback loop (ręczne korekty)

- Koryguj `ai_guess_*` lub ustaw `target_subfolder` ręcznie; kolejne treningi będą trafiać lepiej.
- Plan: dodać osobną komendę do zbierania poprawek i incremental retraining.

Uwagi: Docelowo rozdzielimy: (A) predykcję tagów/genres (uniwersalną) oraz (B) mapowanie tagów→buckety według Twojej taksonomii. Dzięki temu jeden model będzie działał u różnych użytkowników, a tylko mapowanie będzie lokalne.

## Konfiguracja

Przy pierwszym uruchomieniu aplikacja zapyta o ścieżki:

- **Library root**: folder główny biblioteki (domyślnie `~/Music Library`), gdzie będą tworzone podfoldery `READY TO PLAY/`, `REVIEW QUEUE/`, `LOGS/`, `library.csv`
- **Inbox dir**: folder z nowymi plikami do przeskanowania (domyślnie `~/Unsorted`)

Ścieżki są zapisywane w:

- Pliku konfiguracyjnym: `config.local.yml` (w repo) lub `~/.djlib_manager/config.yml`
- Ukrytych plikach znaczników dla automatycznego wykrywania:
  - `.djlib_root` w folderze library root (zawiera YAML z `library_root` i `inbox_dir`)
  - `.djlib_inbox` w folderze inbox (zawiera te same dane)

Aby zmienić konfigurację, uruchom ponownie aplikację - wykryje istniejące pliki znaczników i zapyta o potwierdzenie lub zmianę.

Zainstaluj zależności: `pip install -r requirements.txt`

(Opcjonalnie) fingerprint audio wymaga `pyacoustid` i narzędzia `fpcalc` (Chromaprint). Bez tego fingerprint będzie pusty, ale skan działa.

## Lokalna analiza audio

System wspiera lokalną ekstrakcję BPM/Key/Energy bez potrzeby Traktora:

- **Wymagania**: Essentia Python bindings (instalacja przez `scripts/install_essentia.py`)
- **Polecenie**: `python -m djlib.cli sync-audio-metrics --write-tags`
- **Rezultat**:
  - Metryki zapisane w cache SQLite (`LOGS/audio_analysis.sqlite`)
  - BPM/Key/Energy zapisane w tagach ID3 plików (Camelot notation dla kluczy)
  - Kompatybilne z DJ oprogramowaniem (Serato, rekordbox, itp.)

**Format kluczy**: Camelot notation (1A-12A, 1B-12B), zapisane jako ID3 TKEY tag.

**Przykład użycia**:

```bash
# Analiza i zapis do cache
python -m djlib.cli sync-audio-metrics

# Analiza + zapis do tagów plików
python -m djlib.cli sync-audio-metrics --write-tags

# Force re-analysis wszystkich plików
python -m djlib.cli sync-audio-metrics --force --write-tags
```

## Struktura CSV

Kolumny: patrz `djlib/csvdb.py::FIELDNAMES`.

## Uwaga

To jest MVP. Fingerprint działa tylko jeśli masz `fpcalc`. Brak `fpcalc` → duplikaty wykrywane po `file_hash`.
