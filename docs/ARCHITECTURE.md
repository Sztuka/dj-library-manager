# DJ Library Manager - Dokumentacja Architektury

**Wersja:** 2.1  
**Data:** 2025  
**Przeznaczenie:** Dokumentacja techniczna dla agentów AI oraz deweloperów

---

## Spis Treści

1. [Przegląd Systemu](#przegląd-systemu)
2. [Struktura Plików i Folderów](#struktura-plików-i-folderów)
3. [Taksonomia i Naming Conventions](#taksonomia-i-naming-conventions)
4. [Struktura Danych CSV](#struktura-danych-csv)
5. [Format Nazewnictwa Plików](#format-nazewnictwa-plików)
6. [Moduły i Komponenty](#moduły-i-komponenty)
7. [Workflow i Procesy](#workflow-i-procesy)
8. [Rozwiązania Techniczne](#rozwiązania-techniczne)
9. [Standardy Kodowania](#standardy-kodowania)

---

## Przegląd Systemu

DJ Library Manager to narzędzie do zarządzania biblioteką muzyczną DJ-a. System skanuje nowe pliki audio, ekstrahuje metadane, wzbogaca je danymi online (MusicBrainz, Last.fm, opcjonalnie SoundCloud), klasyfikuje utwory według gatunków i organizuje je w strukturze folderów.

### Główne funkcjonalności:

- Skanowanie INBOX i ekstrakcja tagów audio
- **Online enrichment**: Wzbogacanie metadanych z MusicBrainz, AcoustID, Last.fm, SoundCloud (opcjonalnie)
- **Local audio analysis**: Ekstrakcja BPM/Key/Energy z Essentia (bez Traktora)
- **Tag writing**: Zapis metryk do ID3 tagów plików (Camelot notation)
- **Genre resolution**: Rozpoznawanie gatunków z wielu źródeł (MusicBrainz / Last.fm / SoundCloud) z wagami źródeł + per-source kolumny w CSV
- Automatyczna klasyfikacja utworów (AI guessing + taxonomy mapping)
- Zarządzanie taksonomią kategorii (buckets)
- Automatyczne decyzje na podstawie reguł lub heurystyk
- Przenoszenie i zmiana nazw plików
- Wykrywanie duplikatów (hash + fingerprint audio)
- **System propozycji**: Suggest/accept workflow dla metadanych

---

## Struktura Plików i Folderów

### Struktura projektu:

```
dj-library-manager/
├── djlib/              # Główny moduł aplikacji
│   ├── config.py       # Konfiguracja ścieżek i ustawień
│   ├── taxonomy.py     # Zarządzanie taksonomią bucketów
│   ├── csvdb.py        # Operacje na bazie CSV
│   ├── tags.py         # Czytanie/zapis tagów audio
│   ├── fingerprint.py  # Fingerprint audio i hash
│   ├── filename.py     # Generowanie nazw plików
│   ├── mover.py        # Przenoszenie plików
│   ├── classify.py     # AI guessing bucketów (legacy)
│   ├── placement.py    # Automatyczne decyzje o bucketach
│   ├── enrich.py       # Online enrichment metadanych
│   ├── genre.py        # Genre resolution i taxonomy mapping
│   ├── extern.py       # Integracje zewnętrzne (Last.fm)
│   ├── buckets.py      # Walidacja bucketów
│   ├── audio/          # Lokalna analiza audio
│   │   ├── __init__.py
│   │   ├── cache.py    # Cache metryk audio (SQLite)
│   │   ├── features.py # Ekstrakcja cech audio
│   │   └── essentia_backend.py # Backend Essentia dla analizy
│   └── metadata/       # Klienci API metadanych
│       ├── __init__.py
│       ├── genre_resolver.py   # Główny resolver gatunków (wagi źródeł)
│       ├── mb_client.py        # MusicBrainz client
│       ├── lastfm.py           # Last.fm client
│       ├── soundcloud.py       # SoundCloud: tag_list + health check
├── scripts/            # Skrypty CLI
├── webui/              # Interfejs webowy (TODO)
├── docs/               # Dokumentacja
├── taxonomy.yml        # Definicja bucketów
├── taxonomy_map.yml    # Mapowanie tagów → bucketów
├── rules.yml           # Reguły auto-decide
└── config.local.yml    # Lokalna konfiguracja (gitignored)
```

### Struktura biblioteki (LIB_ROOT):

```
~/Music_DJ/
├── INBOX_UNSORTED/           # Folder skanowany przez scan_inbox
├── READY TO PLAY/            # Gotowe do użycia utwory
│   ├── CLUB/
│   │   ├── HOUSE
│   │   ├── TECH HOUSE
│   │   ├── TECHNO
│   │   └── ...
│   └── OPEN FORMAT/
│       ├── RNB
│       ├── HIP-HOP
│       └── ...
├── REVIEW QUEUE/             # Do przeglądu
│   ├── UNDECIDED
│   └── NEEDS EDIT
├── LOGS/                     # Logi operacji
│   ├── enrich_status.json    # Status wzbogacania (plan: dodać decyzję usera nt. SoundCloud)
│   ├── fingerprint_status.json
│   ├── moves-{timestamp}.csv # Logi przeniesień
│   └── dupes.csv             # Raport duplikatów
└── library.csv               # Główna baza danych
```

---

## Taksonomia i Naming Conventions

### Standardy nazewnictwa

**WAŻNE:** Wszystkie nazwy bucketów używają SPACJI (nie podkreślników).

### Format target_subfolder:

```
READY TO PLAY/{SECTION}/{BUCKET}
REVIEW QUEUE/{BUCKET}
```

Gdzie `{SECTION}` to: `CLUB` lub `OPEN FORMAT`

### Lista dostępnych bucketów (z taxonomy.yml):

#### READY TO PLAY / CLUB:

- `CLUB/AFRO HOUSE`
- `CLUB/DEEP HOUSE`
- `CLUB/ELECTRO`
- `CLUB/ELECTRO SWING`
- `CLUB/HOUSE`
- `CLUB/MELODIC TECHNO`
- `CLUB/TECH HOUSE`
- `CLUB/TECHNO`
- `CLUB/TRANCE`
- `CLUB/DNB`
- `MIXES` (bucket top-level)

#### READY TO PLAY / OPEN FORMAT:

- `OPEN FORMAT/2000s`
- `OPEN FORMAT/2010s`
- `OPEN FORMAT/70s`
- `OPEN FORMAT/80s`
- `OPEN FORMAT/90s`
- `OPEN FORMAT/FUNK SOUL`
- `OPEN FORMAT/HIP-HOP`
- `OPEN FORMAT/LATIN REGGAETON`
- `OPEN FORMAT/PARTY DANCE`
- `OPEN FORMAT/POLISH SINGALONG`
- `OPEN FORMAT/RNB`
- `OPEN FORMAT/ROCK CLASSICS`
- `OPEN FORMAT/ROCKNROLL`

#### REVIEW QUEUE:

- `UNDECIDED`
- `NEEDS EDIT`

### Ważne konwencje:

1. **Spacje zamiast podkreślników**: `TECH HOUSE` nie `TECH_HOUSE`
2. **Wielkie litery**: Wszystkie nazwy w UPPERCASE
3. **Kolejność słów**: `MELODIC TECHNO` nie `TECHNO MELODIC`, `AFRO HOUSE` nie `AFROHOUSE`
4. **Separator sekcji**: `/` między sekcją a bucketem
5. **Prefix w target_subfolder**: Zawsze pełna ścieżka, np. `READY TO PLAY/CLUB/HOUSE`

---

## Struktura Danych CSV

### Plik: `library.csv`

Baza danych główna w formacie CSV. Kolumny zdefiniowane w `djlib/csvdb.py::FIELDNAMES`:

| Kolumna                                    | Opis                                    | Przykład                              |
| ------------------------------------------ | --------------------------------------- | ------------------------------------- |
| `track_id`                                 | Unikalne ID utworu                      | `a1b2c3d4e5f6_1234567890`             |
| `file_path`                                | Oryginalna ścieżka do pliku             | `/Users/user/Music/INBOX/track.mp3`   |
| `artist`                                   | Artysta (zaakceptowany)                 | `Daft Punk`                           |
| `title`                                    | Tytuł utworu (zaakceptowany)            | `Get Lucky`                           |
| `version_info`                             | Wersja/remix (zaakceptowany)            | `Radio Edit`, `Extended Mix`          |
| `genre`                                    | Gatunek z tagów audio                   | `Electronic`                          |
| `bpm`                                      | BPM (z Traktor/analizy)                 | `120`                                 |
| `key_camelot`                              | Tonacja Camelot (z Traktor)             | `6A`                                  |
| `energy_hint`                              | Wskazówka energii (opcjonalnie)         | `high`, `medium`                      |
| `file_hash`                                | SHA-256 hash pliku                      | `a1b2c3...`                           |
| `fingerprint`                              | Audio fingerprint (Chromaprint)         | `AQAA...`                             |
| `is_duplicate`                             | Czy duplikat (na podstawie fingerprint) | `true`, `false`                       |
| `ai_guess_bucket`                          | Sugerowany bucket przez AI              | `READY TO PLAY/CLUB/HOUSE`            |
| `ai_guess_comment`                         | Komentarz do sugestii                   | `genre=house; conf=0.95`              |
| `target_subfolder`                         | Finalna decyzja użytkownika             | `READY TO PLAY/CLUB/HOUSE`            |
| `must_play`                                | Flaga "must play" (opcjonalnie)         | `true`, `false`                       |
| `occasion_tags`                            | Tagi okazji (opcjonalnie)               | `wedding`, `party`                    |
| `notes`                                    | Uwagi użytkownika                       | Dowolny tekst                         |
| `final_filename`                           | Finalna nazwa po przeniesieniu          | `Artist - Title (Mix) [6A 120].mp3`   |
| `final_path`                               | Finalna ścieżka po przeniesieniu        | `/Users/user/Music/READY TO PLAY/...` |
| `added_date`                               | Data dodania do biblioteki              | `2024-01-15 10:30:00`                 |
| **Propozycje metadanych (do akceptacji):** |                                         |                                       |
| `artist_suggest`                           | Proponowany artysta                     | `Daft Punk`                           |
| `title_suggest`                            | Proponowany tytuł                       | `Get Lucky`                           |
| `version_suggest`                          | Proponowana wersja                      | `Radio Edit`                          |
| `genre_suggest`                            | Proponowany główny gatunek (agregowany) | `House, Electronic, Dance`            |
| `genres_musicbrainz`                       | Surowe gatunki z MusicBrainz            | `house; electronic`                   |
| `genres_lastfm`                            | Surowe tagi z Last.fm                   | `house; french; disco`                |
| `genres_soundcloud`                        | Tag list z SoundCloud (opcjonalnie)     | `house; afro; remix`                  |
| `pop_playcount`                            | Last.fm playcount (jeśli dostępny)      | `123456`                              |
| `pop_listeners`                            | Last.fm listeners (jeśli dostępny)      | `34567`                               |
| `album_suggest`                            | Proponowany album                       | `Random Access Memories`              |
| `year_suggest`                             | Proponowany rok wydania                 | `2013`                                |
| `duration_suggest`                         | Proponowany czas trwania                | `4:45`                                |
| `meta_source`                              | Źródło metadanych                       | `musicbrainz`, `acoustid+musicbrainz` |
| `review_status`                            | Status przeglądu                        | `pending`, `accepted`                 |

### Ważne pola:

**`target_subfolder`**:

- Puste = nie zdecydowano
- `REJECT` = odrzucony
- `READY TO PLAY/...` lub `REVIEW QUEUE/...` = gotowe do przeniesienia

**`is_duplicate`**:

- Sprawdzane przez porównanie `fingerprint` (Chromaprint)
- Jeśli brak fingerprint, porównywany jest `file_hash`

---

## Format Nazewnictwa Plików

### Format finalnej nazwy pliku:

```
{Artist} - {Title} ({VersionInfo}) [{Key} {BPM}]{ext}
```

### Przykłady:

```
Daft Punk - Get Lucky (Radio Edit) [6A 120].mp3
The Prodigy - Firestarter (Original Mix) [8B 145].flac
Unknown Artist - Unknown Title (Original Mix) [?? ??].mp3
```

### Reguły (z `djlib/filename.py`):

1. **VersionInfo**: Jeśli puste, ustawiane na `"Original Mix"`
2. **Key**: Jeśli puste, ustawiane na `"??"`
3. **BPM**: Jeśli puste, ustawiane na `"??"`
4. **Artist**: Jeśli puste, ustawiane na `"Unknown Artist"`
5. **Title**: Jeśli puste, ustawiane na `"Unknown Title"`
6. **Nielegalne znaki**: `/\:*?"<>|` są zamieniane na `-`

### Konflikt nazw:

Jeśli plik o takiej samej nazwie już istnieje, dodawany jest numer:

```
Artist - Title (Mix) [6A 120].mp3
Artist - Title (Mix) [6A 120] (2).mp3
Artist - Title (Mix) [6A 120] (3).mp3
```

---

## Moduły i Komponenty

### `djlib/config.py`

**Zadanie**: Zarządzanie konfiguracją ścieżek

**Kluczowe zmienne**:

- `LIB_ROOT`: Główny katalog biblioteki (domyślnie `~/Music_DJ`)
- `INBOX_DIR`: Folder do skanowania (domyślnie `~/Music_DJ/INBOX_UNSORTED`)
- `READY_TO_PLAY_DIR`: `LIB_ROOT / "READY TO PLAY"`
- `REVIEW_QUEUE_DIR`: `LIB_ROOT / "REVIEW QUEUE"`
- `LOGS_DIR`: `LIB_ROOT / "LOGS"`
- `CSV_PATH`: `LIB_ROOT / "library.csv"`

**Konfiguracja**:

- Lokalizacja pliku: `config.local.yml` (preferowany) lub `~/.djlib_manager/config.yml`
- Format: YAML z kluczami `library_root` i `inbox_dir`

### `djlib/taxonomy.py`

**Zadanie**: Zarządzanie taksonomią bucketów

**Funkcje kluczowe**:

- `allowed_targets()`: Zwraca listę wszystkich dozwolonych targetów
- `is_valid_target(value)`: Sprawdza czy target jest poprawny
- `target_to_path(target)`: Konwertuje target na ścieżkę Path
- `ensure_taxonomy_dirs()`: Tworzy wszystkie katalogi z taxonomy
- `add_ready_bucket(rel_key)`: Dodaje nowy bucket do READY

**Plik konfiguracyjny**: `taxonomy.yml`

```yaml
ready_buckets:
  - CLUB/HOUSE
  - CLUB/TECH HOUSE
  - ...
review_buckets:
  - UNDECIDED
  - NEEDS EDIT
```

### `djlib/csvdb.py`

**Zadanie**: Operacje na bazie CSV

**Funkcje**:

- `load_records(csv_path)`: Ładuje wszystkie rekordy z CSV
- `save_records(csv_path, rows)`: Zapisuje rekordy do CSV i filtruje wiersze wyłącznie do `FIELDNAMES`, aby legacy kolumny (np. `genres_spotify`) nie wracały do pliku
- `FIELDNAMES`: Lista kolumn CSV

### `djlib/tags.py`

**Zadanie**: Czytanie/zapis tagów audio z plików

**Metoda**: Używa `mutagen` do czytania/zapisu tagów ID3/MP3

**Czytanie**: Zwraca dict z kluczami: `artist`, `title`, `version_info`, `bpm`, `key_camelot`, `genre`, `comment`, `energy_hint`

**Zapis**: Funkcja `write_tags()` zapisuje metryki audio do tagów ID3 (BPM, Key jako TKEY, Energy jako komentarz)

**Uwaga**: BPM i Key mogą być ustawione przez Traktor Pro lub lokalną analizę Essentia

### `djlib/audio/`

**Zadanie**: Lokalna analiza audio bez Traktora

#### `cache.py`

- **Zadanie**: Cache metryk audio w SQLite
- **Funkcje**: `init_db()`, `store_metrics()`, `get_metrics()`
- **Cache location**: `LOGS_DIR / "audio_analysis.sqlite"`
- **Schema**: audio_id, bpm, key_camelot, energy, source, timestamp

#### `features.py`

- **Zadanie**: Ekstrakcja cech audio
- **Funkcje**: `extract_bpm()`, `extract_key()`, `extract_energy()`
- **Backend**: Używa Essentia Python bindings

#### `essentia_backend.py`

- **Zadanie**: Główny backend analizy audio z Essentia
- **Funkcje**: `analyze_file()`, `batch_analyze()`
- **Features**: BPM, Key (Camelot), Energy, Onset rate, Spectral features
- **Output**: Metryki zapisane w cache i opcjonalnie w tagach plików

### `djlib/fingerprint.py`

**Zadanie**: Fingerprint audio i hash plików

**Funkcje**:

- `file_sha256(path)`: SHA-256 hash całego pliku
- `audio_fingerprint(path)`: Chromaprint fingerprint (wymaga `fpcalc`)

**Wykrywanie duplikatów**:

1. Najpierw porównywany `fingerprint` (jeśli dostępny)
2. Fallback na `file_hash` jeśli brak fingerprint

### `djlib/filename.py`

**Zadanie**: Generowanie finalnych nazw plików

**Funkcja główna**:

- `build_final_filename(artist, title, version_info, key_camelot, bpm, ext)`: Generuje nazwę zgodnie z konwencją

#### Parsowanie wielokrotnych nawiasów (multi-parentheses parsing)

Przykład surowej nazwy:

```
Artist - Title (Karibu Remix) (Extended Edit).mp3
```

Algorytm:

1. Wykryj wszystkie segmenty w nawiasach na końcu bazowej nazwy.
2. Oczyść z nadmiarowych spacji i znaków.
3. Dedup (z zachowaniem kolejności).
4. Połącz przecinkami → `version_suggest = "Karibu Remix, Extended Edit"`.
5. Brak segmentów → fallback `Original Mix`.

Korzyść: zachowanie kompletu wariantów (Remix / Edit / Radio / Extended) dla przyszłych heurystyk (np. afro house jeśli token zawiera specyficzny wzorzec).

### `djlib/mover.py`

**Zadanie**: Przenoszenie i zmiana nazw plików

**Funkcje**:

- `resolve_target_path(target)`: Konwertuje target_subfolder na Path
- `move_with_rename(src, dest_dir, final_name)`: Przenosi i zmienia nazwę (obsługuje konflikty)
- `utc_now_str()`: Format daty/czasu UTC dla `added_date`

### `djlib/enrich.py`

**Zadanie**: Wzbogacanie metadanych online

**Funkcje kluczowe**:

- `suggest_metadata(path, tags)`: Generuje propozycje metadanych z nazwy pliku
- `lookup_musicbrainz(artist, title)`: Wyszukiwanie w MusicBrainz API
- `lookup_acoustid(fp, duration)`: Wyszukiwanie przez AcoustID fingerprint
- `enrich_online_for_row(path, row)`: Główna funkcja wzbogacania dla rekordu

**Źródła metadanych**:

1. **AcoustID + MusicBrainz**: Najwyższy priorytet (jeśli dostępny fingerprint)
2. **MusicBrainz search**: Bezpośrednie wyszukiwanie
3. **Fallback**: Parsowanie nazwy pliku + tagi audio

### `djlib/genre.py`

**Zadanie**: Rozpoznawanie gatunków i mapowanie na buckety

**Funkcje kluczowe**:

- `load_taxonomy_map()`: Ładuje mapowanie tagów → bucketów z `taxonomy_map.yml`
- `external_genre_votes(artist, title)`: Zbiera głosy gatunków z Last.fm
- `suggest_bucket_from_votes(votes, mapping)`: Sugeruje bucket na podstawie głosów

**Plik konfiguracyjny**: `taxonomy_map.yml`

```yaml
map:
  house: "CLUB/HOUSE"
  techno: "CLUB/TECHNO"
  hip-hop: "OPEN FORMAT/HIP-HOP"
  # ...
```

### `djlib/metadata/`

**Zadanie**: Klienci API dla zewnętrznych źródeł metadanych

-#### `genre_resolver.py`

- `resolve(artist, title, duration_s, version=None, disable_soundcloud=False)`: Główny resolver gatunków
- Łączy dane z MusicBrainz, Last.fm oraz opcjonalnie SoundCloud (SoundCloud korzysta z `version`/remix tokens przekazanych z CLI, by wyszukiwać właściwe warianty)
- Wagi (domyślne): Last.fm 6.0, MusicBrainz 3.0, SoundCloud 2.0
- Zwraca agregat + per-source listy (`genres_*`) i confidence

#### `mb_client.py`

- `search_recording(artist, title)`: Wyszukiwanie utworów w MusicBrainz
- `get_recording_genres(recording_id, ...)`: Pobieranie gatunków z MB
- Obsługa rate limiting (1 req/s) i retry

#### `lastfm.py`

- `get_top_tags(artist, title)`: Pobieranie top tagów z Last.fm

#### `soundcloud.py`

- `track_tags(artist, title, version=None)`: Próbuje pobrać `genre` i `tag_list` z SoundCloud API, korzystając z `version`/remix tokens (np. `Extended Mix`, `Karibu Remix`) do budowy zapytań.
- `_focus_version_tokens()` i `_candidate_queries()` filtrują wersję, aby preferować właściwe remiksy i rozszerzenia (np. Extended Edit vs Radio Edit), co zwiększa trafność wyników.
- Obsługuje cache HTTP i health check `SOUNDCLOUD_CLIENT_ID` (brak/invalid/rate limit) zanim enrichment wystartuje.

### `djlib/extern.py`

**Zadanie**: Integracje zewnętrzne (Last.fm)

**Funkcje**:

- `lastfm_toptags(artist, title)`: Tagi z Last.fm

**Konfiguracja API**:

- Last.fm: `lastfm_api_key` w config

### `djlib/placement.py`

**Zadanie**: Automatyczne decyzje o bucketach na podstawie metadanych

**Funkcja główna**:

- `decide_bucket(row)`: Zwraca `(target_bucket, confidence, reason)`

**Logika**:

1. **CLUB genres**: House, Tech House, Techno, DNB, Trance, Afro House, Electro Swing
2. **Era-based**: 70s, 80s, 90s, 2000s, 2010s → `OPEN FORMAT/{era}`
3. **Vibe-based**: Hip-hop → `OPEN FORMAT/HIP-HOP`, RNB → `OPEN FORMAT/RNB`, etc.
4. **BPM-based**: Jeśli BPM >= 122 i genre klubowy → CLUB
5. **Remix tokens**: Jeśli w tytule/version_info są słowa "remix", "edit", "club" → CLUB

**Confidence**: 0.0-1.0 (0.95 = bardzo pewny, 0.6 = słaby)

### `djlib/cli.py`

**Zadanie**: Interfejs CLI główny

**Komendy**:

- `scan`: Skanowanie INBOX
- `sync-audio-metrics`: Lokalna analiza BPM/Key/Energy z Essentia
  - `--write-tags`: Zapisuje metryki do tagów ID3 plików
  - `--force`: Wymusza re-analizę wszystkich plików
- `enrich-online`: Wzbogacanie metadanych online (MB, AcoustID, Last.fm, SoundCloud)
  - `--force-genres` – wymusza nadpisanie kolumn `genres_*` i `genre_suggest`
  - `--skip-soundcloud` – pomija SoundCloud bez pytania
  - Interaktywny prompt przy nieważnym/missing `SOUNDCLOUD_CLIENT_ID`
- `fix-fingerprints`: Uzupełnianie brakujących fingerprintów
- `auto-decide`: Automatyczne uzupełnianie target_subfolder
- `auto-decide-smart`: Inteligentne auto-decide z confidence thresholds
- `apply`: Przenoszenie plików zgodnie z target_subfolder
- `undo`: Cofanie ostatniej operacji przenoszenia
- `dupes`: Raport duplikatów
- `genres resolve`: Rozpoznawanie gatunków dla pojedynczego utworu (obsługuje `--version` do przekazania info o remixach/edycjach)
- `detect-taxonomy`: Wykrywanie taksonomii z istniejącej struktury folderów
- _(wstrzymane)_ meta-komendy `round-1`/`round-2`: orchestration wróci w nowym workflow, aktualnie wyłączone by wymusić ręczną kontrolę nad `unsorted.xlsx`.

---

## Workflow i Procesy

### 0. Lokalna analiza audio (opcjonalne)

**Skrypt**: `djlib/cli.py::cmd_sync_audio_metrics`

**Proces**:

1. Skanuje wszystkie pliki audio w `INBOX_DIR`
2. Dla każdego pliku:
   - Ekstrahuje BPM, Key, Energy używając Essentia
   - Zapisuje metryki w cache SQLite (`LOGS/audio_analysis.sqlite`)
   - Opcjonalnie zapisuje metryki w tagach ID3 plików (`--write-tags`)
3. Key konwertowany na Camelot notation (1A-12A, 1B-12B)
4. BPM/Key zapisane jako ID3 TKEY tag dla kompatybilności z DJ software

**Alternatywa dla Traktora**: Zamiast analizy w Traktorze, można użyć tej komendy do lokalnej ekstrakcji metryk.

### 1. Skanowanie nowych plików

**Skrypt**: `scripts/scan_inbox.py` lub `djlib/cli.py::cmd_scan`

**Proces**:

1. Skanuje `INBOX_DIR` rekurencyjnie
2. Dla każdego pliku audio:
   - Sprawdza czy już istnieje (po `file_hash`)
   - Czyta tagi audio (`mutagen`)
   - Generuje fingerprint (jeśli `fpcalc` dostępny)
   - Sprawdza duplikaty (po fingerprint lub hash)
   - AI guessing bucketu
   - **Generuje propozycje metadanych** (`suggest_*` pola)
   - Tworzy rekord w CSV z `review_status = "pending"`

**Rezultat**: Nowe wiersze w `library.csv` z pustym `target_subfolder` i propozycjami do przeglądu

### 2. Wzbogacanie metadanych online

**Skrypt**: `djlib/cli.py::cmd_enrich_online`

**Proces**:

1. Dla rekordów z `review_status != "accepted"`:

- **AcoustID lookup**: Jeśli fingerprint dostępny → MusicBrainz recording
- **MusicBrainz search**: Bezpośrednie wyszukiwanie artist/title
- **SoundCloud probe** (opcjonalnie, remix-aware): próba pobrania `genre` + `tag_list` (jeśli dostępny `SOUNDCLOUD_CLIENT_ID`), z użyciem `version`/remix info do budowania zapytań

- **Genre resolution**: Agreguje MB / Last.fm / SoundCloud (SoundCloud może być wyłączony); `resolve()` przyjmuje `version`, aby trafiać w konkretne remiksy
- **Popularity hints**: Last.fm playcount / listeners → kolumny `pop_playcount`, `pop_listeners`
- **Bucket suggestion**: Mapuje gatunki na buckety przez `taxonomy_map.yml`
- Aktualizuje `suggest_*` pola jeśli lepsze od istniejących

**Priorytety nadpisywania**:

- AcoustID + MB (fingerprint) wygrywa zawsze (najwyższa jakość identyfikacji)
- Override jeśli nowe dane mają wyższy aggregated confidence (waga źródła \* score)
- `--force-genres` wymusza nadpisanie `genres_*` oraz `genre_suggest` nawet przy równym confidence
- Preserve accepted (użytkownik) chyba że jawnie wymuszono

### 3. Automatyczne decyzje

**Opcje**:

- **Rules-based** (`rules.yml`): Na podstawie zawartości słów-kluczy
- **Placement-based** (`placement.py`): Na podstawie metadanych (genre, BPM, era)
- **Smart auto-decide**: Używa heurystyk z confidence thresholds

**Proces (smart)**:

1. Używa `djlib/placement.py::decide_bucket()`
2. Jeśli confidence >= 0.85 → ustawia `target_subfolder` automatycznie
3. Jeśli confidence >= 0.65 → tylko sugeruje w `ai_guess_bucket`

### 4. Przenoszenie plików

**Skrypt**: `scripts/apply_decisions.py` lub `djlib/cli.py::cmd_apply`

**Proces**:

1. Dla każdego wiersza z `target_subfolder` i bez `final_path`:
   - **Używa suggest\_\* pól** dla nazwy pliku (jeśli zaakceptowane)
   - Resolvuje ścieżkę docelową z `target_subfolder`
   - Generuje finalną nazwę pliku
   - Przenosi i zmienia nazwę
   - Aktualizuje `final_filename`, `final_path`, `added_date`

**Uwaga**: Użyj `--dry-run` aby zobaczyć co zostanie zrobione bez wykonania

### 4. Undo (Cofanie)

**Skrypt**: `scripts/undo_last.py` lub `djlib/cli.py::cmd_undo`

**Proces**:

1. Znajduje najnowszy plik logu w `LOGS/moves-*.csv`
2. Dla każdej operacji w logu:
   - Przenosi plik z powrotem
   - Czyści `final_filename`, `final_path` w CSV

### 5. Manualny flow z `unsorted.xlsx`

Obecny MVP wymaga sekwencyjnego przejścia przez arkusz stagingowy zamiast automatycznych „rund”. Zalecany przebieg:

1. **`scan`** – gromadzi nowe pliki w `unsorted.xlsx`, dopełnia fingerprint, tagi i sugestie AI.
2. **`analyze-audio` / `sync-audio-metrics`** – uzupełnia BPM/Key/Energy i zapisuje je w cache/tagach.
3. **(Opcjonalnie) `enrich-online` oraz `auto-decide(-smart)`** – poprawia `suggest_*` kolumny i/lub wypełnia `target_subfolder` tam, gdzie reguły są pewne.
4. **Manualna edycja `unsorted.xlsx`** – użytkownik uzupełnia finalne metadane, wybiera bucket z dropdownu i ustawia `done = TRUE` tylko dla zaakceptowanych rekordów.
5. **`apply`** – eksportuje jedynie wiersze oznaczone `done = TRUE`, przenosi pliki i czyści staging.
6. **`ml-export-training-dataset`** – łączy przyjęte rekordy z cechami Essentii dla trenowania nowych modeli.

Meta-komendy `round-1`/`round-2` zostały odłączone, aby dopracować powyższy flow i uniknąć automatycznego „przeklikiwania” niezweryfikowanych rekordów. Powrócą jako orkiestrator budujący na tym samym zestawie kroków.

---

## Rozwiązania Techniczne

### Lokalna analiza audio

- **Framework**: Essentia v2.1-beta6-dev Python bindings
- **Features**: BPM, Key (Camelot notation), Energy, Onset rate, Spectral centroid/rolloff
- **Cache**: SQLite database (`LOGS/audio_analysis.sqlite`) dla uniknięcia re-analizy
- **Tag writing**: ID3 TKEY tag dla kluczy, standardowe tagi dla BPM/Energy
- **Performance**: Batch processing, progress tracking, error handling
- **Alternatywa dla Traktora**: Kompletna ekstrakcja metryk bez DJ software

### Obsługa duplikatów

- **Primarna metoda**: Chromaprint fingerprint (porównywanie audio nawet przy różnych formatach/bitrate)
- **Fallback**: SHA-256 hash (tylko identyczne pliki)
- **Implementacja**: `djlib/fingerprint.py`

### System propozycji metadanych

- **Suggest/Accept workflow**: Metadane dzielone na zaakceptowane (główne pola) i proponowane (`suggest_*`)
- **Źródła**: Filename parsing, tagi audio, MusicBrainz, AcoustID, Last.fm, SoundCloud (opcjonalnie)
- **Priorytety**: AcoustID > MusicBrainz > filename/tags
- **Status**: `review_status` = "pending" | "accepted"

### Wzbogacanie online

- **MusicBrainz**: Recording search, genre/tags z recording/release-group/artist
- **AcoustID**: Fingerprint-based lookup (wymaga API key)
- **Last.fm**: Top tags dla utworów
- **Rate limiting**: 1 req/s dla MB, caching z `requests-cache`

### Genre Resolution

- **Multi-source aggregation**: Łączy dane z 3 źródeł (MB / Last.fm / SoundCloud) z wagami: Last.fm 6.0, MB 3.0, SoundCloud 2.0
- **Format wyjściowy**: "Main Genre, Sub1, Sub2" (max 3)
- **Confidence threshold**: Bazowy próg dla dodania tagu: >= 0.03; override istniejącego: >= 0.08 (parametry można stroić przy zwiększeniu liczby źródeł)
- **Taxonomy mapping**: Tagi → buckety przez `taxonomy_map.yml`

### Rozwiązywanie konfliktów nazw

- Jeśli plik o takiej samej nazwie już istnieje: dodawany numer `(2)`, `(3)`, etc.
- **Implementacja**: `djlib/mover.py::move_with_rename()`

### Walidacja target_subfolder

- Sprawdzanie czy target istnieje w taxonomy przed przenoszeniem
- **Implementacja**: `djlib/taxonomy.py::is_valid_target()`

---

## Standardy Kodowania

### Format kodu:

- Python 3.10+
- Type hints (z `from __future__ import annotations`)
- `black` formatter (opcjonalnie)
- `ruff` linter (opcjonalnie)

### Konwencje nazewnictwa:

- **Funkcje**: snake_case (`load_records`, `target_to_path`)
- **Klasy**: PascalCase (`AppConfig`)
- **Zmienne**: snake_case (`csv_path`, `dest_dir`)
- **Stałe**: UPPER_SNAKE_CASE (`FIELDNAMES`, `LIB_ROOT`)

### Struktura importów:

```python
from __future__ import annotations

# Standard library
from pathlib import Path
from typing import List, Dict

# Third-party
import yaml
import requests

# Local imports
from djlib.config import LIB_ROOT
from djlib.taxonomy import allowed_targets
```

### Obsługa błędów:

- Sprawdzanie istnienia plików przed operacjami
- Graceful degradation (np. brak fingerprint → użyj hash)
- Logowanie błędów do stdout (nie exceptions nie są ciche)

### Ścieżki:

- Zawsze używaj `pathlib.Path` zamiast strings
- Rozwiązuj ścieżki względem `LIB_ROOT` ustawionego w config
- Funkcja `_expand()` w `config.py` obsługuje `~` i rozszerza ścieżki

### CSV:

- Zawsze UTF-8 encoding
- `newline=""` dla kompatybilności cross-platform
- Używaj `csv.DictReader/DictWriter` z `FIELDNAMES`

### API Keys i konfiguracja:

- **AcoustID**: `acoustid_api_key` w config (Application API key)
- **Last.fm**: `lastfm_api_key` w config lub env `LASTFM_API_KEY`
- **MusicBrainz**: User-Agent w config (`app_name`, `app_version`, `contact`)

### Zależności zewnętrzne:

````python
# Core
mutagen>=1.46          # Audio tag reading/writing
pyacoustid>=0.3        # AcoustID fingerprint lookup
requests>=2.31         # HTTP requests
requests-cache>=1.1    # HTTP caching

# Audio analysis (optional)
essentia>=2.1b6.dev0   # Local BPM/Key/Energy extraction

# Optional for enrichment
musicbrainzngs>=0.7   # MusicBrainz API client
pylast>=5.2           # Last.fm API
## Testowanie i jakość

### Test suites
- Jednostkowe: parsowanie nazw (filename), konfiguracja (config), audio cache, taxonomy, podstawowa logika placement.
- Integracyjne: komendy CLI (scan, enrich-online, apply, undo) na mini-fixtures.

### Uruchamianie
Taski:
- `TESTS — run` (pytest -q)
- `TESTS — coverage` (pokrycie z wyszczególnieniem brakujących linii)

CLI ręcznie:
```bash
pytest -q
pytest --cov=djlib --cov-report=term-missing
````

### Quality gates

- Build: brak kompilacji (pure Python) – weryfikacja instalacji przez task STEP 0.
- Tests: wszystkie muszą przejść (docelowo dodać threshold pokrycia > 80%).
- Lint: plan wprowadzenia `ruff` + `black` (CI future).

### Planowane rozszerzenia jakości

- Logowanie decyzji użytkownika o pominięciu SoundCloud do `enrich_status.json`.
- Snapshot testy dla multi-source genre fuzji (deterministyczna waga + sort alfabetyczny).
- Parametryzacja testów filename dla wielu nawiasów / duplikatów / pustych tokenów.

````

### Caching:

- HTTP requests cache'owane w `djlib_http_cache.sqlite`
- Rate limiting: 1 req/s dla MusicBrainz
- Retry logic dla API calls

---

## Przykłady Użycia

### Dodanie nowego bucketu:

```python
from djlib.taxonomy import add_ready_bucket, ensure_taxonomy_dirs

add_ready_bucket("CLUB/PROGRESSIVE HOUSE")
ensure_taxonomy_dirs()  # Tworzy katalogi
````

### Sprawdzenie czy target jest poprawny:

```python
from djlib.taxonomy import is_valid_target

is_valid_target("READY TO PLAY/CLUB/HOUSE")  # True
is_valid_target("READY TO PLAY/CLUB/UNKNOWN")  # False
```

### Użycie auto-decide:

```python
from djlib.placement import decide_bucket

row = {"genre": "tech house", "bpm": "128", ...}
bucket, confidence, reason = decide_bucket(row)
# ("CLUB/TECH HOUSE", 0.95, "genre=tech house")
```

---

## Uwagi dla Agentów AI

### Przy proponowaniu zmian:

1. **Sprawdź taxonomy.yml**: Zawsze używaj nazw zgodnych z aktualną taksonomią
2. **Używaj spacji**: Nigdy podkreślników w nazwach bucketów
3. **Format target_subfolder**: Zawsze pełna ścieżka z prefiksem (`READY TO PLAY/...`)
4. **CSV schema**: Nie dodawaj nowych kolumn bez aktualizacji `FIELDNAMES`
5. **Path handling**: Zawsze używaj `pathlib.Path`, nie strings
6. **Backward compatibility**: Przy zmianach sprawdź czy istniejące skrypty będą działać

### Przy dodawaniu nowych bucketów:

1. Dodaj do `taxonomy.yml` (sekcja `ready_buckets` lub `review_buckets`)
2. Uruchom `ensure_taxonomy_dirs()` aby utworzyć katalogi
3. Zaktualizuj `djlib/placement.py` jeśli bucket ma być rozpoznawany automatycznie
4. Zaktualizuj `rules.yml` jeśli potrzebne są reguły dla tego bucketu

### Przy modyfikacji reguł auto-decide:

- `rules.yml`: Proste reguły słów-kluczy (contains → target)
- `placement.py`: Zaawansowane reguły na podstawie metadanych (genre, BPM, era)

---

**Ostatnia aktualizacja**: 2025 (SoundCloud + per-source genres)  
**Wersja dokumentacji**: 2.1

---

### Planowane rozszerzenie `enrich_status.json`

Format (propozycja) dodający sekcję SoundCloud audytu:

```json
{
  "started_at": "2025-11-12T14:03:22Z",
  "completed_at": "2025-11-12T14:05:47Z",
  "rows_processed": 312,
  "soundcloud": {
    "client_id_status": "invalid", // ok | invalid | missing | error | rate-limit
    "decision": "aborted", // active | skipped | aborted
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

Cele:

- Transparentność: wiemy czy brak danych SC wynikał z decyzji czy błędu.
- Monitoring jakości: korelacja completeness vs. aktywność źródeł.
- Pod grunt dla automatycznych retry/reguł adaptacyjnych.

Implementacja: w komendzie `enrich-online` – wstępny zapis stanu (started), aktualizacja po health check (status SC), finalizacja przy zakończeniu lub abort.
