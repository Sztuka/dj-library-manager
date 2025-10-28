# DJ Library Manager - Quick Reference

Szybki przewodnik po najważniejszych konwencjach i strukturze systemu.

## 📁 Struktura Bucketów

### Format target_subfolder:

```
READY TO PLAY/{SECTION}/{BUCKET}
REVIEW QUEUE/{BUCKET}
```

### READY TO PLAY / CLUB:

- `CLUB/AFRO HOUSE`, `CLUB/DEEP HOUSE`, `CLUB/ELECTRO`, `CLUB/ELECTRO SWING`
- `CLUB/HOUSE`, `CLUB/MELODIC TECHNO`, `CLUB/TECH HOUSE`, `CLUB/TECHNO`, `CLUB/TRANCE`, `CLUB/DNB`
- `MIXES/`

### READY TO PLAY / OPEN FORMAT:

- `OPEN FORMAT/70s`, `OPEN FORMAT/80s`, `OPEN FORMAT/90s`, `OPEN FORMAT/2000s`, `OPEN FORMAT/2010s`
- `OPEN FORMAT/FUNK SOUL`, `OPEN FORMAT/HIP-HOP`, `OPEN FORMAT/LATIN REGGAETON`
- `OPEN FORMAT/PARTY DANCE`, `OPEN FORMAT/POLISH SINGALONG`, `OPEN FORMAT/RNB`
- `OPEN FORMAT/ROCK CLASSICS`, `OPEN FORMAT/ROCKNROLL`

### REVIEW QUEUE:

- `UNDECIDED`, `NEEDS EDIT`

## 🏷️ Naming Conventions

- **SPACJE** zamiast podkreślników: `TECH HOUSE` ✅ nie `TECH_HOUSE` ❌
- **UPPERCASE**: Wszystkie nazwy bucketów
- **Kolejność**: `MELODIC TECHNO` ✅ nie `TECHNO MELODIC` ❌
- **Wieloczłonowe**: `AFRO HOUSE` ✅ nie `AFROHOUSE` ❌

## 📝 Format Nazw Plików

```
{Artist} - {Title} ({VersionInfo}) [{Key} {BPM}]{ext}
```

**Przykład**: `Daft Punk - Get Lucky (Radio Edit) [6A 120].mp3`

## 📊 CSV Schema

Główne kolumny:

- `track_id`, `file_path`, `artist`, `title`, `version_info`
- `bpm`, `key_camelot`, `genre`, `comment`
- `file_hash`, `fingerprint`, `is_duplicate`
- `ai_guess_bucket`, `ai_guess_comment`
- `target_subfolder` ⭐ (główna decyzja)
- `final_filename`, `final_path`, `added_date`

## 🔧 Główne Moduły

- `config.py`: Ścieżki i konfiguracja
- `taxonomy.py`: Zarządzanie bucketami
- `csvdb.py`: Operacje CSV
- `tags.py`: Czytanie metadanych audio
- `fingerprint.py`: Duplikaty (hash + Chromaprint)
- `filename.py`: Generowanie nazw plików
- `mover.py`: Przenoszenie plików
- `placement.py`: Auto-decide na podstawie metadanych
- `classify.py`: Proste AI guessing

## 🎯 Workflow

1. **Skanowanie**: `scan_inbox.py` → wypełnia CSV z pustym `target_subfolder`
2. **Auto-decide**: `auto_decide.py` → uzupełnia `target_subfolder` na podstawie reguł
3. **Apply**: `apply_decisions.py` → przenosi pliki zgodnie z `target_subfolder`

## 📄 Pliki Konfiguracyjne

- `taxonomy.yml`: Definicja bucketów
- `rules.yml`: Reguły auto-decide (contains → target)
- `config.local.yml`: Ścieżki lokalne (gitignored)

## ⚠️ Ważne dla Agentów AI

1. Zawsze sprawdź `taxonomy.yml` przed dodaniem bucketu
2. Używaj SPACJI w nazwach, nie podkreślników
3. Format target: `READY TO PLAY/{SECTION}/{BUCKET}`
4. Używaj `pathlib.Path`, nie strings dla ścieżek
5. Sprawdź `FIELDNAMES` przed dodaniem kolumny do CSV

---

Pełna dokumentacja: `docs/ARCHITECTURE.md`
