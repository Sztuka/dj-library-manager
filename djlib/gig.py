"""Gig preparation helpers — Phase 2.

Slice 1 (dry-run): parse_m3u, resolve_tracks, validate_gig_prep
Slice 2 (copy):    GigDir, PrepState, copy_track_atomic, run_gig_prep_copy

Three-phase copy protocol
--------------------------
1. RESERVE  (short csv_lock): set live_location="gig:<id>:preparing" for all tracks
2. COPY     (no lock):        src → dest.partial → fsync → rename; SHA-256 verify;
                               append events to prep.state
3. COMMIT   (short csv_lock): set live_location="gig:<id>", live_path; write manifest.json

The intermediate "preparing" state means apply_gig_track_guard will protect
the tracks even if the process is killed mid-copy — sync won't overwrite them.

Resume: replay prep.state → derive last state per track → skip already-verified.
"""
from __future__ import annotations

import csv as csv_mod
import fcntl
import hashlib
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger(__name__)


# ── M3U parser ────────────────────────────────────────────────────────────────


def parse_m3u(path: Path) -> List[str]:
    """Return file paths found in an M3U or M3U8 playlist.

    Skips blank lines, #EXTM3U headers, and #EXTINF metadata lines.
    Handles both absolute and relative paths (relative resolved against
    the playlist's parent directory).
    """
    playlist_dir = path.parent
    paths: List[str] = []
    with path.open(encoding="utf-8-sig", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if os.path.isabs(line):
                paths.append(line)
            else:
                paths.append(str((playlist_dir / line).resolve()))
    return paths


# ── Track resolver ────────────────────────────────────────────────────────────


@dataclass
class ResolveResult:
    playlist_path: str
    track_id: Optional[str]
    library_row: Optional[Dict[str, str]]
    match_type: str  # "exact" | "filename" | "not_found"


def resolve_tracks(
    paths: List[str],
    library_rows: List[Dict[str, str]],
) -> List[ResolveResult]:
    """Map playlist file paths to library.csv rows.

    Match strategy (in order):
    1. Exact match on old_full_path
    2. Filename-only match (basename) — fallback for path drift,
       skipped when ambiguous (multiple rows share the same filename)
    """
    by_full: Dict[str, Dict[str, str]] = {}
    by_name: Dict[str, List[Dict[str, str]]] = {}
    for row in library_rows:
        fp = row.get("old_full_path", "")
        if fp:
            by_full[fp] = row
        name = Path(fp).name if fp else ""
        if name:
            by_name.setdefault(name, []).append(row)

    results: List[ResolveResult] = []
    for p in paths:
        if p in by_full:
            row = by_full[p]
            results.append(ResolveResult(
                playlist_path=p,
                track_id=row.get("track_id") or None,
                library_row=row,
                match_type="exact",
            ))
        else:
            name = Path(p).name
            candidates = by_name.get(name, [])
            if len(candidates) == 1:
                row = candidates[0]
                results.append(ResolveResult(
                    playlist_path=p,
                    track_id=row.get("track_id") or None,
                    library_row=row,
                    match_type="filename",
                ))
            else:
                results.append(ResolveResult(
                    playlist_path=p,
                    track_id=None,
                    library_row=None,
                    match_type="not_found",
                ))
    return results


# ── Validator ─────────────────────────────────────────────────────────────────


@dataclass
class ValidationError:
    kind: str   # "NOT_IN_LIBRARY" | "ON_ANOTHER_GIG" | "FILE_MISSING"
    path: str
    detail: str = ""


def validate_gig_prep(
    gig_id: str,
    resolved: List[ResolveResult],
    check_files_exist: bool = True,
) -> List[ValidationError]:
    """Collect all pre-flight errors. Never raises — returns full list so the
    DJ sees every problem before fixing anything."""
    errors: List[ValidationError] = []
    for r in resolved:
        if r.match_type == "not_found" or r.track_id is None:
            errors.append(ValidationError(
                kind="NOT_IN_LIBRARY",
                path=r.playlist_path,
            ))
            continue

        live_loc = (r.library_row or {}).get("live_location", "") or ""
        safe_locs = ("nas", f"gig:{gig_id}", f"gig:{gig_id}:preparing")
        if live_loc and live_loc not in safe_locs:
            errors.append(ValidationError(
                kind="ON_ANOTHER_GIG",
                path=r.playlist_path,
                detail=live_loc,
            ))

        if check_files_exist and not Path(r.playlist_path).exists():
            errors.append(ValidationError(
                kind="FILE_MISSING",
                path=r.playlist_path,
            ))

    return errors


# ── Size estimator ────────────────────────────────────────────────────────────


def estimate_total_bytes(resolved: List[ResolveResult]) -> int:
    """Sum file sizes for resolved tracks that exist on disk."""
    total = 0
    for r in resolved:
        if r.track_id and Path(r.playlist_path).exists():
            try:
                total += Path(r.playlist_path).stat().st_size
            except OSError:
                pass
    return total


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n //= 1024
    return f"{n:.0f} TB"


# ── GigDir ────────────────────────────────────────────────────────────────────


def _default_gig_root() -> Path:
    try:
        from djlib.config import load_config
        cfg = load_config()
        if cfg.get("GIG_ROOT"):
            return Path(cfg["GIG_ROOT"]).expanduser()
    except Exception:
        pass
    return Path.home() / "Gigs"


@dataclass
class GigDir:
    """Path manager for ~/Gigs/<gig_id>/."""
    gig_id: str
    root: Path = field(default_factory=_default_gig_root)

    @property
    def path(self) -> Path:
        return self.root / self.gig_id

    @property
    def audio_dir(self) -> Path:
        return self.path / "audio"

    @property
    def prep_state_path(self) -> Path:
        return self.path / "prep.state"

    @property
    def manifest_path(self) -> Path:
        return self.path / "manifest.json"

    @property
    def lock_path(self) -> Path:
        return self.path / ".prep.lock"

    @property
    def gig_csv_path(self) -> Path:
        return self.path / "gig.csv"

    @property
    def merge_state_path(self) -> Path:
        return self.path / "merge.state"

    def ensure(self) -> None:
        self.audio_dir.mkdir(parents=True, exist_ok=True)

    def audio_dest(self, track_id: str, src_path: str) -> Path:
        """Destination path: audio/<track_id><original_extension>."""
        ext = Path(src_path).suffix
        return self.audio_dir / f"{track_id}{ext}"


# ── PrepState (JSON Lines WAL) ────────────────────────────────────────────────


# Event kinds written to prep.state
PREP_COPY_START = "copy_start"
PREP_VERIFIED   = "verified"
PREP_COMMITTED  = "committed"
PREP_FAILED     = "failed"


class PrepState:
    """Append-only JSON Lines write-ahead log for crash-safe gig prep.

    Each line: {"track_id": "...", "event": "...", "ts": "...", ...extra}
    Crash recovery: replay all events → take last event per track_id.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def append_event(self, track_id: str, event: str, **kwargs: object) -> None:
        entry = {
            "track_id": track_id,
            "event": event,
            "ts": datetime.now(timezone.utc).isoformat(),
            **kwargs,
        }
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def get_track_states(self) -> Dict[str, str]:
        """Return {track_id: last_event} for all tracks in the log."""
        states: Dict[str, str] = {}
        if not self._path.exists():
            return states
        with self._path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    tid = entry.get("track_id", "")
                    evt = entry.get("event", "")
                    if tid and evt:
                        states[tid] = evt
                except json.JSONDecodeError:
                    pass
        return states


# ── Atomic file copy with SHA-256 ─────────────────────────────────────────────


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_track_atomic(src: Path, dest: Path) -> str:
    """Copy src → dest atomically. Returns sha256 of the copied file.

    Protocol: hash src → copy to dest.partial → fsync → rename → verify.
    Cross-checks src and dest hashes to catch bit flips during transfer.
    Raises OSError or ValueError on failure. The .partial file is left on
    disk if we crash mid-copy so callers can detect and clean it on resume.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".partial")

    src_sha = _sha256_file(src)

    with src.open("rb") as fsrc, partial.open("wb") as fdest:
        shutil.copyfileobj(fsrc, fdest)
        fdest.flush()
        os.fsync(fdest.fileno())

    partial.rename(dest)
    dest_sha = _sha256_file(dest)

    if src_sha != dest_sha:
        dest.unlink(missing_ok=True)
        raise ValueError(
            f"SHA-256 mismatch after copy: src={src_sha[:12]}… dest={dest_sha[:12]}…"
        )
    return dest_sha


# ── Single-instance lock ──────────────────────────────────────────────────────


class GigPrepLock:
    """flock-based guard: only one gig-prep process per gig_id at a time."""

    def __init__(self, lock_path: Path) -> None:
        self._path = lock_path
        self._fd: Optional[int] = None

    def acquire(self) -> bool:
        """Try to acquire the lock. Returns False if already held."""
        self._fd = os.open(str(self._path), os.O_CREAT | os.O_WRONLY)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            os.close(self._fd)
            self._fd = None
            return False

    def release(self) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


# ── Three-phase orchestrator ──────────────────────────────────────────────────


@dataclass
class GigPrepResult:
    copied: int = 0
    skipped: int = 0       # already verified (resume)
    failed: int = 0
    committed: int = 0


def run_gig_prep_copy(
    gig_id: str,
    resolved: List[ResolveResult],
    csv_path: Path,
    gig_dir: GigDir,
    resume: bool = False,
    source_playlist: str = "",
) -> GigPrepResult:
    """Orchestrate the three-phase copy protocol.

    Phase 1 — RESERVE (short csv_lock):
        Set live_location="gig:<gig_id>:preparing" for all tracks.

    Phase 2 — COPY (no lock):
        For each track: copy_track_atomic, verify SHA-256, append prep.state.

    Phase 3 — COMMIT (short csv_lock):
        Set live_location="gig:<gig_id>", live_path=<local>; write manifest.json.
    """
    from djlib.library_schema import load_library_csv, save_library_csv
    from djlib.locks import csv_lock

    gig_dir.ensure()

    proc_lock = GigPrepLock(gig_dir.lock_path)
    if not proc_lock.acquire():
        raise RuntimeError(
            f"Another gig-prep is already running for '{gig_id}'. "
            f"Lock: {gig_dir.lock_path}"
        )

    try:
        return _run_gig_prep_copy_locked(
            gig_id=gig_id,
            resolved=resolved,
            csv_path=csv_path,
            gig_dir=gig_dir,
            resume=resume,
            source_playlist=source_playlist,
            load_library_csv=load_library_csv,
            save_library_csv=save_library_csv,
            csv_lock=csv_lock,
        )
    finally:
        proc_lock.release()


def _run_gig_prep_copy_locked(
    gig_id: str,
    resolved: List[ResolveResult],
    csv_path: Path,
    gig_dir: GigDir,
    resume: bool,
    source_playlist: str,
    load_library_csv,
    save_library_csv,
    csv_lock,
) -> "GigPrepResult":
    """Inner implementation — called only when GigPrepLock is held."""
    prep = PrepState(gig_dir.prep_state_path)
    result = GigPrepResult()

    # Only copy tracks that resolved successfully
    to_copy = [r for r in resolved if r.track_id]

    # Resume: skip tracks already verified or committed
    already_done: set = set()
    if resume:
        states = prep.get_track_states()
        already_done = {
            tid for tid, evt in states.items()
            if evt in (PREP_VERIFIED, PREP_COMMITTED)
        }
        result.skipped = len(already_done)

    # Clean up stale .partial files from a previous interrupted run
    if resume:
        for r in to_copy:
            if r.track_id in already_done:
                continue
            dest = gig_dir.audio_dest(r.track_id, r.playlist_path)
            partial = dest.with_suffix(dest.suffix + ".partial")
            if partial.exists():
                partial.unlink()

    # ── Phase 1: RESERVE ────────────────────────────────────────────────────
    reserving_loc = f"gig:{gig_id}:preparing"
    with csv_lock(csv_path):
        rows = load_library_csv(csv_path)
        by_tid = {str(r.get("track_id", "")): r for r in rows}
        for res in to_copy:
            tid = res.track_id
            if tid in already_done:
                continue
            if tid in by_tid:
                by_tid[tid]["live_location"] = reserving_loc
        save_library_csv(csv_path, rows)

    # ── Phase 2: COPY ───────────────────────────────────────────────────────
    verified_tracks: List[Dict[str, str]] = []  # {track_id, local_path, sha256}
    for res in to_copy:
        tid = res.track_id
        if tid in already_done:
            continue
        src = Path(res.playlist_path)
        dest = gig_dir.audio_dest(tid, res.playlist_path)

        prep.append_event(tid, PREP_COPY_START, src=str(src), dest=str(dest))
        try:
            sha = copy_track_atomic(src, dest)
            prep.append_event(tid, PREP_VERIFIED, sha256=sha, dest=str(dest))
            verified_tracks.append({
                "track_id": tid,
                "local_path": str(dest),
                "sha256": sha,
                "src": str(src),
            })
            result.copied += 1
        except Exception as exc:
            prep.append_event(tid, PREP_FAILED, error=str(exc))
            result.failed += 1
            print(f"  ERROR copying {src.name}: {exc}")

    # ── Phase 3: COMMIT ─────────────────────────────────────────────────────
    committed_loc = f"gig:{gig_id}"
    committed_at = datetime.now(timezone.utc).isoformat()

    with csv_lock(csv_path):
        rows = load_library_csv(csv_path)
        by_tid = {str(r.get("track_id", "")): r for r in rows}
        for vt in verified_tracks:
            tid = vt["track_id"]
            if tid in by_tid:
                by_tid[tid]["live_location"] = committed_loc
                by_tid[tid]["live_path"] = vt["local_path"]
        save_library_csv(csv_path, rows)

    for vt in verified_tracks:
        prep.append_event(vt["track_id"], PREP_COMMITTED)
        result.committed += 1

    # Write manifest.json
    from djlib.library_schema import LIBRARY_FIELDNAMES
    all_states = prep.get_track_states()
    manifest = {
        "gig_id": gig_id,
        "schema_version": list(LIBRARY_FIELDNAMES),
        "created_at": committed_at,
        "source": source_playlist,
        "tracks": [
            {
                "track_id": vt["track_id"],
                "src_path": vt["src"],
                "local_path": vt["local_path"],
                "sha256": vt["sha256"],
                "status": all_states.get(vt["track_id"], PREP_COMMITTED),
            }
            for vt in verified_tracks
        ],
    }
    with gig_dir.manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # Write gig.csv — frozen snapshot of each track's library row at COMMIT time.
    # Phase 3 gig-merge uses this as the LWW baseline: fields changed in Rekordbox
    # since this snapshot will be detected and merged back to library.csv.
    # Reuses by_tid from the csv_lock block above — already post-COMMIT state,
    # no second lock needed and no race window with concurrent sync-dj-libraries.
    gig_csv_rows = [
        by_tid[vt["track_id"]]
        for vt in verified_tracks
        if vt["track_id"] in by_tid
    ]
    with gig_dir.gig_csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv_mod.DictWriter(
            f, fieldnames=list(LIBRARY_FIELDNAMES), extrasaction="ignore"
        )
        writer.writeheader()
        for row in gig_csv_rows:
            # Use "" for None but preserve falsy non-None values (0, "0", False)
            writer.writerow(
                {k: ("" if row.get(k) is None else row.get(k, "")) for k in LIBRARY_FIELDNAMES}
            )

    # Write rekordbox.xml — enrich verified tracks with library metadata
    rb_tracks = []
    for vt in verified_tracks:
        row = by_tid.get(vt["track_id"], {})
        rb_tracks.append({**row, "local_path": vt["local_path"]})

    if rb_tracks:
        try:
            from djlib.rekordbox_xml import write_rekordbox_xml
            xml_path = gig_dir.path / "rekordbox.xml"
            write_rekordbox_xml(rb_tracks, gig_id, xml_path)
        except Exception as exc:
            print(f"  WARNING: could not write rekordbox.xml: {exc}")

    return result


# ── Gig-merge WAL ─────────────────────────────────────────────────────────────


MERGE_FILE_COPIED   = "file_copied_to_nas"
MERGE_ROW_MERGED    = "row_merged"
MERGE_NAS_MISSING   = "nas_path_missing"
MERGE_SHA_MISMATCH  = "sha_mismatch"
MERGE_SKIPPED       = "skipped_already_merged"


class MergeState:
    """Append-only JSON Lines WAL for crash-safe gig-merge (mirrors PrepState)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def append_event(self, track_id: str, event: str, **kwargs: object) -> None:
        entry = {
            "track_id": track_id,
            "event": event,
            "ts": datetime.now(timezone.utc).isoformat(),
            **kwargs,
        }
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def get_track_states(self) -> Dict[str, str]:
        states: Dict[str, str] = {}
        if not self._path.exists():
            return states
        with self._path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    tid = entry.get("track_id", "")
                    evt = entry.get("event", "")
                    if tid and evt:
                        states[tid] = evt
                except json.JSONDecodeError:
                    pass
        return states


# ── Gig-merge result ──────────────────────────────────────────────────────────


@dataclass
class GigMergeResult:
    merged: int = 0
    skipped_already_merged: int = 0
    failed_sha: int = 0
    failed_nas_missing: int = 0
    quarantined: int = 0
    conflicts: int = 0


# ── Gig-merge orchestrator ────────────────────────────────────────────────────


def run_gig_merge(
    gig_id: str,
    csv_path: Path,
    gig_dir: Optional[GigDir] = None,
    resume: bool = False,
    dry_run: bool = False,
    create_missing_dirs: bool = False,
    quarantine_root: Optional[Path] = None,
) -> GigMergeResult:
    """Merge post-gig Rekordbox state back to library.csv and copy files to NAS.

    Sequence:
      1. Load gig.csv (baseline) + manifest.json
      2. Fetch Rekordbox DB for fresh field values
      3. Backup library.csv
      4. Per track: SHA-verify MacBook file, copy to NAS, LWW merge
      5. Quarantine unknown files in audio/ dir
      6. csv_lock: write resolved rows, reset live_location="nas"
      7. Write audit log to LOGS/

    Args:
        gig_id:              gig identifier, must match a directory in GIG_ROOT
        csv_path:            path to library.csv
        gig_dir:             GigDir instance (defaults to standard location)
        resume:              skip tracks already recorded as row_merged in WAL
        dry_run:             print plan only, no writes
        create_missing_dirs: mkdir -p NAS parent dir if missing (default: abort)
    """
    from djlib.library_schema import LIBRARY_FIELDNAMES, load_library_csv, save_library_csv
    from djlib.locks import csv_lock

    if gig_dir is None:
        gig_dir = GigDir(gig_id=gig_id)

    if not gig_dir.gig_csv_path.exists():
        raise FileNotFoundError(
            f"gig.csv not found at {gig_dir.gig_csv_path} — "
            "was gig-prep run for this gig_id?"
        )
    if not gig_dir.manifest_path.exists():
        raise FileNotFoundError(f"manifest.json not found at {gig_dir.manifest_path}")

    # Load baseline snapshot
    import csv as csv_mod
    with gig_dir.gig_csv_path.open(encoding="utf-8") as f:
        gig_rows = list(csv_mod.DictReader(f))

    with gig_dir.manifest_path.open(encoding="utf-8") as f:
        manifest = json.load(f)
    manifest_by_tid: Dict[str, Dict] = {
        t["track_id"]: t for t in manifest.get("tracks", [])
    }

    result = GigMergeResult()
    merge_state = MergeState(gig_dir.path / "merge.state")

    if dry_run:
        print(f"[dry-run] gig-merge {gig_id}: {len(gig_rows)} tracks in gig.csv")
        for row in gig_rows:
            tid = row.get("track_id", "")
            src = manifest_by_tid.get(tid, {}).get("local_path", "?")
            dest = row.get("old_full_path", "?")
            print(f"  {tid[:8]}…  {Path(src).name} → {dest}")
        return result

    # Fetch fresh data from Rekordbox
    fresh_by_tid: Dict[str, Dict[str, str]] = {}
    try:
        from djlib.rekordbox_reader import fetch_gig_tracks
        fresh_by_tid = fetch_gig_tracks(gig_rows, db_path=None)
    except FileNotFoundError as exc:
        print(f"  WARNING: Rekordbox DB not found — merge will use baseline values: {exc}")
    except Exception as exc:
        print(f"  WARNING: Could not read Rekordbox DB: {exc}")

    merged_at = datetime.now(timezone.utc).isoformat()
    ts_tag = merged_at.replace(":", "-").replace("+", "Z")[:19]
    logs_dir = csv_path.parent.parent / "LOGS"

    # Warn if an interrupted merge exists but --resume was not passed
    if not resume and merge_state._path.exists():
        log.warning(
            "merge.state exists for gig %s but --resume was not passed — "
            "existing WAL is ignored and tracks may be re-processed. "
            "Pass --resume to continue from where the last run stopped.",
            gig_id,
        )

    # Replay WAL for resume
    prior_states = merge_state.get_track_states() if resume else {}

    # Load current library for LWW live side; backup inside lock for consistency
    backup_path = logs_dir / f"library-backup-{gig_id}-{ts_tag}.csv"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_lock(csv_path):
        shutil.copy2(csv_path, backup_path)
        current_rows = load_library_csv(csv_path)
    live_by_tid: Dict[str, Dict[str, str]] = {
        str(r.get("track_id", "")): r for r in current_rows
    }

    audit_entries = []

    # ── Per-track: copy-back then LWW ────────────────────────────────────────
    from djlib.lww_merge import merge_track

    resolved_rows_by_tid: Dict[str, Dict[str, str]] = {}

    for row in gig_rows:
        tid = str(row.get("track_id", "") or "")
        if not tid:
            continue

        live_row = live_by_tid.get(tid, {})
        live_loc = str(live_row.get("live_location", "") or "")

        # Idempotent: already merged in a previous run
        if live_loc == "nas":
            merge_state.append_event(tid, MERGE_SKIPPED)
            result.skipped_already_merged += 1
            continue
        if live_loc == "":
            log.warning("track_id=%s has empty live_location — skipping (already on NAS?)", tid)
            result.skipped_already_merged += 1
            continue

        # Resume: skip copy + LWW if already fully merged in WAL
        prior = prior_states.get(tid, "")
        if prior == MERGE_ROW_MERGED:
            result.skipped_already_merged += 1
            continue

        mf = manifest_by_tid.get(tid, {})
        macbook_path = Path(str(mf.get("local_path", "") or ""))
        nas_path     = Path(str(row.get("old_full_path", "") or ""))
        manifest_sha = str(mf.get("sha256", "") or "")

        # ── Step 1: copy MacBook → NAS (unless resume already did it) ────────
        if prior != MERGE_FILE_COPIED:
            if not macbook_path.exists():
                print(f"  ERROR {tid[:8]}…: MacBook file missing: {macbook_path}")
                merge_state.append_event(tid, MERGE_SHA_MISMATCH,
                                         reason="macbook_file_missing")
                result.failed_sha += 1
                continue

            # Verify MacBook file hasn't been corrupted since prep
            if manifest_sha:
                actual_sha = _sha256_file(macbook_path)
                if actual_sha != manifest_sha:
                    print(f"  ERROR {tid[:8]}…: SHA mismatch (MacBook bit-rot?)")
                    merge_state.append_event(tid, MERGE_SHA_MISMATCH,
                                             expected=manifest_sha[:12],
                                             actual=actual_sha[:12])
                    result.failed_sha += 1
                    continue

            if not nas_path.parent.exists():
                if create_missing_dirs:
                    nas_path.parent.mkdir(parents=True, exist_ok=True)
                else:
                    print(f"  ERROR {tid[:8]}…: NAS directory missing: {nas_path.parent}"
                          "  (use --create-missing-dirs to create it)")
                    merge_state.append_event(tid, MERGE_NAS_MISSING,
                                             path=str(nas_path))
                    result.failed_nas_missing += 1
                    continue

            try:
                copy_track_atomic(macbook_path, nas_path)
                merge_state.append_event(tid, MERGE_FILE_COPIED,
                                         src=str(macbook_path),
                                         dest=str(nas_path))
            except Exception as exc:
                print(f"  ERROR {tid[:8]}…: copy failed: {exc}")
                merge_state.append_event(tid, MERGE_SHA_MISMATCH, reason=str(exc))
                result.failed_sha += 1
                continue

        # ── Step 2: LWW merge ─────────────────────────────────────────────────
        fresh = fresh_by_tid.get(tid, {})
        resolved, track_audit = merge_track(
            track_id=tid,
            baseline=dict(row),
            live=live_row or dict(row),
            fresh=fresh,
        )
        resolved["live_location"] = "nas"
        resolved["live_path"]     = ""
        resolved_rows_by_tid[tid] = resolved
        audit_entries.extend(track_audit)
        result.conflicts += sum(1 for e in track_audit if e.conflict)

        merge_state.append_event(tid, MERGE_ROW_MERGED)
        result.merged += 1

    # ── Quarantine pass ───────────────────────────────────────────────────────
    if quarantine_root is None:
        quarantine_root = Path.home() / "Music Unsorted" / "quarantine"
    quarantine_dir = quarantine_root / gig_id
    gig_track_ids = {str(r.get("track_id", "")) for r in gig_rows}

    if gig_dir.audio_dir.exists():
        for audio_file in gig_dir.audio_dir.iterdir():
            if not audio_file.is_file():
                continue
            stem = audio_file.stem  # track_id is the filename stem
            if stem not in gig_track_ids:
                quarantine_dir.mkdir(parents=True, exist_ok=True)
                dest = quarantine_dir / audio_file.name
                try:
                    shutil.move(str(audio_file), dest)
                    file_sha = _sha256_file(dest)
                    print(f"  QUARANTINE: {audio_file.name} → {dest} (sha={file_sha[:12]}…)")
                    result.quarantined += 1
                except Exception as exc:
                    print(f"  WARNING: could not quarantine {audio_file.name}: {exc}")

    # ── Final csv_lock write ──────────────────────────────────────────────────
    with csv_lock(csv_path):
        rows = load_library_csv(csv_path)
        by_tid = {str(r.get("track_id", "")): r for r in rows}
        for tid, resolved in resolved_rows_by_tid.items():
            if tid in by_tid:
                by_tid[tid].update(resolved)
        save_library_csv(csv_path, rows)

    # ── Audit log ─────────────────────────────────────────────────────────────
    if audit_entries:
        audit_path = logs_dir / f"gig-merge-{gig_id}-{ts_tag}.csv"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_fields = [
            "track_id", "field_name", "baseline_value", "live_value",
            "fresh_value", "resolved_value", "source", "conflict",
        ]
        with audit_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv_mod.DictWriter(f, fieldnames=audit_fields)
            writer.writeheader()
            for e in audit_entries:
                writer.writerow({
                    "track_id":       e.track_id,
                    "field_name":     e.field_name,
                    "baseline_value": e.baseline_value,
                    "live_value":     e.live_value,
                    "fresh_value":    e.fresh_value,
                    "resolved_value": e.resolved_value,
                    "source":         e.source,
                    "conflict":       str(e.conflict),
                })

    return result


# ── Gig-cleanup ───────────────────────────────────────────────────────────────


@dataclass
class GigCleanupResult:
    deleted_files: int = 0
    not_merged: int = 0    # tracks not yet on NAS — blocked cleanup
    sha_failures: int = 0  # NAS verify failed — those files kept


def run_gig_cleanup(
    gig_id: str,
    csv_path: Path,
    gig_dir: Optional[GigDir] = None,
    verify_nas: bool = False,
    dry_run: bool = False,
) -> GigCleanupResult:
    """Remove audio/ from a fully-merged gig, freeing MacBook disk space.

    Safety guards:
      1. All tracks in gig.csv must have live_location=='nas' in library.csv.
         Any not-yet-merged track aborts the whole operation.
      2. (--verify-nas) SHA-256 the NAS copy against the manifest before
         deleting each MacBook file. Mismatches skip that file only.

    What survives: ~/Gigs/<gig_id>/ directory with manifest.json, gig.csv,
    prep.state, merge.state kept as historical record. Only audio/ is removed.
    """
    from djlib.library_schema import load_library_csv
    from djlib.locks import csv_lock

    if gig_dir is None:
        gig_dir = GigDir(gig_id=gig_id)

    if not gig_dir.gig_csv_path.exists():
        raise FileNotFoundError(
            f"gig.csv not found at {gig_dir.gig_csv_path} — "
            "was gig-prep run for this gig_id?"
        )

    with gig_dir.gig_csv_path.open(encoding="utf-8") as f:
        gig_rows = list(csv_mod.DictReader(f))

    if not gig_rows:
        log.warning("gig.csv for %s has no tracks — nothing to clean up", gig_id)
        return GigCleanupResult()

    manifest_by_tid: Dict[str, Dict] = {}
    if gig_dir.manifest_path.exists():
        with gig_dir.manifest_path.open(encoding="utf-8") as f:
            manifest = json.load(f)
        manifest_by_tid = {t["track_id"]: t for t in manifest.get("tracks", [])}

    result = GigCleanupResult()

    # ── Guard: every track must be back on NAS ────────────────────────────────
    # Use csv_lock to avoid reading a partially-written library.csv.
    with csv_lock(csv_path):
        current_rows = load_library_csv(csv_path)
    live_by_tid: Dict[str, Dict[str, str]] = {
        str(r.get("track_id", "")): r for r in current_rows
    }

    for row in gig_rows:
        tid = str(row.get("track_id", "") or "")
        if not tid:
            continue
        live_loc = str((live_by_tid.get(tid) or {}).get("live_location", "") or "")
        if live_loc != "nas":
            log.warning(
                "track_id=%s not on NAS (live_location=%r) — run gig-merge first",
                tid, live_loc,
            )
            result.not_merged += 1

    if result.not_merged:
        return result

    # ── Per-track: optionally verify NAS, then delete MacBook file ────────────
    for row in gig_rows:
        tid = str(row.get("track_id", "") or "")
        if not tid:
            continue

        mf = manifest_by_tid.get(tid, {})
        local_path_str = str(mf.get("local_path", "") or "")
        if not local_path_str:
            log.warning("track_id=%s has no local_path in manifest — skipping", tid)
            continue
        local_path = Path(local_path_str)
        nas_path     = Path(str(mf.get("src_path", "") or row.get("old_full_path", "")))
        manifest_sha = str(mf.get("sha256", "") or "")

        # Use is_file() not exists() — prevents Path("") == Path(".") footgun
        if not local_path.is_file():
            continue  # already cleaned up in a prior run

        if verify_nas:
            if not manifest_sha:
                # No sha in manifest — can't verify; skip to be safe
                log.warning(
                    "track_id=%s has no sha256 in manifest — skipping delete "
                    "(cannot verify NAS copy without a reference hash)",
                    tid,
                )
                result.sha_failures += 1
                continue
            if not nas_path.exists():
                log.warning("track_id=%s NAS file missing: %s — skipping delete", tid, nas_path)
                result.sha_failures += 1
                continue
            nas_sha = _sha256_file(nas_path)
            if nas_sha != manifest_sha:
                log.warning(
                    "track_id=%s NAS SHA mismatch (expected %s… got %s…) — skipping delete",
                    tid, manifest_sha[:12], nas_sha[:12],
                )
                result.sha_failures += 1
                continue

        if dry_run:
            print(f"  [dry-run] would delete: {local_path}")
            result.deleted_files += 1
            continue

        try:
            local_path.unlink()
            result.deleted_files += 1
        except OSError as exc:
            log.warning("could not delete %s: %s", local_path, exc)

    # Remove audio/ if now empty (skip in dry_run); tolerate non-empty or OS errors
    if not dry_run and gig_dir.audio_dir.exists():
        try:
            gig_dir.audio_dir.rmdir()  # fails silently if non-empty (e.g. .DS_Store)
        except OSError:
            pass

    return result
