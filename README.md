# DJ Library Manager (MVP)

Quick start (local BPM/Key without Traktor):

See `docs/INSTALL.md` for installing Essentia (optional but recommended)

1. **Setup deps** – task _STEP 0 — Setup: create venv & install deps_ (or run the equivalent commands manually).
2. **Verify audio toolchain** – task _TOOLS — Check audio env_ (runs `python -m djlib.cli analyze-audio --check-env`).
3. **Scan INBOX → `unsorted.xlsx`** – task _WORKFLOW 1 — Scan UNSORTED_ (`python -m djlib.cli scan`).
4. **Analyze BPM/Key/Energy** – task _WORKFLOW 2 — Analyze audio (Essentia)_ (`python -m djlib.cli analyze-audio`).
   - Optional: `python -m djlib.cli sync-audio-metrics --write-tags` updates both the cache and the ID3 tags.
5. **Edit `unsorted.xlsx`** – fill `artist`/`title`/`version_info`/`genre`, pick `target_subfolder`, set `done = TRUE` only for tracks ready to export.
6. **Export approved tracks** – task _WORKFLOW 3 — Export approved tracks_ (`python -m djlib.cli apply`).
7. **(Optional) Build ML dataset** – task _WORKFLOW 4 — ML dataset export_ (`python -m djlib.cli ml-export-training-dataset`).
8. Generate a preview any time with `python scripts/report_preview.py` (adds detected bpm/key/energy columns).

**Local audio analysis with tag writing** (alternative to Traktor):

- `python -m djlib.cli sync-audio-metrics --write-tags` analyzes BPM/Key/Energy locally and writes them to file tags.
- Results land both in the Essentia cache and in ID3 tags, so any DJ software can read them immediately.

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

Szybkie kroki:

1. `python -m djlib.cli scan` – zapełnia `unsorted.xlsx`.
2. `python -m djlib.cli analyze-audio` – Essentia cache (`LOGS/audio_analysis.sqlite`).
3. Edytuj `unsorted.xlsx`, wyznacz docelowy bucket, ustaw `done = TRUE`.
4. `python -m djlib.cli apply` – przenosi pliki i dopisuje do `library.csv`.
5. (Opcjonalnie) `python -m djlib.cli ml-export-training-dataset` – buduje `data/training_dataset_full.csv`.

## Multi-source genre enrichment

Komenda: `python -m djlib.cli enrich-online`

Źródła i wagi w resolverze: Last.fm (6.0), MusicBrainz (3.0), SoundCloud (2.0). Wynik agregowany trafia do `genre_suggest`, a surowe listy do odpowiednich kolumn `genres_*`.

Przykład wymuszenia ponownego pobrania gatunków i pominięcia SoundCloud:

```bash
python -m djlib.cli enrich-online --force-genres --skip-soundcloud
```

## Workflow (unsorted.xlsx → library.csv)

1. **Scan UNSORTED**  
   Uruchom `python -m djlib.cli scan` (VS Code task: _WORKFLOW 1 — Scan UNSORTED_).  
   Komenda przeszukuje folder INBOX, liczy fingerprinty, zbiera podpowiedzi z Last.fm/MusicBrainz/SoundCloud i zapisuje wszystko do `unsorted.xlsx` (w `LIB_ROOT`).  
   Arkusz zawiera:

   - kolumny techniczne (track_id, file_path, file_hash, fingerprint, added_date, is_duplicate) – domyślnie ukryte,
   - oryginalne tagi z pliku (`tag_*`), listy gatunków z usług online, pola popularności,
   - sugestie AI/reguł (`ai_guess_bucket`, `ai_guess_comment`),
   - pola do edycji ręcznej: `artist`, `title`, `version_info`, `genre`, `target_subfolder`, `must_play`, `occasion_tags`, `notes`,
   - podstawowe metryki audio (`bpm`, `key_camelot`, `energy_hint`),
   - **kolumnę `done` na końcu arkusza** – dropdown TRUE/FALSE pełniący rolę checkboxa.
     Dropdown dla `target_subfolder` korzysta z aktualnej taksonomii, więc unikamy literówek.

2. **Analyze audio (Essentia)**  
   `python -m djlib.cli analyze-audio` (VS Code: _WORKFLOW 2 — Analyze audio (Essentia)_) liczy cechy i zapisuje je do cache (`LOGS/audio_analysis.sqlite`). Możesz opcjonalnie uruchomić `sync-audio-metrics`, aby przepisać BPM/Key/Energy do arkusza.

3. **Manual edits w `unsorted.xlsx`**  
   Otwórz arkusz w Excelu/Numbers/LibreOffice i uzupełnij `artist`, `title`, `version_info`, `genre`, `target_subfolder`, `must_play`, `occasion_tags`, `notes`. Gdy utwór jest gotowy, ustaw `done = TRUE`. Wszystko, co ma `FALSE`, pozostaje w stagingu.

4. **Export approved tracks do `library.csv`**  
   `python -m djlib.cli apply` (VS Code: _WORKFLOW 3 — Export approved tracks_) bierze tylko wiersze z `done = TRUE`, przenosi pliki do docelowych folderów, zapisuje finalne tagi i dopisuje rekordy do `library.csv`. Wiersze z wyeksportowanych utworów znikają z arkusza.

5. **ML dataset export**  
   `python -m djlib.cli ml-export-training-dataset` (VS Code: _WORKFLOW 4 — ML dataset export_) łączy cechy Essentii z `library.csv` i zapisuje `data/training_dataset_full.csv`.
   - `genre` → `genre_label`.
   - `target_subfolder` → `bucket_label`.
   - Dodajemy dodatkowe kolumny `library_*` (np. `library_bpm`, `library_pop_playcount`) jako cechy pomocnicze.
   - Więcej szczegółów: `docs/ML_PIPELINE.md`.

## Afro house / remix heuristics (planowane)

Plan: dodatkowe heurystyki dla afro house jeśli `version_suggest` zawiera wzorce typu `Karibu Remix`. (Jeszcze nie wdrożone.)

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
