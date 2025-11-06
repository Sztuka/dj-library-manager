# DJ Library Manager (MVP)

Quick start (local BPM/Key without Traktor):

- See `docs/INSTALL.md` for installing Essentia (optional but recommended)
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
