# CLAUDE.md — DJ Library Manager

## Project Overview

DJ library organizer: scans unsorted audio → enriches metadata (Beatport, MusicBrainz, Last.fm, SoundCloud) → Review UI curation → moves to organized library → syncs Rekordbox/Traktor databases.

**Owner:** solo developer (DJ + engineer). **Language of conversation:** Polish. **Language of code/commits:** English.

## Tech Stack

- **Python 3.13**, venv at `.venv/`, macOS primary
- **Flask 3.1** — Review UI server (port 8899)
- **Vanilla JS + CSS** — no frontend frameworks (no React, Vue, etc.)
- **CSV as database** — `data/unsorted.csv` (staging), `data/library.csv` (master), `LOGS/moves-*.csv` (history)
- **mutagen** — audio tag reading/writing
- **pyrekordbox** / **traktor-nml-utils** — DJ software integration
- **Essentia** (optional) — audio feature extraction for ML
- **pandas** — data processing, ML export
- **requests-cache** — HTTP caching for API calls

## Key Commands

```bash
# Run all tests
.venv/bin/pytest -q

# Run tests with coverage
.venv/bin/pytest --cov=djlib --cov-report=term-missing

# Start Review UI server
.venv/bin/python -m djlib.review.server

# Main CLI entry
.venv/bin/python -m djlib.cli <command>
```

## Project Structure

```
djlib/              # Main package
  cli.py            # CLI commands (argparse)
  config.py         # Paths, settings, YAML config loader
  csvdb.py          # CSV read/write operations
  review/           # Flask Review UI
    server.py       # API endpoints + HTML serving
    static/         # app.js, style.css
    templates/      # index.html (Jinja2)
  metadata/         # API clients (beatport, mb_client, lastfm, soundcloud)
  audio/            # Essentia feature extraction + SQLite cache
  ml/               # ML dataset export
data/               # CSV databases (gitignored except .gitkeep)
LOGS/               # Move logs, scan status, audio cache (gitignored)
tests/              # pytest tests
genres.yml          # Canonical genre definitions (~680 lines, 60+ genres)
config.yml          # Default config (paths, API keys)
config.local.yml    # Local overrides (gitignored)
```

## Coding Conventions

- **Formatter:** Black (format on save)
- **Linter:** Ruff
- **Type checking:** Pyright basic mode
- **Imports:** `from __future__ import annotations` at top of every module
- **Typing:** use `from typing import Dict, List, Optional` style (not `dict[]` builtins) for consistency with existing code
- **Comments in code:** English. Occasional Polish in older modules — don't convert existing ones
- **CSV field names:** snake_case (e.g. `track_id`, `key_camelot`, `pop_playcount`)
- **Config keys:** UPPER_CASE for paths, lowercase for nested settings
- **No classes for data** — use dicts and plain functions. The codebase is functional-style
- **Error handling:** log warnings, don't crash. Graceful fallback for missing data

## Git Workflow

- **NEVER commit directly to `main`** — always create a feature/fix branch first, work there, then merge to main
- **Branch strategy:** feature branches from `main` (e.g. `feature/review-ui`, `fix/genre-resolver-remix-scoring`)
- **Commit format:** Conventional Commits — `feat(scope): description`, `fix(scope): description`, `refactor:`, `chore:`
- **Commit body:** detailed, explain WHY not just WHAT. Use bullet points for multi-change commits
- **Before committing:** always run full test suite (`pytest -q`) and verify 0 failures
- **Multi-line commits:** use `git commit -F /tmp/commit_msg.txt` to avoid terminal escaping issues

## Testing

- **Framework:** pytest
- **Location:** `tests/` directory
- **Naming:** `test_<module>.py`, functions `test_<behavior>()`
- **Fixtures:** use `@pytest.fixture`, Flask test client via `app.test_client()`
- **Mocking:** `unittest.mock.patch` for filesystem/API calls, `tempfile` for test data
- **Coverage target:** test all API endpoints and core logic. Edge cases matter
- **Always run tests before commit** — never commit with failing tests

## Data Architecture

- **track_id:** UUID5 hash (stable, derived from file content). Primary key across all CSVs
- **library.csv:** master database, ~30 fields. Overwritten entirely by `sync-dj-libraries`
- **unsorted.csv:** staging area. Rows removed by `apply` command after processing
- **LOGS/moves-\*.csv:** append-only history (src, dest, track_id). Source of truth for processed tracks
- **genres.yml:** canonical genre definitions with synonyms, categories, boost values

## Review UI Architecture

- **Server:** Flask at `djlib/review/server.py`
- **Frontend:** single-page vanilla JS app with tab navigation (Unsorted, Library, Processed)
- **API pattern:** `/api/tracks?source=unsorted|library|processed`, `/api/genres`, `/api/library-index`
- **Column rendering:** type-based system (`rating`, `color-dot`, `source-badge`, `dest-badge`, `in-dj-badge`)
- **No build step** — static files served directly by Flask

## Important Patterns

- **Folders are logistics, not categories.** Genre lives in metadata, not folder names
- **Destinations:** library (main), reject, archive, mixes — that's it
- **DJ software IDs** (rekordbox_id, traktor_id) are critical — never lose them during operations
- **File operations must be safe** — hash-check before overwrite, never create silent `(2)` copies
- **HTTP cache** exists at `djlib_http_cache/` — can be cleared with `rm -f djlib_http_cache*`
