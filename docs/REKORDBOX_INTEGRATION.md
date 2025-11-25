# Rekordbox Integration Guide

## Overview

The `djlib.rekordbox_status` module provides:
1. **Detection** - Whether files have been analyzed in Rekordbox (BPM & Key)
2. **Metadata Extraction** - Direct extraction of BPM/Key from Rekordbox database

This ensures all files have proper tempo and harmony metadata with authoritative values from Rekordbox.

## NEW: Direct Metadata Extraction

### Why Extract from Database?

Rekordbox doesn't always write BPM/Key to file tags (especially for FLAC files):

- **MP3**: Sometimes writes TBPM/TKEY tags, but not consistently
- **FLAC**: Rarely writes tags, only stores in database
- **Database is authoritative**: Most up-to-date values after manual corrections

### `extract_metadata_from_db(path: Path) -> Dict[str, str]`

Extracts BPM and Key directly from Rekordbox database.

**Returns:**

```python
{
    'bpm': '112.57',          # 2 decimal precision (Rekordbox displays format)
    'key_camelot': '1B'       # Camelot notation (converted from musical notation)
}
```

**Features:**

- **BPM Precision**: 2 decimal places (e.g., 112.57) as shown in Rekordbox
- **Key Conversion**: Automatic conversion from Rekordbox musical notation (B, Cm) to Camelot (1B, 5A)
- **Unicode Normalization**: Handles macOS path normalization (NFC) for reliable matching

**Integration:**

Used in `scan` command after reading file tags:

```python
from djlib.rekordbox_status import extract_metadata_from_db

# Read tags first
tags = read_tags(path)

# Then extract from Rekordbox DB (overrides file tags)
db_meta = extract_metadata_from_db(path)
if db_meta:
    tags.update(db_meta)  # DB values are more authoritative
```

**Example Output:**

```
Bruce Springsteen - Dancing In The Dark
  File tags:  bpm=???, key=???
  DB values:  bpm=112.57, key=1B  ← Extracted from Rekordbox
```

## Detection Strategy

### Priority Order

1. **Rekordbox Database** - Primary check (authoritative)
2. **ID3 Tags (TBPM + TKEY)** - Fallback (could be from any DJ software)

### Why DB First?

**NEW BEHAVIOR (enforces Rekordbox-specific analysis):**

The DB check confirms files were analyzed **specifically in Rekordbox**, not in Traktor/Serato/other tools. This ensures:

- ✅ Consistent analysis quality (Rekordbox's algorithms)
- ✅ Professional-grade BPM/Key detection
- ✅ Verification that files went through proper Rekordbox import/analysis workflow

**Tags as fallback** provide flexibility:

- ✅ Work after file moves (DB paths become stale)
- ⚠️ Could be from Traktor/Serato (less authoritative)
- ✅ Better than blocking workflow entirely

### Strict Mode

Use `--strict` flag with `scan` command to **enforce Rekordbox DB confirmation**:

```bash
# Strict: ONLY accept files in Rekordbox DB
$ djlib scan --strict

# Normal: Accept DB OR tags (more flexible)
$ djlib scan
```

**When to use strict mode:**

- ✅ UNSORTED folder (before import) - enforce quality
- ✅ Want to ensure Rekordbox analysis only
- ✅ Reject files with Traktor/Serato tags

**When to use normal mode:**

- ✅ After moving files (DB paths stale)
- ✅ Mixed workflow (Traktor + Rekordbox)
- ✅ Accept tags from any DJ software

## Common Scenarios

### ✅ Scenario 1: Normal Workflow

```
1. Import track to Rekordbox collection
2. Analyze in Rekordbox → writes BPM/Key to DB + tags
3. Run `scan` → checks tags → finds TBPM+TKEY → ✓ analyzed
4. Move file with `apply` → tags travel with file
5. Run `scan` again → checks tags → still works ✓
```

**Result:** ✅ Works perfectly. Tags are portable.

### ⚠️ Scenario 2: Manual Edit Outside Rekordbox

```
1. Analyze in Rekordbox → BPM=120.0, Key=5A
2. Open in Traktor → manually correct BPM to 119.5
3. Traktor updates TBPM tag to 119.5
4. Run `scan` → checks tags → finds TBPM+TKEY → ✓ analyzed
```

**Result:** ✅ Detects as analyzed. Respects manual corrections.

**Note:** The ML training will see both `tag_bpm: 119.5` (from tags) and `ess_bpm: 121.3` (from Essentia cache), allowing models to learn from discrepancies.

### ❌ Scenario 3: File Moved Before Tags Written

```
1. Import to Rekordbox at /UNSORTED/track.mp3
2. Analyze → writes to DB, but tags not saved yet
3. Move file to /Library/House/track.mp3 (outside Rekordbox)
4. Run `scan` → checks tags → not found → checks DB → wrong path → ✗ not found
```

**Result:** ❌ Fails. DB has old path, tags missing.

**Solution:** Always ensure Rekordbox writes tags during analysis:

- Settings → Advanced → Browse → "Write metadata to files"
- Or: Select all → Right-click → "Relocate" to update DB paths

### ⚠️ Scenario 4: Tags Modified Outside Rekordbox

```
1. Analyze in Rekordbox → TBPM=120, TKEY=5A
2. Some tool deletes TBPM tag
3. Run `scan` → checks tags → missing TBPM → checks DB → found → ✓ analyzed
```

**Result:** ⚠️ Detected via DB fallback, but fragile.

**Recommendation:** Maintain tag integrity. Use MP3Tag or Mutagen carefully.

## Database Details

### Connection

The module automatically discovers Rekordbox DB on macOS:

```
~/Library/Pioneer/rekordbox/master.db
~/Library/Pioneer/rekordbox/datafile.edb
```

Connection is cached for performance. The DB uses SQLCipher encryption and is accessed via `pyrekordbox`.

### DB Schema (Relevant Fields)

From `djmdContent` table:

- `UUID` - Unique track ID (not in tags, not portable)
- `FolderPath` - Full file path (breaks after moves)
- `BPM` - BPM × 100 (e.g., 11954 = 119.54)
- `KeyID` / `KeyName` - Key in Rekordbox notation (e.g., "9m")
- `Analysed` - Analysis flag (bitmask, 105 = analyzed)

### Why Not Use UUID?

Rekordbox doesn't write UUID to ID3 tags, so:

- UUID only exists in DB
- UUID → path mapping breaks after file moves
- Tags (TBPM/TKEY) are the only portable identifier

## API Reference

### `was_analyzed(path: Path, *, use_db: bool = True) -> bool`

High-level function used by `scan` workflow.

**Returns:**

- `True` if file has BPM+Key (from tags or DB)
- `False` if analysis missing

**Example:**

```python
from pathlib import Path
from djlib.rekordbox_status import was_analyzed

path = Path("/Music/track.mp3")
if was_analyzed(path):
    print("✓ Ready for processing")
else:
    print("✗ Analyze in Rekordbox first")
```

### `was_analyzed_from_tags(path: Path) -> bool`

Check ID3 tags only (TBPM + TKEY).

**Returns:**

- `True` if both TBPM and TKEY exist and non-empty
- `False` otherwise

### `was_analyzed_from_db(path: Path) -> Optional[bool]`

Check Rekordbox database.

**Returns:**

- `True` if found in DB with BPM+Key
- `False` if found but missing data
- `None` if DB unavailable or track not found

### `debug_print_db_status() -> None`

Print diagnostic information about Rekordbox DB.

**Output:**

```
✅ Rekordbox database found: /Users/.../master.db
   Tracks in database: 5694
```

## Configuration

### Rekordbox Settings

Ensure these settings are enabled:

1. **Preferences → Advanced → Browse**

   - ☑ "Write metadata to files"
   - Frequency: "Every time"

2. **Analysis Settings**
   - ☑ "Analyze tracks automatically"
   - ☑ "BPM"
   - ☑ "Key"

### Graceful Degradation

If `pyrekordbox` not installed:

- Falls back to tag-only detection
- Prints warning: "⚠️ pyrekordbox not installed - DB queries disabled"

If DB not found:

- Falls back to tag-only detection
- Prints warning: "⚠️ Rekordbox database not found"

If Rekordbox is running:

- Shows warning but continues: "Rekordbox is running!"
- DB access works (read-only)

## Troubleshooting

### "Track not detected as analyzed but I analyzed it"

1. **Check if tags were written:**
   ```bash
   python -c "from mutagen.id3 import ID3; tags = ID3('track.mp3'); print(tags.get('TBPM'), tags.get('TKEY'))"
   ```
2. **If tags missing, force Rekordbox to write them:**

   - Rekordbox → Right-click track → "Relocate"
   - Or: Settings → ensure "Write metadata to files" enabled

3. **Check DB status:**
   ```bash
   python -m djlib.cli
   # Then in Python:
   from djlib.rekordbox_status import debug_print_db_status
   debug_print_db_status()
   ```

### "After moving files, detection fails"

**This is expected if tags missing!**

Solution:

1. Ensure Rekordbox writes tags during analysis
2. Or: Update Rekordbox collection after moves:
   - Rekordbox → Right-click folder → "Relocate"
   - Select new location → Rekordbox updates DB paths

### "Want to use UUID instead of path"

Not possible - Rekordbox doesn't write UUID to tags. Alternatives:

1. **Best:** Ensure tags always written during analysis
2. **Good:** Maintain Rekordbox collection in sync (use "Relocate")
3. **Workaround:** Build local UUID cache (complex, not implemented)

## Integration with Workflow

### In `scan` Command

```python
from djlib.rekordbox_status import was_analyzed

# Check all files before generating unsorted.xlsx
not_analyzed = []
for file_path in all_files:
    if not was_analyzed(file_path):
        not_analyzed.append(file_path)

if not_analyzed:
    print("ERROR: These files need Rekordbox analysis:")
    for path in not_analyzed:
        print(f"  - {path}")
    sys.exit(1)
```

### In ML Training

The `ml-export-training-dataset` command creates:

- `tag_bpm` / `tag_key_camelot` - from Rekordbox tags (via scan)
- `ess_bpm` / `ess_key_camelot` - from Essentia cache

This allows ML models to:

- Compare professional (Rekordbox) vs algorithmic (Essentia) analysis
- Learn from manual corrections
- Handle discrepancies intelligently

## Future Enhancements

### Possible Improvements

1. **Local UUID cache** - Map UUID → current path

   - Survives file moves
   - Requires maintenance overhead

2. **Rekordbox XML export** - Alternative to DB

   - More portable
   - Updated on export (not real-time)

3. **AcoustID fingerprints** - Content-based matching
   - Works regardless of path/UUID
   - Requires online lookup or local DB

### Not Planned

- Direct SQLCipher integration (pyrekordbox is sufficient)
- Write support (read-only by design, Rekordbox owns the data)
- UUID in tags (not Rekordbox's behavior, would break compatibility)

## Summary

**Key Takeaways:**

1. ✅ **Tags are source of truth** - TBPM+TKEY travel with files
2. ⚠️ **DB is fallback** - Works before moves, fragile after
3. ✅ **Respect manual edits** - Tags reflect DJ's corrections
4. ⚠️ **Ensure Rekordbox writes tags** - Critical for portable workflow
5. ✅ **Graceful degradation** - Works without DB if tags present

**Recommended Setup:**

- Rekordbox → Write metadata to files: ON
- Analyze before moving files
- If move needed: Update Rekordbox collection or rely on tags
