# Rekordbox & Traktor Integration

**Status:** ✅ Phase 1, 2, 3 COMPLETED (Full sync with write operations)  
**Date:** November 2025

---

## Philosophy

The DJ Library Manager integrates with Rekordbox and Traktor as **external sources of truth** and **automatic sync targets**.

**Core principles:**

1. **Auto-sync by default** - WORKFLOW 0, 1, and 4 automatically synchronize DJ software
2. **Backup always** - Automatic backups before any write operation
3. **Full integration** - Read IDs, write new tracks, update paths for moved tracks
4. **Custom tags** - DJLIB_* tags ensure permanent track identification

---

## Current Integration (COMPLETED)

### Rekordbox (Read + Write)

**Fully implemented:**

1. **BPM/Key validation** (`scan --strict`)

   - Checks if files exist in Rekordbox DB
   - Extracts BPM/Key from DB (more reliable than tags, especially for FLAC)
   - See `djlib/rekordbox_status.py`

2. **Metadata extraction** (`scan`)
   - Reads analysis results from Rekordbox 6 SQLite DB
   - Location: `~/Library/Pioneer/rekordbox/master.db`
   - Uses SQLCipher decryption

3. **Read track IDs** (`get_rekordbox_track_ids()`)
   - Returns {Path: rekordbox_id} mapping from DB
   - Used in WORKFLOW 1 (scan) for auto-tagging

4. **Write to database** (`add_tracks_to_rekordbox()`)
   - Adds new tracks via `pyrekordbox.add_content()`
   - Updates paths for moved tracks via `update_content_path()`
   - Used in WORKFLOW 0 and WORKFLOW 4 (auto-sync)

**Usage:**

```python
from djlib.rekordbox_status import was_analyzed, extract_metadata_from_db
from djlib.external_sync import get_rekordbox_track_ids, add_tracks_to_rekordbox

# Read validation (existing):
if was_analyzed(file_path, strict=True):
    metadata = extract_metadata_from_db(file_path)
    # Returns: {bpm, key_camelot, analyzed_at}

# Read track IDs (NEW):
rekordbox_map = get_rekordbox_track_ids()
rekordbox_id = rekordbox_map.get(file_path)

# Write to Rekordbox DB (NEW):
add_tracks_to_rekordbox(library_csv="library.csv", dry_run=False)
```

---

## Traktor Integration (COMPLETED)

**Fully implemented:**

1. **Read track IDs** (`get_traktor_track_ids()`)
   - Parses `collection.nml` XML
   - Returns {Path: traktor_audio_id} mapping
   - Used in WORKFLOW 1 (scan) for auto-tagging

2. **Write to collection** (`add_tracks_to_traktor()`)
   - Adds new tracks to `collection.nml` via XML manipulation
   - Updates paths for moved tracks (existing entries)
   - Creates automatic backups (`collection.nml.backup`)
   - Used in WORKFLOW 0 and WORKFLOW 4 (auto-sync)

**Usage:**

```python
from djlib.external_sync import get_traktor_track_ids, add_tracks_to_traktor

# Read track IDs:
traktor_map = get_traktor_track_ids()
traktor_id = traktor_map.get(file_path)

# Write to Traktor:
add_tracks_to_traktor(library_csv="library.csv", dry_run=False)
```

---

## Automatic Sync Workflows

### WORKFLOW 0: Sync DJ Libraries & Tags

**Command:** `python -m djlib.cli sync-dj-libraries --write`

**Purpose:** Ensure library.csv is in sync with Rekordbox/Traktor

**Actions:**
1. Compare library.csv with Rekordbox DB and Traktor collection.nml
2. Identify missing tracks
3. Add missing tracks to both DJ software
4. Update paths for moved tracks
5. Add custom DJLIB tags where missing

### WORKFLOW 1: Scan UNSORTED (Auto-Sync)

**Command:** `python -m djlib.cli scan --strict`

**Auto-sync actions:**
1. Read Rekordbox DB → extract rekordbox_id
2. Read Traktor collection.nml → extract traktor_id
3. Tag all files with DJLIB_TRACK_ID + rekordbox_id + traktor_id
4. Store IDs in library.csv

### WORKFLOW 4: Export (Auto-Sync)

**Command:** `python -m djlib.cli apply`

**Auto-sync actions:**
1. Move approved tracks to LIBRARY
2. Add new tracks to Rekordbox (via `pyrekordbox.add_content()`)
3. Add new tracks to Traktor (via XML manipulation)
4. Update paths for moved tracks in Traktor
5. Tag all files with updated DJLIB_* tags

---

## Implementation Status

### Phase 1: Import Snapshots (READ-ONLY) - ✅ COMPLETED

**Implemented via:** `get_rekordbox_track_ids()` and `get_traktor_track_ids()`

**Purpose:** Read DJ software databases to get track ID mappings

**Rekordbox:**
```python
from djlib.external_sync import get_rekordbox_track_ids

# Returns {Path: rekordbox_id} mapping
rekordbox_map = get_rekordbox_track_ids()
```

**Traktor:**
```python
from djlib.external_sync import get_traktor_track_ids

# Returns {Path: traktor_audio_id} mapping
traktor_map = get_traktor_track_ids()
```

**Used in:** WORKFLOW 1 (scan) for auto-tagging files with rekordbox_id/traktor_id

---

### Phase 2: Path Mapping (READ-ONLY) - ✅ COMPLETED

**Implemented via:** Custom DJLIB tags in `djlib/djlib_tags.py`

**Tags written to files:**
- `DJLIB_TRACK_ID` - Permanent UUID5 (path + metadata)
- `DJLIB_REKORDBOX_ID` - Rekordbox DB primary key
- `DJLIB_TRAKTOR_ID` - Traktor AUDIO_ID
- `DJLIB_SNAPSHOT_DATE` - ISO 8601 timestamp
- `DJLIB_ORIGINAL_PATH` - Original file location

**Format support:** MP3 (ID3v2.4 TXXX), FLAC (Vorbis Comments), M4A (iTunes atoms)

**Used in:** WORKFLOW 1 (scan) and WORKFLOW 4 (apply) for permanent track identification

---

### Phase 3: Write Operations - ✅ COMPLETED

**Implemented via:** `add_tracks_to_rekordbox()` and `add_tracks_to_traktor()`

**Rekordbox write:**
```python
from djlib.external_sync import add_tracks_to_rekordbox

# Add new tracks + update paths
add_tracks_to_rekordbox(library_csv="library.csv", dry_run=False)
```

**Traktor write:**
```python
from djlib.external_sync import add_tracks_to_traktor

# Add new tracks + update paths via XML
add_tracks_to_traktor(library_csv="library.csv", dry_run=False)
```

**Safety features:**
- Automatic backups (Rekordbox DB copy, Traktor `collection.nml.backup`)
- Dry-run mode for preview
- Error handling with detailed logging
- Preserves existing metadata, only updates paths

**Used in:** WORKFLOW 0 (sync-dj-libraries) and WORKFLOW 4 (apply) for automatic sync

---

## Legacy Documentation (Historical)

The sections below describe the original design. Implementation matches the design but uses different function names.

### Original Phase 1 Design: Import Snapshots (READ-ONLY)

**Original command design:** `import-rekordbox`

```bash
python -m djlib.cli import-rekordbox --out snapshots/rekordbox_snapshot.csv
```

**Output CSV columns:**

- `external_source`: "rekordbox"
- `external_track_id`: Rekordbox internal ID
- `old_full_path`: Current file location in RB DB
- `artist`, `title`, `bpm`, `key`: Basic metadata
- `last_played_at`, `rating`: DJ usage data
- `snapshot_date`: Timestamp

**Implementation notes:**

- Read from `~/Library/Pioneer/rekordbox/master.db`
- Use SQLCipher for decryption
- Store in `LOGS/external_snapshots/`

#### Command: `import-traktor`

```bash
python -m djlib.cli import-traktor --collection ~/Music/Traktor/collection.nml --out snapshots/traktor_snapshot.csv
```

**Output CSV columns:**

- `external_source`: "traktor"
- `external_track_id`: Traktor UID (from PRIMARYKEY)
- `old_full_path`: FILE/@DIR + FILE/@FILE
- `artist`, `title`, `bpm`, `key`: From INFO tags
- `cue_count`: Number of cue points
- `snapshot_date`: Timestamp

**Implementation notes:**

- Parse XML: `collection.nml`
- Extract `ENTRY` nodes with `LOCATION` and `INFO`
- Store cue point count (indicates track usage)

---

### Phase 2: Path Mapping (READ-ONLY)

**Goal:** Track where files were moved during library cleaning.

#### Auto-logging in `apply`

Already partially implemented in `cmd_apply`:

```python
log_rows.append([str(src_before), str(dest_after), track_id])
```

**Enhancement:** Cross-reference with external snapshots

```python
# In apply command
for r in ready:
    old_path = r["file_path"]
    new_path = dest_path

    # Find in snapshots
    rb_id = find_in_snapshot("rekordbox", old_path)
    tr_id = find_in_snapshot("traktor", old_path)

    # Store path map
    path_map.append({
        "track_id": r["track_id"],
        "old_path": old_path,
        "new_path": new_path,
        "rekordbox_id": rb_id,
        "traktor_id": tr_id,
    })
```

**Output:** `LOGS/path_maps/path_map_TIMESTAMP.csv`

---

### Phase 3: Path Sync (WRITE - EXPLICIT OPT-IN)

**Goal:** Update Rekordbox/Traktor to point to new library locations.

⚠️ **DANGER ZONE:** These commands modify DJ software databases.

#### Command: `sync-rekordbox-paths`

```bash
# Dry-run (default)
python -m djlib.cli sync-rekordbox-paths --path-map LOGS/path_maps/path_map_20251126.csv

# Actual sync (requires explicit flag + confirmation)
python -m djlib.cli sync-rekordbox-paths --path-map LOGS/path_maps/path_map_20251126.csv --write
```

**Safety features:**

1. **Automatic backup:**

   ```bash
   cp ~/Library/Pioneer/rekordbox/master.db \
      ~/Library/Pioneer/rekordbox/master.db.backup_TIMESTAMP
   ```

2. **Dry-run report:**

   ```
   Would update 42 paths:
   ✓ Track ID 12345: /old/path.flac → /new/LIBRARY/Artist/file.flac
   ✓ Track ID 12346: /old/path2.mp3 → /new/LIBRARY/Artist2/file2.mp3
   ✗ Track ID 12347: NOT FOUND in Rekordbox DB (skip)
   ```

3. **Interactive confirmation:**

   ```
   Backup created: master.db.backup_20251126_143022

   Ready to update 42 paths in Rekordbox database.
   This operation will modify ~/Library/Pioneer/rekordbox/master.db

   Type 'YES' to continue: _
   ```

4. **Rollback on error:**
   - If any UPDATE fails, restore from backup
   - Atomic transaction (all or nothing)

**Implementation:**

```python
def sync_rekordbox_paths(path_map_file: Path, write: bool = False):
    # Load path map
    path_map = load_path_map(path_map_file)

    # Backup DB
    if write:
        backup_path = backup_rekordbox_db()
        print(f"Backup created: {backup_path}")

    # Open DB (SQLCipher)
    conn = open_rekordbox_db()

    # Dry-run: show changes
    for mapping in path_map:
        old_path = mapping["old_path"]
        new_path = mapping["new_path"]
        rb_id = mapping.get("rekordbox_id")

        if not rb_id:
            print(f"✗ {old_path}: NOT FOUND in snapshot")
            continue

        print(f"✓ Track ID {rb_id}: {old_path} → {new_path}")

    if not write:
        print("\n[DRY-RUN] Use --write to apply changes")
        return

    # Confirmation
    confirm = input("Type 'YES' to continue: ")
    if confirm != "YES":
        print("Aborted")
        return

    # Apply updates (transaction)
    try:
        conn.execute("BEGIN")
        for mapping in path_map:
            conn.execute(
                "UPDATE djmdContent SET FolderPath = ?, Title = ? WHERE ID = ?",
                (new_path.parent, new_path.name, rb_id)
            )
        conn.execute("COMMIT")
        print(f"✓ Updated {len(path_map)} paths")
    except Exception as e:
        conn.execute("ROLLBACK")
        restore_backup(backup_path)
        print(f"✗ Error: {e}")
        print(f"Restored from backup: {backup_path}")
```

#### Command: `sync-traktor-paths`

```bash
# Dry-run
python -m djlib.cli sync-traktor-paths \
    --collection ~/Music/Traktor/collection.nml \
    --path-map LOGS/path_maps/path_map_20251126.csv

# Actual sync
python -m djlib.cli sync-traktor-paths \
    --collection ~/Music/Traktor/collection.nml \
    --path-map LOGS/path_maps/path_map_20251126.csv \
    --write
```

**Safety features:**

1. **Backup collection.nml**
2. **XML parsing/writing** (preserve all other data)
3. **Only update `<LOCATION>` elements** for matched tracks

**Implementation:**

```python
def sync_traktor_paths(collection_file: Path, path_map_file: Path, write: bool = False):
    # Backup collection.nml
    if write:
        backup_path = collection_file.parent / f"collection.nml.backup_{timestamp}"
        shutil.copy(collection_file, backup_path)

    # Parse XML
    tree = ET.parse(collection_file)
    root = tree.getroot()

    # Load path map
    path_map = load_path_map(path_map_file)
    updated = 0

    # Update <LOCATION> elements
    for entry in root.findall(".//ENTRY"):
        location = entry.find("LOCATION")
        if location is None:
            continue

        old_dir = location.get("DIR")
        old_file = location.get("FILE")
        old_path = Path(old_dir) / old_file

        # Find in path map
        new_path = find_new_path(path_map, old_path)
        if not new_path:
            continue

        print(f"✓ {old_path} → {new_path}")

        if write:
            location.set("DIR", str(new_path.parent) + "/")
            location.set("FILE", new_path.name)
            updated += 1

    if not write:
        print(f"\n[DRY-RUN] Would update {updated} paths")
        return

    # Confirm
    confirm = input(f"Update {updated} paths in {collection_file}? Type 'YES': ")
    if confirm != "YES":
        print("Aborted")
        return

    # Write XML
    tree.write(collection_file, encoding="utf-8", xml_declaration=True)
    print(f"✓ Updated {updated} paths in {collection_file}")
    print(f"Backup: {backup_path}")
```

---

## Implementation Checklist

**Phase 1: Import Snapshots (SAFE)**

- [ ] `import-rekordbox` command stub
- [ ] Rekordbox DB reader (reuse existing SQLCipher code)
- [ ] CSV export format
- [ ] `import-traktor` command stub
- [ ] Traktor NML parser (use `xml.etree`)
- [ ] CSV export format
- [ ] Tests: snapshot integrity

**Phase 2: Path Mapping (SAFE)**

- [ ] Enhance `apply` to cross-reference snapshots
- [ ] `path_map` CSV format
- [ ] Helper: `find_in_snapshot(source, old_path)`
- [ ] Tests: path mapping logic

**Phase 3: Path Sync (DANGEROUS)**

- [ ] `sync-rekordbox-paths` command stub
- [ ] Backup/restore logic
- [ ] Dry-run report formatting
- [ ] Interactive confirmation
- [ ] Transaction-based updates
- [ ] Error handling + rollback
- [ ] `sync-traktor-paths` command stub
- [ ] XML preservation (don't corrupt structure)
- [ ] Tests: backup/restore, dry-run vs write

---

## Safety Checklist (for future implementation)

Before implementing Phase 3:

- [ ] Multiple developers review code
- [ ] Test on isolated Rekordbox/Traktor instances
- [ ] Document recovery procedures
- [ ] Add `--backup-only` mode for testing
- [ ] Implement checksum verification for backups
- [ ] Add `--force` flag (requires `--write` + confirmation)
- [ ] Log all DB operations to `LOGS/sync_operations.log`
- [ ] Add rollback command: `undo-sync-rekordbox`

---

## References

- `djlib/rekordbox_status.py` - Current read-only integration
- [Rekordbox DB structure](https://github.com/dylanljones/pyrekordbox) - Reference implementation
- [Traktor NML format](https://github.com/wolkenarchitekt/traktor-nml-utils) - XML schema docs
