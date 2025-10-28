# Dokumentacja DJ Library Manager

Ten folder zawiera dokumentację techniczną systemu dla agentów AI i deweloperów.

## Pliki

- **`ARCHITECTURE.md`** - Pełna dokumentacja architektury systemu
- **`QUICK_REFERENCE.md`** - Szybki przewodnik po najważniejszych konwencjach
- **`generate_pdf.sh`** - Skrypt do generowania PDF z dokumentacji Markdown

## Użycie

### Odczytywanie dokumentacji

Po prostu otwórz pliki `.md` w dowolnym edytorze Markdown lub przeglądarce.

### Generowanie PDF

Aby wygenerować PDF z dokumentacji:

```bash
cd docs
./generate_pdf.sh
```

**Wymagania:**

- `pandoc` - https://pandoc.org/
- `xelatex` (dla obsługi polskich znaków)

**Instalacja na macOS:**

```bash
brew install pandoc
brew install --cask basictex
```

**Instalacja na Linux:**

```bash
sudo apt-get install pandoc texlive-xetex
```

### Użycie w ChatGPT/Agentach AI

1. Skopiuj zawartość `ARCHITECTURE.md` do kontekstu agenta
2. Lub użyj `QUICK_REFERENCE.md` dla szybkiego wglądu
3. Lub wygeneruj PDF i prześlij jako załącznik (jeśli agent obsługuje PDF)

## Aktualizacja dokumentacji

Dokumentacja powinna być aktualizowana przy:

- Dodawaniu nowych bucketów do taxonomy
- Zmianach w strukturze CSV
- Nowych modułach/funkcjach
- Zmianach w workflow

## Uwaga

Ta dokumentacja opisuje aktualny stan systemu na dzień jej utworzenia. W przypadku zmian w kodzie, proszę zaktualizować odpowiednie sekcje.
