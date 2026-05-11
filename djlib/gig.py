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
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


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
