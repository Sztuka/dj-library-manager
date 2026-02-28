# AGENTS.md — DJ Library Manager

Instructions for AI agents working on this codebase. Read @CLAUDE.md for project context first.

## Communication Protocol

**Language:** Conversations happen in Polish. Code, commits, variable names, and documentation are in English.

## Decision-Making: Multi-Persona Approach

When implementing non-trivial features, discuss the design through three perspectives before writing code:

### CTO (Architecture)
- Edge cases: what happens when data is missing, malformed, or empty?
- Performance: will this scale with 5000+ tracks in library.csv?
- Data integrity: does this touch track_id, library.csv, or DJ software IDs? If yes, be extra careful
- Backward compatibility: does this break existing data formats or CLI commands?

### Product Designer (UX)
- What columns should the UI show? What's their order and type?
- Visual hierarchy: badges, color coding, star ratings — match existing patterns
- Empty states: what does the user see when there's no data?
- Consistency: follow existing column type system (`rating`, `color-dot`, `source-badge`, etc.)

### Product Owner (Scope)
- Is this feature needed now or is it scope creep?
- What's the minimal useful implementation?
- User story: "As a DJ, I want to X so that Y"
- Priority: core workflow features > nice-to-have polish

## Workflow: Feature Implementation

1. **Measure first** — before making architecture decisions, analyze the actual data. Run Python scripts to count records, check coverage, find edge cases
2. **Discuss design** — use CTO/PD/PO perspectives (above) to evaluate options
3. **Implement** — write code following conventions in CLAUDE.md
4. **Test** — write pytest tests for new endpoints/logic. Mock filesystem and external APIs
5. **Audit** — run full test suite, check for errors, verify edge cases
6. **Commit** — conventional commit format with detailed body explaining WHY

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
- **LOGS/moves-*.csv** is the only reliable history of processed tracks. These are append-only
- **track_id** (UUID5) is the stable identifier across all systems. Never regenerate for existing tracks
- **File operations** must check for existing files before moving. Never create `(2)` copies silently
- **Always test with real data paths** in mind — paths contain spaces (`~/Music Unsorted/`), Unicode characters (é, á), and special chars (&, commas)

## Testing Expectations

- Every new API endpoint gets at least 2 tests: happy path + empty/error state
- Use `tempfile.TemporaryDirectory()` for file-system tests
- Mock external dependencies: `@patch("djlib.review.server.LOGS_DIR")`, etc.
- Test data should be realistic — use actual CSV field names and formats
- Run `pytest -q` before every commit. Zero failures required

## File Naming

- Python modules: `snake_case.py`
- Test files: `test_<module>.py`
- CSS classes: `kebab-case` (`.badge-dest`, `.stat-processed-total`)
- JS functions: `camelCase` (`destBadgeHtml()`, `updateStats()`)
- CSV fields: `snake_case` (`track_id`, `key_camelot`)
- Git branches: `feature/<name>`, `fix/<name>`, `refactor/<name>`
