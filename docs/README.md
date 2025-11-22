# DJ Library Manager

Lokalny pomocnik do **porządkowania biblioteki DJ-a**: skanuje INBOX, sugeruje docelowe kubełki (foldery), robi „dry-run” i bezpieczne przenosiny z opcją **undo**. Działa pod **Rekordbox** / granie z dysku/pendrive (foldery), nie ingeruje w Twoje playlisty.

## Features (MVP / Extended)

- **Setup Wizard (web)** – 3 kroki: _Lokalizacja_ → _Taksonomia_ → _Foldery & Skan_.
- **Struktura dysku**: `READY TO PLAY` (CLUB / OPEN FORMAT / MIXES) oraz `REVIEW QUEUE` (UNDECIDED / NEEDS EDIT).
- **Scan → CSV** – metadane audio (rozmiar, SHA256, tagi; BPM/key jeśli już w pliku) + wielokrotne nawiasy w nazwie pliku konsolidowane do `version_suggest`.
- **Auto-decide** – zasady z `rules.yml` (na razie proste reguły) + plan rozszerzenia o wagi heurystyczne (np. afro house remix tokens).
- **Apply (dry-run / real)** – przenosi pliki do docelowych kubełków; **Undo** cofa ostatnie przenosiny.
- **Zero „podłóg”** – nazwy kubełków/folderów z **przerwami, UPPERCASE**.

## Wymagania

- macOS (testowane), Python **3.11+** (3.13 OK).
- `fpcalc` (Chromaprint) – do fingerprintów.
  - Tryb OFFLINE: aplikacja szuka binarki najpierw w `bin/mac/fpcalc` (w repo/bundlu). Jeśli plik tam leży i ma prawa wykonywania, fingerprint działa bez internetu.
  - Alternatywnie: ustaw zmienną `ACOUSTID_FPCALC` na pełną ścieżkę do binarki.
  - Tryb ONLINE (opcjonalny): można skorzystać z tasków: **TOOLS — Install fpcalc (Homebrew)** lub **TOOLS — Install fpcalc (Download vendor)**.

Uwaga: jeśli system zgłasza komunikat o „quarantine”, aplikacja spróbuje zdjąć atrybut automatycznie; w razie potrzeby możesz nadać prawa wykonania ręcznie: `chmod +x bin/mac/fpcalc`.

## Szybki start (Tasks w VS Code)

1. **STEP 0 — Setup: create venv & install deps**
2. **TOOLS — Install Essentia (Homebrew)** (opcjonalnie) oraz **TOOLS — Check audio env**.
3. **WORKFLOW 1 — Scan UNSORTED**: zbiera fingerprinty/metadane i aktualizuje `unsorted.xlsx`.
4. **WORKFLOW 2 — Analyze audio (Essentia)**: liczy cechy i zapisuje je do cache (`LOGS/audio_analysis.sqlite`).
5. Edytuj `unsorted.xlsx` – uzupełnij `artist`/`title`/`genre`/`target_subfolder`, oznacz wiersze `done = TRUE`.
6. **WORKFLOW 3 — Export approved tracks** (`python -m djlib.cli apply`): przenosi tylko wiersze z `done = TRUE`, zapisuje finalne tagi i dopisuje rekordy do `library.csv`.
7. **WORKFLOW 4 — ML dataset export** (`python -m djlib.cli ml-export-training-dataset`): tworzy `data/training_dataset_full.csv` na podstawie cache Essentii i `library.csv`.
8. Testy: _TESTS — run_ / _TESTS — coverage_ (opcjonalnie przed commitem).

## How-to: praca z `unsorted.xlsx`

1. **Po skanie sprawdź status**

- `LOGS/scan_status.json` pokaże liczbę plików i ewentualne błędy (`missing_fpcalc`).
- Jeśli pojawiły się duplikaty, kolumna `is_duplicate` ma `true` – takich wierszy zwykle nie oznaczamy `done`.

2. **Zamknij alternatywne edytory**

- Upewnij się, że Excel/Numbers nie ma otwartej starej wersji arkusza; w przeciwnym razie `scan` nie zapisze nowych danych.

3. **Otwórz `unsorted.xlsx`**

- Pierwszy wiersz to nagłówki, kolumny techniczne są ukryte.
- Włącz filtr (`A1` → Filtr) jeżeli chcesz szybciej filtrować po `target_subfolder`, `done` lub `ai_guess_bucket`.

4. **Korzystaj z dropdownów**

- `target_subfolder` pobiera wartości z `_lists` → to zawsze aktualna taksonomia; nie wpisuj nazw ręcznie.
- Kolumna `done` akceptuje tylko `TRUE/FALSE`; Excel pokazuje listę wyboru.

5. **Uzupełnij metadane**

- Kolumny `artist`, `title`, `version_info`, `genre`, `must_play`, `occasion_tags`, `notes` są edytowalne.
- Jeśli sugerowane wartości (`*_suggest`) są poprawne, możesz je skopiować: `artist_suggest → artist` itp.
- `bpm` i `key_camelot` są kopiowane z tagów lub Essentii – popraw je ręcznie, jeśli trzeba.

6. **Weryfikuj wskazówki**

- `ai_guess_bucket` i `ai_guess_comment` opisują heurystyki/reguły; traktuj je jako inspirację, nie pewnik.
- `pop_playcount`/`pop_listeners` pomagają priorytetyzować hity – możesz filtrować po tych kolumnach przed edycją.

7. **Ustaw `done = TRUE` wyłącznie, gdy**

- plik ma finalny bucket, nazwy są poprawne, a tagi nie wymagają dodatkowej edycji;
- duplikaty (`is_duplicate = true`) zostały manualnie przeanalizowane – często zostają w stanie `FALSE` do decyzji.

8. **Zapisz i zamknij arkusz przed `apply`**

- `djlib.cli apply` blokuje się, gdy `unsorted.xlsx` jest otwarty w trybie wyłącznym (np. Excel na Windows).
- Po zapisaniu warto zrobić kopię np. `unsorted-backup.xlsx` jeśli edytujesz większe partie.

9. **Uruchom `python -m djlib.cli apply`**

- Pliki z `done = TRUE` i poprawnym `target_subfolder` zostaną przeniesione do docelowych folderów, `library.csv` zostanie uzupełniony, a wiersze znikną z `unsorted.xlsx`.
- Jeśli chcesz zobaczyć plan bez przenosin, dodaj `--dry-run`.

10. **Cofnij się w razie błędu**

- `python -m djlib.cli undo` wykorzystuje log `LOGS/moves-*.csv`, aby przywrócić poprzedni stan i usunąć wpisy z `library.csv`.

### Tipy i diagnostyka

- Jeżeli dropdowny zniknęły, uruchom ponownie `scan` lub `apply` (obie komendy regenerują arkusz).
- Gdy Essentia nie policzyła BPM/Key, uruchom `python -m djlib.cli analyze-audio --recompute` lub `sync-audio-metrics --force`.
- Kolumny `genres_*` są tylko do odczytu – edytuj jedynie `genre`/`target_subfolder`.
- Filtrowanie po `done = FALSE` + `target_subfolder` pusty to szybki sposób na znalezienie rekordów wymagających decyzji.
- Jeśli Excel pokazuje komunikat o edycji tylko do odczytu, skopiuj plik w inne miejsce, edytuj i nadpisz oryginał po zamknięciu programu.

## Pliki konfiguracyjne i klucze

- **`config.yml`** (zapisywany przez wizard):
  ```yaml
  LIB_ROOT: /Volumes/Music/Library
  INBOX_UNSORTED: /Volumes/Music/INBOX_UNSORTED
  CSV_PATH: data/library.csv
  LASTFM_API_KEY: ...
  SOUNDCLOUD_CLIENT_ID: ...
  ```
  - Alternatywnie: ustaw w zmiennych środowiskowych (`LASTFM_API_KEY`, `SOUNDCLOUD_CLIENT_ID`).

## Enrichment (multi-source) i nowe kolumny CSV

- Źródła: MusicBrainz, Last.fm, SoundCloud (opcjonalnie).
- Kolumny: `genres_musicbrainz`, `genres_lastfm`, `genres_soundcloud`, `pop_playcount`, `pop_listeners`.
- Agregat: `genre_suggest` bazuje na ważonej fuzji źródeł (Last.fm 6.0, MB 3.0, SC 2.0).
- Flagi: `--force-genres` (nadpisywanie) i `--skip-soundcloud` (pominięcie SC bez pytania).
- Interaktywny prompt: przy nieważnym/missing `SOUNDCLOUD_CLIENT_ID` jeśli brak `--skip-soundcloud`.

Przykład:

```bash
python -m djlib.cli enrich-online --force-genres --skip-soundcloud
```

## Dokumentation Index

| Sekcja                     | Zawartość                                 | Plik                            |
| -------------------------- | ----------------------------------------- | ------------------------------- |
| Podstawowy opis & workflow | Główne kroki pracy, flags                 | `README.md` (root)              |
| Szczegóły funkcjonalne     | Szybki start, tasks, enrichment           | `docs/README.md`                |
| Architektura               | Moduły, wagi źródeł, parser wersji, testy | `docs/ARCHITECTURE.md`          |
| Roadmap                    | Stan realizacji, backlog, priorytety      | `docs/ROADMAP_essentia_plan.md` |
| Instalacja                 | Essentia, fpcalc, zależności              | `docs/INSTALL.md`               |
| Taksonomia                 | Definicja bucketów                        | `taxonomy.yml`                  |
| Mapowanie tagów → bucket   | Reguły konwersji tagów na targety         | `taxonomy_map.yml`              |
| Reguły auto-decide         | Proste zasady przypisań                   | `rules.yml`                     |

## CLI Cheat‑Sheet

| Komenda                                          | Cel                                          | Kluczowe opcje                         |
| ------------------------------------------------ | -------------------------------------------- | -------------------------------------- |
| `python -m djlib.cli scan`                       | Skan INBOX → `unsorted.xlsx`                 | –                                      |
| `python -m djlib.cli analyze-audio`              | Lokalne obliczenie cech (Essentia)           | `--check-env`, `--recompute`, `--path` |
| `python -m djlib.cli enrich-online`              | Wzbogacanie multi-source                     | `--force-genres`, `--skip-soundcloud`  |
| `python -m djlib.cli auto-decide`                | Uzupełnienie pustych targetów                | `--only-empty`                         |
| `python -m djlib.cli apply`                      | Export `done=TRUE` → biblioteka              | `--dry-run`                            |
| `python -m djlib.cli undo`                       | Cofnięcie ostatnich przenosin                | –                                      |
| `python -m djlib.cli dupes`                      | Raport duplikatów                            | –                                      |
| `python -m djlib.cli detect-taxonomy`            | Odtworzenie taxonomy z folderów              | –                                      |
| `python -m djlib.cli sync-audio-metrics`         | Przepisanie BPM/Key/Energy do arkusza        | `--write-tags`, `--force`              |
| `python -m djlib.cli ml-export-training-dataset` | Zbiór treningowy (Essentia + library labels) | `--out`, `--require-both-labels`       |

## Planowane rozszerzenie `enrich_status.json`

Plik w `LOGS/` będzie rozszerzony o zapisy decyzji SoundCloud:

Proponowany schemat:

```json
{
  "started_at": "2025-11-12T14:03:22Z",
  "completed_at": "2025-11-12T14:05:47Z",
  "rows_processed": 312,
  "soundcloud": {
    "client_id_status": "invalid", // ok | invalid | missing | error | rate-limit
    "decision": "skipped", // active | skipped | aborted
    "prompt_shown": true,
    "attempted_requests": 0,
    "timestamp": "2025-11-12T14:03:25Z"
  },
  "sources_counts": {
    "musicbrainz": 250,
    "lastfm": 260,
    "soundcloud": 0
  }
}
```

Zalety: audyt decyzji, łatwiejsza telemetria jakości (które źródła często odpadają). Implementacja planowana w jednym kroku z dodaniem zapisu decyzji w CLI.

---

Jeśli potrzebujesz szybkiego skrótu działań, trzymaj się sekwencji z sekcji **Szybki start (Tasks w VS Code)**: `scan` → `analyze-audio` → edycja `unsorted.xlsx` → `apply` (opcjonalnie zakończ `ml-export-training-dataset`). Automatyczne meta-komendy są obecnie wstrzymane do czasu wdrożenia nowego orkiestratora.
