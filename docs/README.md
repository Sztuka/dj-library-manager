# DJ Library Manager

Lokalny pomocnik do **porządkowania biblioteki DJ-a**: skanuje INBOX, sugeruje docelowe kubełki (foldery), robi „dry-run” i bezpieczne przenosiny z opcją **undo**. Działa pod **Rekordbox** / granie z dysku/pendrive (foldery), nie ingeruje w Twoje playlisty.

## Features (MVP)

- **Setup Wizard (web)** – 3 kroki: _Lokalizacja_ → _Taksonomia_ → _Foldery & Skan_.
- **Struktura dysku**: `READY TO PLAY` (CLUB / OPEN FORMAT / MIXES) oraz `REVIEW QUEUE` (UNDECIDED / NEEDS EDIT).
- **Scan → CSV** – metadane audio (rozmiar, SHA256, tagi; BPM/key jeśli już w pliku).
- **Auto-decide** – zasady z `rules.yml` (na razie proste reguły).
- **Apply (dry-run / real)** – przenosi pliki do docelowych kubełków; **Undo** cofa ostatnie przenosiny.
- **Zero „podłóg”** – nazwy kubełków/folderów z **przerwami, UPPERCASE**.

## Wymagania

- macOS (testowane), Python **3.11+** (3.13 OK).
- `fpcalc` (Chromaprint) – przyda się później do fingerprintów.
  - Task: **TOOLS — Install fpcalc (Homebrew)** albo **TOOLS — Install fpcalc (Download vendor)**.

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
3. **STEP 2 — Scan INBOX_UNSORTED** _(jeśli nie skanowałeś w kroku 3)_
4. **STEP 3 — Auto-decide (rules.yml) — only empty** _(uzupełnia tylko brakujące decyzje)_
5. **STEP 4 — Apply decisions (dry-run)** – pokazuje co zostanie przeniesione.
6. **STEP 5 — Apply decisions** – wykonuje przenosiny.  
   Dodatkowo: **TOOLS — Undo last moves**, **TOOLS — Report duplicates**.

## Pliki konfiguracyjne

- **`config.yml`** (zapisywany przez wizard):
  ```yaml
  LIB_ROOT: /Volumes/Music/Library
  INBOX_UNSORTED: /Volumes/Music/INBOX_UNSORTED
  CSV_PATH: data/library.csv
  ```
