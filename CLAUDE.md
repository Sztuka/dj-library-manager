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

# Gig prep — dry-run (validate playlist, print plan, no writes)
.venv/bin/python -m djlib.cli gig-prep friday-2026-05-15 --from-m3u ~/Playlists/friday.m3u --dry-run

# Gig prep — live copy (RESERVE → COPY → COMMIT)
.venv/bin/python -m djlib.cli gig-prep friday-2026-05-15 --from-m3u ~/Playlists/friday.m3u

# Gig prep — resume interrupted copy
.venv/bin/python -m djlib.cli gig-prep friday-2026-05-15 --from-m3u ~/Playlists/friday.m3u --resume

# Gig merge — merge post-gig Rekordbox state back to library.csv (Phase 3)
.venv/bin/python -m djlib.cli gig-merge friday-2026-05-15
.venv/bin/python -m djlib.cli gig-merge friday-2026-05-15 --dry-run
.venv/bin/python -m djlib.cli gig-merge friday-2026-05-15 --resume

# Gig cleanup — delete MacBook audio copies after merge (Phase 4)
.venv/bin/python -m djlib.cli gig-cleanup friday-2026-05-15
.venv/bin/python -m djlib.cli gig-cleanup friday-2026-05-15 --verify-nas
.venv/bin/python -m djlib.cli gig-cleanup friday-2026-05-15 --dry-run
```

## Project Structure

```
djlib/              # Main package
  cli.py            # CLI commands (argparse)
  config.py         # Paths, settings, YAML config loader
  csvdb.py          # CSV read/write operations
  gig.py            # Gig workflow: prep (Phase 2), merge (Phase 3), cleanup (Phase 4)
  rekordbox_reader.py # Read-only Rekordbox DB fetcher for post-gig merge
  lww_merge.py      # Last-Write-Wins per-field merge engine
  library_schema.py # library.csv field definitions, safe writer, merge + gig-track guard
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

## Issue Tracking

- **GitHub Issues** — used for all backlog tickets (bugs, features, deferred tasks)
- **Habit:** before starting any non-trivial work, check if a GitHub Issue exists; create one if not
- **Linking:** reference issues in commits (`closes #N`, `refs #N`) so history is traceable
- **Create issue:** `gh issue create --title "..." --body "..."`
- **List issues:** `gh issue list`
- **View issue:** `gh issue view <N>`

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
- **library.csv:** master database, ~30 fields. Written atomically via `save_library_csv()` — never overwrite directly
- **unsorted.csv:** staging area. Rows removed by `apply` command after processing
- **LOGS/moves-\*.csv:** append-only history (src, dest, track_id). Source of truth for processed tracks
- **genres.yml:** canonical genre definitions with synonyms, categories, boost values
- **live_location / live_path:** gig-tracking fields in library.csv. `live_location` is `"nas"` (default, file on NAS), `"gig:<id>:preparing"` (copy in progress), or `"gig:<id>"` (file on MacBook, gig active). `live_path` is the MacBook-local path while the gig is active. Both fields are djlib-owned — `sync-dj-libraries` never overwrites them, and `apply_gig_track_guard` fully blocks sync updates for any track not on NAS.

## Review UI Architecture

- **Server:** Flask at `djlib/review/server.py`
- **Frontend:** single-page vanilla JS app with tab navigation (Unsorted, Library, Processed)
- **API pattern:** `/api/tracks?source=unsorted|library|processed`, `/api/genres`, `/api/library-index`
- **Column rendering:** type-based system (`rating`, `color-dot`, `source-badge`, `dest-badge`, `in-dj-badge`)
- **No build step** — static files served directly by Flask

## Important Patterns

- **Folders are logistics, not categories.** Genre lives in metadata, not folder names
- **Destinations:** library (main), reject, mixes — that's it (archive removed)
- **DJ software IDs** (rekordbox_id, traktor_id) are critical — never lose them during operations
- **File operations must be safe** — hash-check before overwrite, never create silent `(2)` copies
- **HTTP cache** exists at `djlib_http_cache/` — can be cleared with `rm -f djlib_http_cache*`

## Code Implementation Rules

- **Never add frontend frameworks** — vanilla JS only. No React, Vue, jQuery, or build tools
- **Never add ORM or database** — CSV files are the database. pandas for heavy operations
- **New API endpoints** follow pattern: `@app.route("/api/<resource>")` returning JSON
- **New UI columns** — add to `COLUMNS.<tab>` in app.js, add CSS for new badge types in style.css
- **New CLI commands** — add to `cli.py` with argparse, follow existing `cmd_<name>` pattern
- **Config changes** — add defaults to `config.yml`, document in ARCHITECTURE.md
- **Genre changes** — update `genres.yml` (maintain alphabetical order within categories)

## Data Safety

- **library.csv** is overwritten by `sync-dj-libraries`. Don't assume fields persist between syncs unless they come from DJ software
- **LOGS/moves-*.csv** is append-only history — the only reliable record of processed tracks
- **track_id** (UUID5) is the stable identifier across all systems. Never regenerate for existing tracks
- **File operations** must check for existing files before moving. Never create `(2)` copies silently
- **Always test with real data paths** in mind — paths contain spaces (`~/Music Unsorted/`), Unicode characters (é, á), and special chars (&, commas)

## File Naming

- Python modules: `snake_case.py`
- Test files: `test_<module>.py`
- CSS classes: `kebab-case` (`.badge-dest`, `.stat-processed-total`)
- JS functions: `camelCase` (`destBadgeHtml()`, `updateStats()`)
- CSV fields: `snake_case` (`track_id`, `key_camelot`)
- Git branches: `feature/<name>`, `fix/<name>`, `refactor/<name>`, `chore/<name>`

## Multi-Persona Design Workflow

For non-trivial features, consult the specialized subagents in `.claude/agents/` before writing code. Each represents a distinct perspective:

**General engineering team** (invokable for any feature):

- **Julia** — creative idea generator, opens the solution space
- **Zosia** — CTO / systems architect, guards data integrity and simplicity
- **Adam** — product designer, guards UX and Review UI patterns
- **Kasia** — product owner, guards scope and opportunity cost
- **Łukasz** — technical writer, keeps docs in sync with code
- **Marek** — destructive QA, stress-tests with dirty data and edge cases

**Domain specialists** (for genre classification work):

- **ML Researcher** — AB test design, baselines, confusion matrices
- **Taxonomy Expert** — genres.yml, genre family disputes, scene accuracy
- **DJ Domain Expert** — real-world DJ usage and crate culture
- **Data Engineer** — pipeline design, caching, external signal integration
- **Prompt Engineer** — LLM prompt design and AB test symmetry

Typical flow: **Julia** (brainstorm) → **Zosia/Adam/Kasia** (filter for feasibility, UX, scope) → implement → **Marek** (destructive test) → **Łukasz** (document) → commit.
