"""Read-only index of the original (pre-pipeline) NAS library.

`data/source_index.csv` is a separate, standalone CSV — never mixed with
`library.csv`. It records what sits on `ORIGINAL_ROOT/BEFORE` and
`ORIGINAL_ROOT/AFTER` on the NAS. This module never writes to `ORIGINAL_ROOT`;
it only reads file metadata (stat + tag headers) and maintains the local CSV.

Two entry points, both resumable and checkpointed because the NAS is a
network volume that can flake mid-scan:

- `scan_area()` — walks `<root>/<AREA>` and records size/mtime/tags per file.
- `fingerprint_area()` — computes acoustic fingerprints for rows that don't
  have one yet (or whose file changed since it was last fingerprinted).

Network-failure handling (see cli.py `original-scan` docstring for the
rationale): a per-file `OSError` is classified by `errno` — `ENOENT` means
the file is genuinely gone (`state="missing"`), anything else (`EIO`,
`ETIMEDOUT`, `ESTALE`, `EACCES`, ...) means the NAS glitched
(`state="error"`), and an `OSError` raised while *enumerating* directories
aborts the whole scan without marking anything missing.
"""
from __future__ import annotations

import csv
import errno
import os
import shutil
import subprocess
import tempfile
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from mutagen import File as MutFile  # type: ignore

from djlib.config import AUDIO_EXTS
from djlib.fingerprint import fingerprint_info
from djlib.locks import csv_lock

SOURCE_INDEX_FIELDNAMES: List[str] = [
    "source_id",
    "area",
    "rel_path",
    "folder",
    "filename",
    "size_bytes",
    "mtime",
    "tag_artist",
    "tag_title",
    "tag_genre",
    "duration_seconds",
    "audio_quality",
    "fingerprint",
    "fp_status",
    "dup_of",
    "in_library",
    "state",
    "batch_id",
    "first_seen",
    "last_seen",
    "notes",
]

# Namespace for source_id UUID5s — kept distinct from track_id's namespace so
# the two identifier spaces can never collide even for the same file.
_SOURCE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://dj-library-manager.local/source-index")

_RECYCLE_DIR = "#recycle"


def normalize_rel_path(p: str) -> str:
    """Canonical NFC normalization — the one place in this module that does it.

    Used for `rel_path`, `folder`, `filename`, and the `source_id` key so a
    file always resolves to the same row regardless of which normal form the
    OS/filesystem handed us the path in.
    """
    return unicodedata.normalize("NFC", p)


def make_source_id(area: str, rel_path: str) -> str:
    """Stable UUID5 identity for a (area, rel_path) pair, NFC-normalized first."""
    key = f"{area}/{normalize_rel_path(rel_path)}"
    return str(uuid.uuid5(_SOURCE_NAMESPACE, key))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _mtime_to_iso(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, tz=timezone.utc).replace(microsecond=0).isoformat()


def _blank_row(source_id: str, area: str, rel_path: str) -> Dict[str, str]:
    row = {k: "" for k in SOURCE_INDEX_FIELDNAMES}
    row["source_id"] = source_id
    row["area"] = area
    row["rel_path"] = rel_path
    return row


# ── CSV I/O ──────────────────────────────────────────────────────────────

def load_source_index(path: Path) -> List[Dict[str, str]]:
    """Read source_index.csv. Returns [] if absent."""
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_source_index(path: Path, rows: List[Dict[str, str]]) -> None:
    """Atomically write source_index.csv (tmp + fsync + os.replace, under csv_lock).

    No backup rotation — unlike library.csv, this file is fully reconstructable
    from a re-scan, so a rolling backup would just be dead weight.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with csv_lock(path):
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=SOURCE_INDEX_FIELDNAMES, extrasaction="ignore")
                writer.writeheader()
                for row in rows:
                    clean = {k: ("" if row.get(k) is None else row.get(k, "")) for k in SOURCE_INDEX_FIELDNAMES}
                    writer.writerow(clean)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise


# ── Tag reading (headers only — no audio decode, no hashing, no fpcalc) ───

def _read_raw_tags(path: Path) -> Dict[str, str]:
    """Read artist/title/genre + container-level duration/quality. Never raises."""
    try:
        f = MutFile(str(path), easy=True)
    except Exception:
        return {}
    if f is None:
        return {}

    tags = getattr(f, "tags", {}) or {}

    def first(key: str) -> str:
        v = tags.get(key)
        if not v:
            return ""
        if isinstance(v, (list, tuple)):
            return str(v[0]) if v else ""
        return str(v)

    info = getattr(f, "info", None)
    duration = round(getattr(info, "length", 0.0)) if info else 0
    ext = path.suffix.lower()
    lossless = {".flac", ".wav", ".aiff", ".aif"}
    if ext in lossless:
        fmt = ext.lstrip(".").upper()
        audio_quality = "AIFF" if fmt == "AIF" else fmt
    else:
        bitrate = round(getattr(info, "bitrate", 0) / 1000) if info else 0
        fmt = {"m4a": "AAC", "aac": "AAC"}.get(ext.lstrip("."), ext.lstrip(".").upper())
        audio_quality = f"{fmt} {bitrate}" if bitrate else fmt

    return {
        "tag_artist": first("artist").strip(),
        "tag_title": first("title").strip(),
        "tag_genre": first("genre").strip(),
        "duration_seconds": str(duration) if duration else "",
        "audio_quality": audio_quality,
    }


# ── Directory walk ──────────────────────────────────────────────────────

def _walk_onerror(exc: OSError) -> None:
    # ENOENT (dir vanished between listing and stat) is expected on a live
    # NAS mid-walk — skip it. Anything else (EIO, ESTALE, ...) means the
    # volume is misbehaving; propagate so the caller aborts the whole scan.
    if getattr(exc, "errno", None) != errno.ENOENT:
        raise exc


def iter_audio_files(area_root: Path):
    """Yield audio files under `area_root`, skipping `#recycle/` and dotfiles.

    Raises `OSError` if directory enumeration hits a non-ENOENT error (see
    module docstring). Sorted for deterministic, resumable checkpoint order.
    """
    for dirpath, dirnames, filenames in os.walk(area_root, onerror=_walk_onerror):
        dirnames[:] = sorted(d for d in dirnames if d != _RECYCLE_DIR and not d.startswith("."))
        for fname in sorted(filenames):
            if fname.startswith("."):
                continue
            p = Path(dirpath) / fname
            if p.suffix.lower() in AUDIO_EXTS:
                yield p


# ── Scan ─────────────────────────────────────────────────────────────────

def _build_row(
    path: Path, area: str, area_root: Path, by_id: Dict[str, Dict[str, str]]
) -> tuple[Dict[str, str], bool]:
    rel_path = normalize_rel_path(str(path.relative_to(area_root)))
    source_id = make_source_id(area, rel_path)
    existing = by_id.get(source_id)
    is_new = existing is None
    row: Dict[str, str] = dict(existing) if existing else _blank_row(source_id, area, rel_path)
    now = _utc_now_iso()

    parent_rel = str(path.parent.relative_to(area_root))
    row["folder"] = normalize_rel_path(parent_rel) if parent_rel != "." else ""
    row["filename"] = normalize_rel_path(path.name)
    if not row.get("first_seen"):
        row["first_seen"] = now

    try:
        st = path.stat()
    except OSError as e:
        if e.errno == errno.ENOENT:
            row["state"] = "missing"
        else:
            row["state"] = "error"
            row["notes"] = f"errno {e.errno}: {e}"
        return row, is_new

    row["size_bytes"] = str(st.st_size)
    row["mtime"] = _mtime_to_iso(st.st_mtime)
    row["last_seen"] = now
    if is_new or row.get("state") == "missing":
        row["state"] = "new"
    # Existing rows: `state` (and fingerprint/batch_id/dup_of/notes, carried
    # over via `dict(existing)` above) are intentionally left untouched here —
    # a rescan updates file metadata, not workflow state. The one exception is
    # "missing": a successful read means the file reappeared, so it must
    # return to the "new" workflow instead of staying stuck as missing.
    # "sent"/"processed" rows are never in this branch by definition — they
    # only become "missing" if the file vanishes, which this function
    # (a successful read) can't be seeing.

    tags = _read_raw_tags(path)
    row["tag_artist"] = tags.get("tag_artist", "")
    row["tag_title"] = tags.get("tag_title", "")
    row["tag_genre"] = tags.get("tag_genre", "")
    row["duration_seconds"] = tags.get("duration_seconds", "")
    row["audio_quality"] = tags.get("audio_quality", "")
    return row, is_new


def scan_area(
    root: Path,
    area: str,
    index_path: Path,
    *,
    limit: Optional[int] = None,
    dry_run: bool = False,
    checkpoint_every: int = 200,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, object]:
    """Scan `<root>/<AREA>` and merge results into `index_path`.

    Checkpoints every `checkpoint_every` files so a crash mid-run doesn't
    lose the whole pass. `state="missing"` is only assigned in bulk, and only
    after a *complete* clean walk (no `--limit`, no enumeration error) — see
    module docstring.

    Returns a summary dict; `complete=False` signals the caller (CLI) to
    exit non-zero and print `walk_error`.
    """
    area_root = root / area.upper()
    scan_started_at = _utc_now_iso()

    existing_rows = load_source_index(index_path)
    by_id: Dict[str, Dict[str, str]] = {r["source_id"]: dict(r) for r in existing_rows}

    collected: List[Path] = []
    walk_error: Optional[OSError] = None
    try:
        for p in iter_audio_files(area_root):
            collected.append(p)
            if limit is not None and len(collected) >= limit:
                break
    except OSError as e:
        walk_error = e

    def _checkpoint() -> None:
        if not dry_run:
            save_source_index(index_path, list(by_id.values()))

    scanned = new_count = updated_count = error_count = per_file_missing = 0
    for p in collected:
        row, is_new = _build_row(p, area, area_root, by_id)
        by_id[row["source_id"]] = row
        if is_new:
            new_count += 1
        else:
            updated_count += 1
        if row["state"] == "error":
            error_count += 1
        elif row["state"] == "missing":
            per_file_missing += 1
        scanned += 1
        if on_progress and scanned % 100 == 0:
            on_progress(scanned, len(collected))
        if scanned % checkpoint_every == 0:
            _checkpoint()

    _checkpoint()

    # Bulk "missing" pass: only safe after a complete, unlimited walk of the
    # whole area — a partial/limited walk must never be read as "the rest
    # of the files disappeared".
    is_complete = walk_error is None and limit is None
    marked_missing = 0
    if is_complete:
        for row in by_id.values():
            if row.get("area") != area or row.get("state") == "error":
                continue
            if row.get("last_seen", "") < scan_started_at:
                row["state"] = "missing"
                marked_missing += 1
        if marked_missing:
            _checkpoint()

    return {
        "complete": is_complete,
        "scanned": scanned,
        "new": new_count,
        "updated": updated_count,
        "errors": error_count,
        "per_file_missing": per_file_missing,
        "missing_marked": marked_missing,
        "walk_error": str(walk_error) if walk_error else None,
    }


# ── Fingerprinting ──────────────────────────────────────────────────────

def fingerprint_area(
    root: Path,
    index_path: Path,
    *,
    areas: Optional[List[str]] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
    checkpoint_every: int = 100,
    on_progress: Optional[Callable[[Dict[str, object]], None]] = None,
) -> Dict[str, object]:
    """Compute acoustic fingerprints for rows in `index_path` missing one.

    `index_path` is its own cache: a row with a non-empty `fingerprint` is
    skipped unless the file's current size/mtime disagree with what's
    recorded (then it's stale and recomputed). Never raises on a bad file —
    `fp_status` records the outcome (`ok` / `timeout` / `unreadable` / `error`).
    """
    areas_set = set(areas) if areas else {"before", "after"}
    rows = load_source_index(index_path)
    by_id: Dict[str, Dict[str, str]] = {r["source_id"]: r for r in rows}

    targets: List[Dict[str, str]] = []
    for row in rows:
        if row.get("area") not in areas_set:
            continue
        fp = (row.get("fingerprint") or "").strip()
        needs = not fp
        if fp:
            path = root / (row.get("area") or "").upper() / (row.get("rel_path") or "")
            try:
                st = path.stat()
                if (
                    str(st.st_size) != row.get("size_bytes", "")
                    or _mtime_to_iso(st.st_mtime) != row.get("mtime", "")
                ):
                    needs = True
            except OSError:
                pass  # can't verify freshness right now — leave cached value alone
        if needs:
            targets.append(row)
        if limit is not None and len(targets) >= limit:
            break

    total = len(targets)
    processed = ok = timeout_n = error_n = unreadable_n = 0
    started = time.monotonic()

    def _checkpoint() -> None:
        if not dry_run:
            save_source_index(index_path, list(by_id.values()))

    for row in targets:
        path = root / (row.get("area") or "").upper() / (row.get("rel_path") or "")
        row["notes"] = ""
        try:
            st = path.stat()
            row["size_bytes"] = str(st.st_size)
            row["mtime"] = _mtime_to_iso(st.st_mtime)
        except OSError as e:
            row["fp_status"] = "error"
            row["notes"] = f"errno {getattr(e, 'errno', '')}: {e}"
            processed += 1
            error_n += 1
            continue

        row["fingerprint"] = ""
        try:
            _dur, fp = fingerprint_info(path)
            if fp:
                row["fingerprint"] = fp
                row["fp_status"] = "ok"
                ok += 1
            else:
                row["fp_status"] = "unreadable"
                unreadable_n += 1
        except subprocess.TimeoutExpired:
            row["fp_status"] = "timeout"
            timeout_n += 1
        except RuntimeError as e:
            # fingerprint_info's own failure path — empty/corrupt audio.
            row["fp_status"] = "unreadable"
            row["notes"] = str(e)[:200]
            unreadable_n += 1
        except Exception as e:
            row["fp_status"] = "error"
            row["notes"] = str(e)[:200]
            error_n += 1

        processed += 1
        if on_progress and processed % 25 == 0:
            elapsed = time.monotonic() - started
            avg = elapsed / processed
            eta = avg * (total - processed)
            on_progress(
                {
                    "processed": processed,
                    "total": total,
                    "ok": ok,
                    "timeout": timeout_n,
                    "error": error_n,
                    "unreadable": unreadable_n,
                    "eta_seconds": eta,
                }
            )
        if processed % checkpoint_every == 0:
            _checkpoint()

    _checkpoint()

    return {
        "total": total,
        "processed": processed,
        "ok": ok,
        "timeout": timeout_n,
        "error": error_n,
        "unreadable": unreadable_n,
    }


# ── Matching: in_library / dup_of ───────────────────────────────────────

def match_source_index(
    index_path: Path,
    library_csv_path: Path,
    rejected_csv_path: Path,
    *,
    dry_run: bool = False,
) -> Dict[str, object]:
    """Fill `in_library` and `dup_of` on `index_path` rows from fingerprints.

    `in_library` — proof a file already passed the pipeline, matched by
    acoustic fingerprint (Chromaprint is bitrate-independent, so it catches
    the same track re-encoded at a different quality):
      - fingerprint matches `library.csv` -> `in_library` = that row's
        `track_id`
      - fingerprint matches `library-rejected.csv` -> `in_library` =
        `"rejected:<file_hash>"`, distinguishable from an accepted match —
        the file was consciously rejected, not lost track of
    Rows with no fingerprint, or no match, keep `in_library` empty. No
    filename-based guessing.

    `dup_of` — groups rows in `index_path` itself sharing an identical,
    non-empty fingerprint. Within each group, exactly one row is the
    winner (empty `dup_of`); the rest get `dup_of` = winner's `source_id`.
    Winner selection, in order:
      1. `area="after"` beats `area="before"` (a BEFORE copy of a file
         already in AFTER is the redundant one)
      2. larger `size_bytes` wins (proxy for quality — FLAC/320 over 128)
      3. smaller `source_id` wins — stable tie-break so reruns are
         deterministic
    This is the rule the user deletes files by — keep it in sync with
    `_winner_key` below if it ever changes.

    Idempotent: both columns are reset to "" before recomputing, same
    "reset before recalculation" pattern as `flag_near_dups` in
    `djlib/near_dup.py`, so a rerun after files are added/removed never
    leaves stale flags behind.

    `dry_run=True` computes and returns the summary without writing
    `index_path`.
    """
    rows = load_source_index(index_path)

    for row in rows:
        row["in_library"] = ""
        row["dup_of"] = ""

    from djlib.csvdb import load_rejected, load_records

    lib_by_fp: Dict[str, str] = {}
    for r in load_records(library_csv_path):
        fp = (r.get("fingerprint") or "").strip()
        if fp and fp not in lib_by_fp:
            lib_by_fp[fp] = r.get("track_id", "")

    rejected_by_fp: Dict[str, str] = {}
    for r in load_rejected(rejected_csv_path):
        fp = (r.get("fingerprint") or "").strip()
        if fp and fp not in rejected_by_fp:
            rejected_by_fp[fp] = r.get("file_hash", "")

    in_library_count = rejected_count = 0
    for row in rows:
        fp = (row.get("fingerprint") or "").strip()
        if not fp:
            continue
        if fp in lib_by_fp:
            row["in_library"] = lib_by_fp[fp]
            in_library_count += 1
        elif fp in rejected_by_fp:
            row["in_library"] = f"rejected:{rejected_by_fp[fp]}"
            rejected_count += 1

    by_fp: Dict[str, List[Dict[str, str]]] = {}
    for row in rows:
        fp = (row.get("fingerprint") or "").strip()
        if fp:
            by_fp.setdefault(fp, []).append(row)

    def _winner_key(row: Dict[str, str]) -> tuple:
        area_rank = 0 if row.get("area") == "after" else 1
        try:
            size_rank = -int(row.get("size_bytes") or 0)
        except ValueError:
            size_rank = 0
        return (area_rank, size_rank, row.get("source_id", ""))

    dup_groups = redundant_files = redundant_bytes = 0
    for group in by_fp.values():
        if len(group) < 2:
            continue
        winner = min(group, key=_winner_key)
        dup_groups += 1
        for row in group:
            if row is winner:
                continue
            row["dup_of"] = winner["source_id"]
            redundant_files += 1
            try:
                redundant_bytes += int(row.get("size_bytes") or 0)
            except ValueError:
                pass

    if not dry_run:
        save_source_index(index_path, rows)

    return {
        "total": len(rows),
        "in_library": in_library_count,
        "rejected": rejected_count,
        "dup_groups": dup_groups,
        "redundant_files": redundant_files,
        "redundant_bytes": redundant_bytes,
    }


# ── Archive: move processed BEFORE files into AFTER ─────────────────────

def archive_before_to_after(
    root: Path,
    index_path: Path,
    *,
    execute: bool = False,
    checkpoint_every: int = 25,
) -> Dict[str, object]:
    """Move BEFORE-area files that already passed the pipeline into AFTER.

    Qualification: a `before` row qualifies only when `in_library` is
    non-empty — i.e. `match_source_index` proved it via acoustic
    fingerprint against `library.csv` (accepted) or `library-rejected.csv`
    (rejected). `state="sent"` alone does NOT qualify: sending a file to
    `unsorted.csv` is not the same as it having gone through the pipeline —
    a batch can stall or be abandoned in the Review UI. AFTER means
    "processed", not "queued". Rows with `state="missing"` are skipped —
    there's no file left to move.

    `LOGS/moves-*.csv` is deliberately NOT consulted as a qualification
    signal. It's proven unreliable for this: two test fixtures with
    synthetic (non-UUID) `track_id`s and a since-removed destination live
    in the real `LOGS/` dir, 13 `track_id`s repeat across more than one log
    file, and every existing consumer (`cmd_undo` in cli.py, `unapply.py`)
    only ever reads the single most recent log — there's no precedent in
    this codebase for safely summing the whole history. `in_library`
    (backed by acoustic fingerprints, already computed by
    `match_source_index`) is a strictly stronger and already-available
    signal for "this file was processed", so it's the only one used.

    Default is a dry run: with `execute=False` the NAS is never touched —
    only a plan (list of `{source_id, src, dest, size_bytes}`) and summary
    counts are returned. Pass `execute=True` to actually move files. Each
    move is independently gated (same pattern as `run_gig_cleanup` in
    djlib/gig.py) — one bad file is logged and counted, the rest continue.

    Collisions in AFTER are resolved with `_ensure_unique_path`
    (djlib/cli.py — the only NFC/NFD-aware implementation); an existing
    AFTER file is never overwritten.

    On a successful move: `area` -> `"after"`, `rel_path` -> the
    (possibly renamed) destination's path relative to AFTER, `state` ->
    `"processed"`, and `source_id` is recomputed for the new (area,
    rel_path) — `source_id` is defined as a function of those two fields
    (see `make_source_id`), so leaving it stale would make a future
    `original-scan` of AFTER create a duplicate row for the same file.
    Checkpointed to `index_path` every `checkpoint_every` moves.
    """
    rows = load_source_index(index_path)
    by_id: Dict[str, Dict[str, str]] = {r["source_id"]: r for r in rows}

    qualified = [
        r
        for r in rows
        if r.get("area") == "before"
        and (r.get("in_library") or "").strip()
        and r.get("state") != "missing"
    ]

    plan: List[Dict[str, object]] = []
    planned_bytes = 0
    for row in qualified:
        rel_path = row.get("rel_path") or ""
        size_bytes = int(row["size_bytes"]) if (row.get("size_bytes") or "").isdigit() else 0
        plan.append(
            {
                "source_id": row["source_id"],
                "src": root / "BEFORE" / rel_path,
                "dest": root / "AFTER" / rel_path,
                "size_bytes": size_bytes,
            }
        )
        planned_bytes += size_bytes

    if not execute:
        return {
            "execute": False,
            "qualified": len(qualified),
            "planned_bytes": planned_bytes,
            "plan": plan,
        }

    from djlib.cli import _ensure_unique_path  # lazy: cli.py never imports original.py at module level

    moved = errors = 0
    since_checkpoint = 0
    for item in plan:
        row = by_id[item["source_id"]]
        src = item["src"]
        dest = item["dest"]
        if not src.is_file():
            row["notes"] = "source missing at archive time"
            errors += 1
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest = _ensure_unique_path(dest)
            shutil.move(str(src), str(dest))
        except Exception as e:
            row["notes"] = f"archive move failed: {e}"
            errors += 1
            continue

        new_rel_path = normalize_rel_path(str(dest.relative_to(root / "AFTER")))
        row["area"] = "after"
        row["rel_path"] = new_rel_path
        row["state"] = "processed"
        row["source_id"] = make_source_id("after", new_rel_path)
        moved += 1
        since_checkpoint += 1
        if since_checkpoint >= checkpoint_every:
            save_source_index(index_path, list(by_id.values()))
            since_checkpoint = 0

    save_source_index(index_path, list(by_id.values()))

    return {
        "execute": True,
        "qualified": len(qualified),
        "moved": moved,
        "errors": errors,
        "planned_bytes": planned_bytes,
    }
