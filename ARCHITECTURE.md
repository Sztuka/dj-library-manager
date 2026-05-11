# Architecture — DJ Library Manager

This document covers the gig workflow (Phases 2–4). Other subsystems (Review UI, genre classifier, ML pipeline) will be added here as they stabilise.

---

## Gig Workflow (Phases 2–4)

A "gig" is an event where the DJ plays from the MacBook rather than directly from the NAS. The workflow moves tracks onto the MacBook before the gig, protects them from concurrent sync operations during the gig, and merges any Rekordbox edits back to the master library afterward.

```
NAS (library.csv)
     │
     │  gig-prep (Phase 2)
     ▼
~/Gigs/<gig_id>/audio/      ← MacBook local copies
     │
     │  [gig happens — DJ edits cues, ratings, play counts in Rekordbox]
     │
     │  gig-merge (Phase 3)
     ▼
NAS (library.csv updated)   ← merged Rekordbox state written back
     │
     │  gig-cleanup (Phase 4)
     ▼
~/Gigs/<gig_id>/audio/ deleted  ← MacBook disk space freed
```

---

### Phase 2: gig-prep

Entry point: `cmd_gig_prep` → `run_gig_prep_copy` in `djlib/gig.py`.

Three sub-phases executed in order:

1. **RESERVE** (short `csv_lock`): set `live_location = "gig:<id>:preparing"` for all tracks. This guard fires immediately so `sync-dj-libraries` cannot overwrite these rows even if the process dies mid-copy.

2. **COPY** (no lock): for each track, `copy_track_atomic` hashes the source, copies to `<dest>.partial`, fsyncs, renames, then re-hashes to verify. Each outcome appended to `prep.state` (JSON Lines WAL).

3. **COMMIT** (short `csv_lock`): set `live_location = "gig:<id>"` and `live_path = <local>` for all successfully copied tracks. Write `manifest.json` (per-track sha256 + paths). Write `gig.csv` — a frozen snapshot of each track's library row at this exact moment. This snapshot is the LWW baseline in Phase 3.

**Resume** (`--resume`): replay `prep.state`, skip tracks whose last event is `verified` or `committed`. Stale `.partial` files from the interrupted run are deleted.

---

### Phase 3: gig-merge

Entry point: `cmd_gig_merge` → `run_gig_merge` in `djlib/gig.py`.

Sequence:

1. Load `gig.csv` (baseline) and `manifest.json`.
2. Read `Rekordbox master6.db` via `rekordbox_reader.fetch_gig_tracks` — returns post-gig values for `cue_points_rb`, `rating`, `play_count`, `bpm`. If the DB is absent or the track was removed from the collection, merge proceeds with baseline values.
3. Backup `library.csv` to `LOGS/library-backup-<gig_id>-<ts>.csv` inside a `csv_lock`.
4. Per track:
   - Skip if `live_location == "nas"` (already merged — idempotent).
   - SHA-256 verify the MacBook file against `manifest.json` (catches bit-rot since prep).
   - Copy MacBook file → NAS path using `copy_track_atomic`.
   - Run LWW merge (`lww_merge.merge_track`) — see algorithm below.
   - Set `live_location = "nas"`, `live_path = ""` in the resolved row.
   - Append `row_merged` to `merge.state` WAL.
5. Quarantine pass: any file in `audio/` whose stem is not a known `track_id` is moved to `~/Music Unsorted/quarantine/<gig_id>/`.
6. Final `csv_lock` write: bulk-update `library.csv` with all resolved rows.
7. Write audit log to `LOGS/gig-merge-<gig_id>-<ts>.csv`.

**Resume** (`--resume`): replay `merge.state`, skip tracks with `row_merged`.

**Crash safety**: if the process dies after `MERGE_FILE_COPIED` but before `MERGE_ROW_MERGED`, the NAS file is already there. On resume the copy step is skipped (WAL entry present); LWW merge runs again and the result is the same (deterministic).

---

### Phase 4: gig-cleanup

Entry point: `cmd_gig_cleanup` → `run_gig_cleanup` in `djlib/gig.py`.

Safety guards run before any deletion:

1. **All-or-nothing NAS check**: read `library.csv` (inside `csv_lock`). Every track in `gig.csv` must have `live_location == "nas"`. If any track is not yet on NAS, the entire cleanup is aborted with a non-zero exit — no files touched.
2. **Path containment**: each `local_path` from `manifest.json` is resolved and checked against `gig_dir.audio_dir`. Paths outside that directory are refused. This prevents a corrupt manifest from deleting arbitrary files.
3. **`--verify-nas`**: SHA-256 the NAS copy against `manifest.json` before deleting the MacBook file. Mismatches skip that file only (other files are still deleted).

After per-file deletion, `audio/` is removed with `rmdir` if empty (tolerates `.DS_Store` etc. — `OSError` is silently swallowed). The rest of `~/Gigs/<gig_id>/` (`manifest.json`, `gig.csv`, `prep.state`, `merge.state`) is kept as a historical record.

---

## Key Files

| File | Role |
|---|---|
| `djlib/gig.py` | Phases 2–4 orchestration, WAL classes, atomic copy, GigDir path manager |
| `djlib/rekordbox_reader.py` | Read-only fetch from `master6.db` for Phase 3 |
| `djlib/lww_merge.py` | LWW per-field merge algorithm and audit entry generation |
| `djlib/library_schema.py` | `LIBRARY_FIELDNAMES`, `save_library_csv`, `apply_gig_track_guard` |
| `djlib/locks.py` | `csv_lock` context manager (flock on library.csv) |
| `djlib/cli.py` | `cmd_gig_prep`, `cmd_gig_merge`, `cmd_gig_cleanup` and their argparse subparsers |

---

## Data Files

| File | Location | Contents |
|---|---|---|
| `library.csv` | `data/library.csv` | Master track database. `live_location` and `live_path` are djlib-owned; `sync-dj-libraries` never overwrites them. |
| `gig.csv` | `~/Gigs/<gig_id>/gig.csv` | Frozen snapshot of library rows at gig-prep COMMIT. LWW baseline for Phase 3. |
| `manifest.json` | `~/Gigs/<gig_id>/manifest.json` | Per-track: `track_id`, `src_path` (NAS), `local_path` (MacBook), `sha256`. Written at COMMIT. |
| `prep.state` | `~/Gigs/<gig_id>/prep.state` | JSON Lines WAL for Phase 2. Events: `copy_start`, `verified`, `committed`, `failed`. |
| `merge.state` | `~/Gigs/<gig_id>/merge.state` | JSON Lines WAL for Phase 3. Events: `file_copied_to_nas`, `row_merged`, `sha_mismatch`, `nas_path_missing`, `skipped_already_merged`. |
| `LOGS/library-backup-*.csv` | `LOGS/` | Per-merge backup of library.csv taken inside csv_lock before any writes. |
| `LOGS/gig-merge-*.csv` | `LOGS/` | Audit log: one row per field where at least one value differed, with baseline/live/fresh/resolved. |

---

## live_location State Machine

The `live_location` field in `library.csv` encodes where the audio file currently lives.

```
"nas"                       default — file on NAS, sync-dj-libraries may update this row
    │
    │  gig-prep RESERVE
    ▼
"gig:<id>:preparing"        copy in progress — apply_gig_track_guard blocks sync writes
    │
    │  gig-prep COMMIT
    ▼
"gig:<id>"                  file on MacBook, gig active — sync writes fully blocked
    │
    │  gig-merge (row_merged written)
    ▼
"nas"                       merge complete — sync resumes normal operation
```

`live_path` holds the MacBook-local path while `live_location` is `"gig:<id>"` and is cleared to `""` when returning to `"nas"`.

The `"preparing"` state exists so that a process killed mid-copy does not leave tracks unguarded. `apply_gig_track_guard` treats both `"gig:<id>:preparing"` and `"gig:<id>"` as active-gig states.

---

## LWW Merge Algorithm

Source: `djlib/lww_merge.py`, function `merge_track`.

Three inputs per track:

- **baseline** — `gig.csv` row (state before the gig)
- **live** — current `library.csv` row (may have changed via `sync-dj-libraries` while the gig ran)
- **fresh** — fields from `rekordbox_reader` (what the DJ actually changed during the gig)

Per field in `MERGE_FIELDS` (`cue_points_rb`, `rating`, `play_count`, `bpm`):

```
fresh != baseline  →  use fresh  (DJ changed it — wins)
live  != baseline  →  use live   (sync changed it while DJ was playing)
else               →  no-op      (unchanged everywhere)
```

Conflict (`fresh != baseline AND live != baseline AND fresh != live`): fresh wins. The rationale is that live gig edits are more intentional than automated sync updates. Conflicts are flagged in the audit log but do not abort the merge.

Fields not in `MERGE_FIELDS` are taken from `live` unchanged. Fields absent from `fresh` (Rekordbox returned nothing for that field) are treated as "no change from DJ side" and skipped — `live` value is kept.

Known limitation: `play_count` uses LWW rather than MAX. If sync incremented the count while the DJ also played the track, the lower count wins when `fresh < live`. The alternative (MAX) would overcount after a rollback. LWW is the simpler invariant.

---

## Safety Invariants

These must hold at all times. If any is violated, stop and investigate before writing to library.csv.

1. **A track with `live_location != "nas"` must never be updated by `sync-dj-libraries`.** Enforced by `apply_gig_track_guard` in `library_schema.py`.

2. **`gig.csv` is immutable after gig-prep COMMIT.** Phase 3 reads it as the baseline. Writing to it after the gig would corrupt the LWW delta.

3. **`copy_track_atomic` verifies SHA-256 before and after copy.** A mismatch raises immediately — no silent corruption.

4. **gig-cleanup refuses to run if any track is not on NAS.** The all-or-nothing guard prevents partial cleanup that would leave the library in an ambiguous state.

5. **`local_path` in manifest must resolve inside `audio_dir`.** Path containment check in cleanup prevents a corrupt manifest from deleting files outside the gig directory.

6. **WAL events are fsynced on every append.** Both `PrepState` and `MergeState` call `os.fsync` after each write. A crash cannot leave a partial event line that silently corrupts state on resume.
