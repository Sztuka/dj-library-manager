# Installation Guide

**DJ Library Manager** — Complete installation and setup instructions.

---

## Prerequisites

| Requirement    | Version                  | Notes                    |
| -------------- | ------------------------ | ------------------------ |
| macOS or Linux | —                        | Windows not tested       |
| Python         | 3.11+ (3.13 recommended) |                          |
| Rekordbox      | 6.x                      | For database integration |
| Essentia       | Optional                 | For audio analysis       |

---

## Quick Install

```bash
# 1. Clone repository
git clone https://github.com/Sztuka/dj-library-manager.git
cd dj-library-manager

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -U pip
pip install -r requirements.txt
pip install -e .

# 4. Configure library paths
python -m djlib.cli configure
```

**VS Code Task:** `STEP 0 — Setup: create venv & install deps`

---

## Configuration

### Interactive Setup

```bash
python -m djlib.cli configure
```

This wizard will:

- Detect existing `config.local.yml`
- Prompt for library paths
- Create required directories

### Manual Configuration

Create `config.local.yml` in project root:

```yaml
library_root: "/Users/yourname/Music Library"
inbox_dir: "/Users/yourname/Music Unsorted"
reject_dir: "/Users/yourname/Music Rejected"
archive_dir: "/Users/yourname/Music Archive"
```

---

## Metadata Sources (Optional)

### Beatport (EDM Genres)

Provides highest-quality genre data for electronic music.

```bash
python -m djlib.cli setup-beatport
```

Enter your Beatport credentials. They're stored securely in system keychain.

**VS Code Task:** `TOOLS — Setup Beatport credentials`

### Other Sources

| Source      | Setup           | Notes                             |
| ----------- | --------------- | --------------------------------- |
| MusicBrainz | None required   | Free API                          |
| Last.fm     | None required   | Free API                          |
| SoundCloud  | Auto-configured | Client ID refreshes automatically |

---

## Essentia (Audio Analysis)

Essentia provides BPM, Key, and energy detection. **Optional** — the app works without it.

### Option 1: Homebrew (macOS)

```bash
brew install essentia
```

**VS Code Task:** `TOOLS — Install Essentia (Homebrew)`

### Option 2: Conda (Cross-platform)

```bash
conda install -c conda-forge essentia
```

### Verify Installation

```bash
python -m djlib.cli analyze-audio --check-env
```

Expected output:

```text
✅ essentia_available: True
✅ essentia_cli_available: True
✅ cli_binary: /opt/homebrew/bin/essentia_streaming_extractor_music
```

---

## Rekordbox Configuration

For best results, configure Rekordbox:

1. **Preferences → Advanced → Browse**

   - Enable: "Write metadata to files" (Every time)

2. **Preferences → Analysis**

   - Enable: "Advanced Analysis"

3. **Preferences → View**
   - Set Key notation to **Camelot** (1A-12B)

---

## Traktor Configuration (Optional)

If using Traktor alongside Rekordbox:

1. **Preferences → File Management**
   - ✅ Enable: "Import track metadata from file tags"
   - ❌ Disable: "Update file tags when changing track metadata"

This prevents Traktor from overwriting BPM/Key analyzed by Rekordbox.

---

## Directory Structure

After setup, you should have:

```text
~/Music Unsorted/     # Drop new tracks here
~/Music Library/      # Organized tracks (by artist)
~/Music Rejected/     # Rejected tracks (flat)
~/Music Archive/      # Archived tracks (by artist)

dj-library-manager/
├── data/
│   ├── unsorted.csv  # Staging CSV
│   └── library.csv   # Master database
├── LOGS/             # Operation logs
├── config.local.yml  # Your configuration
└── ...
```

---

## Verify Setup

### Check Configuration

```bash
python -m djlib.cli configure
# Should show current paths
```

### Check Audio Environment

```bash
python -m djlib.cli analyze-audio --check-env
```

### Test Scan

```bash
# Put some tracks in ~/Music Unsorted/
python -m djlib.cli scan
# Check data/unsorted.csv
```

---

## VS Code Integration

The project includes predefined tasks. Open Command Palette (Cmd+Shift+P) → "Tasks: Run Task":

| Task                       | Description                    |
| -------------------------- | ------------------------------ |
| `STEP 0 — Setup`           | Create venv, install deps      |
| `STEP 1 — Configure`       | Run configuration wizard       |
| `TOOLS — Setup Beatport`   | Configure Beatport credentials |
| `TOOLS — Install Essentia` | Install Essentia via Homebrew  |
| `TOOLS — Check audio env`  | Verify Essentia installation   |

---

## Troubleshooting

### Python Version

```bash
python3 --version
# Should be 3.11 or higher
```

### Virtual Environment

```bash
# Activate venv
source .venv/bin/activate

# Check pip packages
pip list | grep mutagen
```

### Rekordbox Database Access

The app uses `pyrekordbox` to access Rekordbox database. If you get errors:

1. Close Rekordbox completely
2. Check database exists: `~/Library/Pioneer/rekordbox/master.db`
3. Database is encrypted — `pyrekordbox` handles decryption

### Permission Errors

```bash
# Fix fpcalc permissions (if bundled)
chmod +x bin/mac/fpcalc

# Remove quarantine attribute (macOS)
xattr -d com.apple.quarantine bin/mac/fpcalc
```

### Missing Dependencies

```bash
# Reinstall all dependencies
pip install -r requirements.txt --force-reinstall
```

---

## Updating

```bash
cd dj-library-manager
git pull
pip install -r requirements.txt
pip install -e .
```

---

## Uninstalling

```bash
# Remove virtual environment
rm -rf .venv

# Remove configuration
rm config.local.yml

# Remove cache
rm -rf LOGS/
rm djlib_http_cache.sqlite
```

Your music files in `~/Music Library/` etc. are not affected.
