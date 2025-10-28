# DJ Library Manager - Dokumentacja Architektury

**Wersja:** 1.0  
**Data:** 2024  
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

DJ Library Manager to narzędzie do zarządzania biblioteką muzyczną DJ-a. System skanuje nowe pliki audio, ekstrahuje metadane, klasyfikuje utwory według gatunków i organizuje je w strukturze folderów.

### Główne funkcjonalności:

- Skanowanie INBOX i ekstrakcja tagów audio
- Automatyczna klasyfikacja utworów (AI guessing)
- Zarządzanie taksonomią kategorii (buckets)
- Automatyczne decyzje na podstawie reguł
- Przenoszenie i zmiana nazw plików
- Wykrywanie duplikatów (hash + fingerprint audio)

---

## Struktura Plików i Folderów

### Struktura projektu:

```
dj-library-manager/
├── djlib/              # Główny moduł aplikacji
│   ├── config.py       # Konfiguracja ścieżek i ustawień
│   ├── taxonomy.py     # Zarządzanie taksonomią bucketów
│   ├── csvdb.py        # Operacje na bazie CSV
│   ├── tags.py         # Czytanie tagów audio
│   ├── fingerprint.py  # Fingerprint audio i hash
│   ├── filename.py     # Generowanie nazw plików
│   ├── mover.py        # Przenoszenie plików
│   ├── classify.py     # AI guessing bucketów
│   ├── placement.py    # Automatyczne decyzje o bucketach
│   └── ...
├── scripts/            # Skrypty CLI
├── webui/              # Interfejs webowy
├── taxonomy.yml        # Definicja bucketów
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

| Kolumna            | Opis                                    | Przykład                              |
| ------------------ | --------------------------------------- | ------------------------------------- |
| `track_id`         | Unikalne ID utworu                      | `a1b2c3d4e5f6_1234567890`             |
| `file_path`        | Oryginalna ścieżka do pliku             | `/Users/user/Music/INBOX/track.mp3`   |
| `artist`           | Artysta (z tagów audio)                 | `Daft Punk`                           |
| `title`            | Tytuł utworu                            | `Get Lucky`                           |
| `version_info`     | Wersja/remix (z tagów)                  | `Radio Edit`, `Extended Mix`          |
| `bpm`              | BPM (z Traktor/analizy)                 | `120`                                 |
| `key_camelot`      | Tonacja Camelot (z Traktor)             | `6A`                                  |
| `energy_hint`      | Wskazówka energii (opcjonalnie)         | `high`, `medium`                      |
| `file_hash`        | SHA-256 hash pliku                      | `a1b2c3...`                           |
| `fingerprint`      | Audio fingerprint (Chromaprint)         | `AQAA...`                             |
| `is_duplicate`     | Czy duplikat (na podstawie fingerprint) | `true`, `false`                       |
| `ai_guess_bucket`  | Sugerowany bucket przez AI              | `READY TO PLAY/CLUB/HOUSE`            |
| `ai_guess_comment` | Komentarz do sugestii                   | `genre=house; conf=0.95`              |
| `target_subfolder` | Finalna decyzja użytkownika             | `READY TO PLAY/CLUB/HOUSE`            |
| `must_play`        | Flaga "must play" (opcjonalnie)         | `true`, `false`                       |
| `occasion_tags`    | Tagi okazji (opcjonalnie)               | `wedding`, `party`                    |
| `notes`            | Uwagi użytkownika                       | Dowolny tekst                         |
| `final_filename`   | Finalna nazwa po przeniesieniu          | `Artist - Title (Mix) [6A 120].mp3`   |
| `final_path`       | Finalna ścieżka po przeniesieniu        | `/Users/user/Music/READY TO PLAY/...` |
| `added_date`       | Data dodania do biblioteki              | `2024-01-15 10:30:00`                 |

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
- `save_records(csv_path, rows)`: Zapisuje rekordy do CSV
- `FIELDNAMES`: Lista kolumn CSV

### `djlib/tags.py`

**Zadanie**: Czytanie tagów audio z plików

**Metoda**: Używa `mutagen` do czytania tagów ID3/MP3

**Zwraca**: Dict z kluczami: `artist`, `title`, `version_info`, `bpm`, `key_camelot`, `genre`, `comment`, `energy_hint`

**Uwaga**: BPM i Key powinny być ustawione przez Traktor Pro przed skanowaniem

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

### `djlib/mover.py`

**Zadanie**: Przenoszenie i zmiana nazw plików

**Funkcje**:

- `resolve_target_path(target)`: Konwertuje target_subfolder na Path
- `move_with_rename(src, dest_dir, final_name)`: Przenosi i zmienia nazwę (obsługuje konflikty)
- `utc_now_str()`: Format daty/czasu UTC dla `added_date`

### `djlib/classify.py`

**Zadanie**: AI guessing bucketów (prosta heurystyka)

**Funkcja**:

- `guess_bucket(artist, title, bpm, genre, comment)`: Zwraca tuple `(bucket, comment)`

**Zwracane buckety**:

- `CLUB_CANDIDATES`, `OPEN_FORMAT_CANDIDATES`, `UNDECIDED`

**Uwaga**: To jest prosta heurystyka, nie prawdziwe AI. Można ją rozszerzyć.

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
- `auto-decide`: Automatyczne uzupełnianie target_subfolder
- `apply`: Przenoszenie plików zgodnie z target_subfolder
- `undo`: Cofanie ostatniej operacji przenoszenia

---

## Workflow i Procesy

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
   - Tworzy rekord w CSV

**Rezultat**: Nowe wiersze w `library.csv` z pustym `target_subfolder`

### 2. Automatyczne decyzje

**Skrypt**: `scripts/auto_decide.py` lub `djlib/cli.py::cmd_auto_decide`

**Opcje**:

- **Rules-based** (`rules.yml`): Na podstawie zawartości słów-kluczy
- **Placement-based** (`placement.py`): Na podstawie metadanych (genre, BPM, era)

**Proces (rules-based)**:

1. Dla każdego wiersza z pustym `target_subfolder`
2. Sprawdza reguły w `rules.yml` (pierwsza dopasowana)
3. Jeśli dopasowana → ustawia `target_subfolder`
4. Fallback na `ai_guess_bucket` jeśli ma `CLUB_CANDIDATES` lub `OPEN_FORMAT_CANDIDATES`

**Proces (placement-based)**:

1. Używa `djlib/placement.py::decide_bucket()`
2. Jeśli confidence >= threshold → ustawia `target_subfolder`
3. W przeciwnym razie → tylko sugeruje w `ai_guess_bucket`

### 3. Przenoszenie plików

**Skrypt**: `scripts/apply_decisions.py` lub `djlib/cli.py::cmd_apply`

**Proces**:

1. Dla każdego wiersza z `target_subfolder` i bez `final_path`:
   - Resolvuje ścieżkę docelową z `target_subfolder`
   - Generuje finalną nazwę pliku
   - Przenosi i zmienia nazwę
   - Aktualizuje `final_filename`, `final_path`, `added_date`
   - Zapisuje log do `LOGS/moves-{timestamp}.csv`

**Uwaga**: Użyj `--dry-run` aby zobaczyć co zostanie zrobione bez wykonania

### 4. Undo (Cofanie)

**Skrypt**: `scripts/undo_last.py` lub `djlib/cli.py::cmd_undo`

**Proces**:

1. Znajduje najnowszy plik logu w `LOGS/moves-*.csv`
2. Dla każdej operacji w logu:
   - Przenosi plik z powrotem
   - Czyści `final_filename`, `final_path` w CSV

---

## Rozwiązania Techniczne

### Obsługa duplikatów

- **Primarna metoda**: Chromaprint fingerprint (porównywanie audio nawet przy różnych formatach/bitrate)
- **Fallback**: SHA-256 hash (tylko identyczne pliki)
- **Implementacja**: `djlib/fingerprint.py`

### Rozwiązywanie konfliktów nazw

- Jeśli plik o takiej samej nazwie już istnieje: dodawany numer `(2)`, `(3)`, etc.
- **Implementacja**: `djlib/mover.py::move_with_rename()`

### Walidacja target_subfolder

- Sprawdzanie czy target istnieje w taxonomy przed przenoszeniem
- **Implementacja**: `djlib/taxonomy.py::is_valid_target()`

### Obsługa braku tagów

- Jeśli brak tagów: używa `"Unknown Artist"`, `"Unknown Title"`
- Jeśli brak BPM/Key: używa `"??"`
- **Implementacja**: `djlib/filename.py::build_final_filename()`

### Struktura katalogów

- Automatyczne tworzenie katalogów z taxonomy
- **Implementacja**: `djlib/taxonomy.py::ensure_taxonomy_dirs()`

### Logowanie operacji

- Wszystkie operacje przenoszenia logowane do `LOGS/moves-{timestamp}.csv`
- Format: `src_before, dest_after, track_id`

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

---

## Przykłady Użycia

### Dodanie nowego bucketu:

```python
from djlib.taxonomy import add_ready_bucket, ensure_taxonomy_dirs

add_ready_bucket("CLUB/PROGRESSIVE HOUSE")
ensure_taxonomy_dirs()  # Tworzy katalogi
```

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

**Ostatnia aktualizacja**: 2025  
**Wersja dokumentacji**: 1.0
