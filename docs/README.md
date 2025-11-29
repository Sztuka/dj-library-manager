# DJ Library Manager

Lokalny pomocnik do **porządkowania biblioteki DJ-a**: skanuje UNSORTED, waliduje analizę Rekordbox, wzbogaca metadane z wielu źródeł, umożliwia manualną kurację w Excel i bezpiecznie przenosi zatwierdzone pliki z opcją **undo**. Integruje się z **Rekordbox** i wspiera eksport danych do treningu modeli ML.

## Features

- **Workflow Excel-based** – skan UNSORTED → `unsorted.xlsx` → manualna edycja metadanych → export zatwierdzonych
- **Struktura dysku**: Main Library (~/Music Library/{Artist}/), Reject (~/Music Rejected/), Archive (~/Music Archive/{Artist}/), Mixes (~/Music Library/MIXES/)
- **Scan → Excel** – metadane audio (rozmiar, SHA256, tagi Rekordbox: BPM/Key) + wielokrotne nawiasy w nazwie pliku konsolidowane do `version_suggest`
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
3. **WORKFLOW 0 — Sync DJ Libraries & Tags** (opcjonalnie): synchronizuje library.csv z bazami Rekordbox/Traktor.
4. **WORKFLOW 1 — Scan UNSORTED**: czyta ID z Rekordbox/Traktor, taguje pliki i aktualizuje `unsorted.xlsx`.
5. **WORKFLOW 2 — Enrich Online** (opcjonalnie): pobiera metadane z internetu (Beatport, MusicBrainz, Last.fm).
6. Edytuj `unsorted.xlsx` – uzupełnij `artist`/`title`/`genre`/`destination` (library/reject/archive/mixes), oznacz wiersze `done = TRUE`.
7. **WORKFLOW 3 — Export approved tracks** (`python -m djlib.cli apply`): przenosi `done = TRUE`, zapisuje tagi i automatycznie synchronizuje DJ software.
8. **WORKFLOW 4 — Analyze audio (Essentia)** (opcjonalnie, po exportie): liczy cechy i zapisuje je do cache (`LOGS/audio_analysis.sqlite`).
9. **WORKFLOW 5 — ML dataset export** (`python -m djlib.cli ml-export-training-dataset`): tworzy `data/training_dataset_full.csv` na podstawie cache Essentii i `library.csv`.
10. Testy: _TESTS — run_ / _TESTS — coverage_ (opcjonalnie przed commitem).

## How-to: praca z `unsorted.xlsx`

1. **Po skanie sprawdź status**

- `LOGS/scan_status.json` pokaże liczbę plików i ewentualne błędy (`missing_fpcalc`).
- Jeśli pojawiły się duplikaty, kolumna `is_duplicate` ma `true` – takich wierszy zwykle nie oznaczamy `done`.

2. **Zamknij alternatywne edytory**

- Upewnij się, że Excel/Numbers nie ma otwartej starej wersji arkusza; w przeciwnym razie `scan` nie zapisze nowych danych.

3. **Otwórz `unsorted.xlsx`**

- Pierwszy wiersz to nagłówki, kolumny techniczne są ukryte.
- Włącz filtr (`A1` → Filtr) jeżeli chcesz szybciej filtrować po `destination`, `genre`, `status` lub `done`.

4. **Korzystaj z dropdownów**

- `genre` pobiera wartości z `genres.yml` – 30 kanonicznych gatunków; nie wpisuj nazw ręcznie.
- `destination` akceptuje: `library`, `reject`, `archive`, `mixes`, lub puste.
- `status` akceptuje: `accept`, `reject`, `review`, lub puste (informacyjne, nie kontroluje przenosin).
- Kolumna `done` akceptuje tylko `TRUE/FALSE`; Excel pokazuje listę wyboru.

5. **Uzupełnij metadane**

- Kolumny `artist`, `title`, `version_info`, `year`, `genre`, `status`, `destination`, `must_play`, `occasion_tags`, `notes` są edytowalne.
- Jeśli sugerowane wartości (`*_suggest`) są poprawne, możesz je skopiować: `artist_suggest → artist` itp.
- `bpm` i `key_camelot` są kopiowane z tagów Rekordbox – popraw je ręcznie, jeśli trzeba.
- **Album**: Zawsze czyszczony podczas exportu (kompilacje nie są przydatne dla DJów).

6. **Weryfikuj sugestie**

- `genre_suggest` bazuje na fuzji Last.fm/MusicBrainz/Beatport – możesz zaakceptować lub zmienić ręcznie.
- `pop_playcount`/`pop_listeners` pomagają priorytetyzować popularne utwory – możesz filtrować po tych kolumnach przed edycją.

7. **Ustaw `done = TRUE` wyłącznie, gdy**

- plik ma wybraną destynację (`destination` = library/reject/archive/mixes), nazwy są poprawne, a metadane kompletne;
- duplikaty (`is_duplicate = true`) zostały manualnie przeanalizowane – często zostają w stanie `FALSE` do decyzji.

8. **Zapisz i zamknij arkusz przed `apply`**

- `djlib.cli apply` blokuje się, gdy `unsorted.xlsx` jest otwarty w trybie wyłącznym (np. Excel na Windows).
- Po zapisaniu warto zrobić kopię np. `unsorted-backup.xlsx` jeśli edytujesz większe partie.

9. **Uruchom `python -m djlib.cli apply`**

- Pliki z `done = TRUE` i poprawnym `destination` zostaną przeniesione do docelowych folderów, `library.csv` zostanie uzupełniony, a wiersze znikną z `unsorted.xlsx`.
- Kolumna `status` jest tylko informacyjna – za przenosiny odpowiada `destination`.
- Jeśli chcesz zobaczyć plan bez przenosin, dodaj `--dry-run`.

10. **Cofnij się w razie błędu**

- `python -m djlib.cli undo` wykorzystuje log `LOGS/moves-*.csv`, aby przywrócić poprzedni stan i usunąć wpisy z `library.csv`.

### Tipy i diagnostyka

- Jeżeli dropdowny zniknęły, uruchom ponownie `scan` lub `apply` (obie komendy regenerują arkusz).
- Gdy Essentia nie policzyła BPM/Key, uruchom `python -m djlib.cli analyze-audio --recompute` lub `sync-audio-metrics --force`.
- Kolumny `genres_*` i `*_suggest` są tylko do odczytu – edytuj jedynie `genre`/`destination`.
- Filtrowanie po `done = FALSE` + `destination` pusty to szybki sposób na znalezienie rekordów wymagających decyzji.
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
