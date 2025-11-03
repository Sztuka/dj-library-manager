# DJ Library Manager (MVP)

Quick start (local BPM/Key without Traktor):

- See `docs/INSTALL.md` for installing Essentia (optional but recommended)
- Run tasks:
   - STEP 0 — Setup: create venv & install deps
   - TOOLS — Check audio env (verifies Essentia)
   - STEP 2 — Analyze audio (cache)
- Generate a preview: `python scripts/report_preview.py` (adds detected bpm/key/energy columns)

Workflow:

1. Skopiuj nowe pliki do `~/Music_DJ/INBOX_UNSORTED`.
2. **W Traktorze** zrób Analyze (BPM + Key), potem **Write Tags to File**.
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

- Edytuj `djlib/config.py` – ustaw `LIB_ROOT` na `/Users/piotr/Music_DJ` lub inną.
- Zainstaluj zależności: `pip install -r requirements.txt`
- (Opcjonalnie) fingerprint audio wymaga `pyacoustid` i narzędzia `fpcalc` (Chromaprint). Bez tego fingerprint będzie pusty, ale skan działa.

## Struktura CSV

Kolumny: patrz `djlib/csvdb.py::FIELDNAMES`.

## Uwaga

To jest MVP. Fingerprint działa tylko jeśli masz `fpcalc`. Brak `fpcalc` → duplikaty wykrywane po `file_hash`.
