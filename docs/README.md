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
2. **STEP 1 — Start Web Wizard (server)** → **STEP 1.1 — Open Wizard URL**  
   Przejdź w przeglądarce:
   - **Krok 1 (Lokalizacja):** wskaż `LIB ROOT` (gdzie powstanie struktura) i `INBOX_UNSORTED`.
   - **Krok 2 (Taksonomia):** zdefiniuj subkubełki:
     - `CLUB`: np. HOUSE, AFRO HOUSE, TECHNO, DNB, ELECTRO SWING …
     - `OPEN FORMAT`: np. PARTY DANCE, FUNK SOUL, HIP-HOP, RNB, 70s/80s/90s/2000s/2010s, POLISH SINGALONG …
     - `MIXES` jest zawsze top-level.
   - **Krok 3 (Foldery & Skan):** utworzymy foldery; opcjonalnie od razu skan INBOX.
3. **STEP 2 — Scan INBOX_UNSORTED** _(jeśli nie skanowałeś w kroku 3; runda `round-1` i tak startuje od `scan`, więc krok może być pominięty)_
4. **ROUND — 1) Analyze+Enrich+Predict+Export** – pełna runda (autorun: scan → analyze → enrich → predict → export XLSX).

- Domyślnie startuje od `scan`, aby odświeżyć `library.csv` (użyj `--skip-scan`, gdy masz świeży stan).
- Eksport XLSX zostanie pominięty z komunikatem, jeśli po analizie brak wierszy do pokazania (koniec pustych arkuszy).

5. **ROUND — 2) Import+Apply+Train+QA** – wczytanie zmian z XLSX, apply, trening lokalnego modelu, kontrola jakości.
6. **STEP 3 — Auto-decide (rules.yml) — only empty** _(alternatywa / manualny krok)_
7. **STEP 4 — Apply decisions (dry-run)** – pokazuje co zostanie przeniesione.
8. **STEP 5 — Apply decisions** – wykonuje przenosiny.  
   Dodatkowo: **TOOLS — Undo last moves**, **TOOLS — Report duplicates**, **TOOLS — Check audio env**, **TOOLS — Detect taxonomy**.

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

| Komenda                                  | Cel                                                | Kluczowe opcje                                 |
| ---------------------------------------- | -------------------------------------------------- | ---------------------------------------------- |
| `python -m djlib.cli scan`               | Skan INBOX → CSV                                   | `--force` (jeśli dodamy), brak aktualnie       |
| `python -m djlib.cli enrich-online`      | Wzbogacanie multi-source                           | `--force-genres`, `--skip-soundcloud`          |
| `python -m djlib.cli auto-decide`        | Uzupełnienie pustych targetów                      | `--only-empty`                                 |
| `python -m djlib.cli apply`              | Przeniesienie plików                               | `--dry-run`                                    |
| `python -m djlib.cli undo`               | Cofnięcie ostatnich przenosin                      | –                                              |
| `python -m djlib.cli dupes`              | Raport duplikatów                                  | –                                              |
| `python -m djlib.cli detect-taxonomy`    | Odtworzenie taxonomy z folderów                    | –                                              |
| `python -m djlib.cli sync-audio-metrics` | Lokalne BPM/Key/Energy                             | `--write-tags`, `--force`                      |
| `python -m djlib.cli round-1`            | Złożona runda (scan+analyze+enrich+predict+export) | `--skip-scan` jeśli biblioteka już zeskanowana |
| `python -m djlib.cli round-2`            | Import zmian + apply + train + QA                  | (wewn. sekwencja)                              |

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

Jeśli potrzebujesz szybkiego skrótu działań: użyj `round-1` (domyślnie rozpoczyna od `scan`, aby odświeżyć `library.csv`), przejrzyj XLSX i popraw `target_subfolder`, potem `round-2`.
