# Unsorted.csv Analysis — 2026-03-01

## Summary: 205 tracks (post scan + enrich)

| Metric | Value | Status |
|---|---|---|
| BPM / Key | 205/205 | OK |
| Title suggest | 205/205 | OK |
| Artist suggest | 194/205 (11 missing) | ⚠️ |
| Genre suggest | 169/205 (36 missing) | ⚠️ |
| Year suggest | 156/205 (49 missing) | ⚠️ |
| Playcount/Listeners | 150/205 | OK |
| Genre mapping OK | 159/205 | — |
| UNMAPPED | 10 | ❌ |
| Status set | 0/205 | — |
| Destination set | 0/205 | — |

## Issues

### 1. 88 tracks have multi-genre instead of single resolved genre ❌

`genre_suggest` contains comma-separated lists instead of one canonical genre.
Genre resolver should pick ONE best match from genres.yml.

Worst examples:
- `carlos pepper, elias rojas, rafael oliver` — artists, not genres (SoundCloud garbage)
- `afro amapiano fastcar` — concatenated tag without separator
- `afro house, alternative rock, trance` — three completely different genres

### 2. 10 UNMAPPED genres ❌

Genre mapper doesn't recognize these values:
- `african` → should map to `afro house` or separate category
- `amapiano` → missing from genres.yml
- `dance edm` → SoundCloud category, should map to `dance` or `edm`
- `ambient experimental` → Beatport genre, missing mapping
- `afro, amapiano` → multi-value + no mapping
- `carlos pepper, elias rojas, rafael oliver` → artist names, not genres

### 3. 34 tracks `filename|tags_fallback` without genre ⚠️

Files not found by any API. Metadata from filename only. No genre tags in files.
Could use folder name as heuristic (e.g. `Afro House/` folder).

### 4. 11 tracks without artist ⚠️

Files in `Afro House/` without tags — mostly `.wav` files without ID3/Vorbis tags.

### 5. Year 2026 entries 🤔

5 tracks with `year_suggest=2026` — verify if correct.

## Top genres

| Genre | Count |
|---|---|
| dance | 17 |
| house | 11 |
| dance pop | 8 |
| afro house | 8 |
| tech house | 7 |
| latin | 5 |
| electronica | 4 |
| deep house | 3 |

## Recommendations (priority order)

1. **Genre resolver** — should return one canonical genre, not a list
2. **genres.yml** — add mappings: `amapiano`, `african`, `dance edm`, `ambient experimental`
3. **SoundCloud tag quality** — filter tags that look like artist names
4. **Filename fallback** — use folder name as heuristic genre source for 34 unmatched tracks
