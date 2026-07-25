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
    if is_new:
        row["state"] = "new"
    # Existing rows: `state` (and fingerprint/batch_id/dup_of/notes, carried
    # over via `dict(existing)` above) are intentionally left untouched here —
    # a rescan updates file metadata, not workflow state.

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
