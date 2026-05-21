"""Review UI server — Flask app for track preview and approval workflow.

Usage:
    python -m djlib.cli review [--port 8899] [--no-browser]

Serves unsorted.csv (editable) and library.csv (read-only) as interactive
tables with inline audio playback. Keyboard-driven: Space to play/pause,
arrows to navigate, A/R/V for accept/reject/review.
"""
from __future__ import annotations

import concurrent.futures
import csv
import json
import logging
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests as http_requests
import yaml
from flask import Flask, Response, jsonify, render_template, request, send_file

from djlib.config import (
    CSV_PATH,
    INBOX_DIR,
    LOGS_DIR,
    UNSORTED_CSV,
    get_ai_chat_model,
    get_ai_quick_model,
    get_openai_api_key,
)
from djlib.filename import parse_from_filename
from djlib.unsorted import load_unsorted_rows, write_unsorted_rows, EXPORT_DISPOSITIONS
from djlib.locks import csv_lock

# ── Flask app ────────────────────────────────────────────────────────────────

_HERE = Path(__file__).resolve().parent

app = Flask(
    __name__,
    template_folder=str(_HERE / "templates"),
    static_folder=str(_HERE / "static"),
)

# Extend MIME types for audio formats browsers handle natively
mimetypes.add_type("audio/flac", ".flac")
mimetypes.add_type("audio/aiff", ".aiff")
mimetypes.add_type("audio/aiff", ".aif")
mimetypes.add_type("audio/mp4", ".m4a")
mimetypes.add_type("audio/wav", ".wav")

_REPO = Path(__file__).resolve().parents[2]
_CSV_LOCK = threading.Lock()

# Library review CSV (temporary, for re-processing existing library tracks)
LIBRARY_REVIEW_CSV = _REPO / "data" / "library_review.csv"


def _static_version() -> str:
    """Return max mtime of static files as cache-buster query string."""
    static = _HERE / "static"
    try:
        ts = max(f.stat().st_mtime for f in static.iterdir() if f.is_file())
        return str(int(ts))
    except (ValueError, OSError):
        return "0"


@app.after_request
def _no_cache_static(response: Response) -> Response:
    """Prevent browser caching of JS/CSS during development."""
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ── Version-groups cache ─────────────────────────────────────────────────────
# Invalidated by any endpoint that writes unsorted.csv or library.csv so that
# stale groups are never served after a rating change or track update.

_VG_CACHE: Dict[str, Any] = {}   # source → {"result": ..., "etag": ...}


def _vg_cache_key(source: str) -> str:
    """Cache key = source + mtime of the CSV files involved."""
    mtimes = []
    if source in ("unsorted", "all"):
        try:
            mtimes.append(int(UNSORTED_CSV.stat().st_mtime * 1000))
        except OSError:
            mtimes.append(0)
    if source in ("library", "all"):
        lib_path = _REPO / "data" / "library.csv"
        try:
            mtimes.append(int(lib_path.stat().st_mtime * 1000))
        except OSError:
            mtimes.append(0)
    return f"{source}:{':'.join(str(m) for m in mtimes)}"


def _invalidate_vg_cache() -> None:
    _VG_CACHE.clear()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_library_csv() -> List[Dict[str, str]]:
    """Load library.csv (sync-dj-libraries format) via stdlib csv."""
    csv_path = _REPO / "data" / "library.csv"
    if not csv_path.exists():
        return []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def _load_library_review_csv() -> List[Dict[str, str]]:
    """Load library_review.csv (same format as unsorted.csv) for re-review."""
    if not LIBRARY_REVIEW_CSV.exists():
        return []
    return load_unsorted_rows(LIBRARY_REVIEW_CSV)


def _get_processed_dest_roots() -> List[tuple[str, Path]]:
    """Return (dest_type, canonical_root) pairs, most-specific-first.

    `mixes` lives under `library` by default (`{LIB_ROOT}/MIXES/`), so it must
    be checked before `library`, otherwise every mix is misclassified.
    """
    from djlib import logistics

    return [
        ("mixes", logistics.get_destination_path("mixes").resolve()),
        ("rejected", logistics.get_destination_path("reject").resolve()),
        ("library", logistics.get_destination_path("library").resolve()),
    ]


def _classify_processed_path(path_str: str, roots: List[tuple[str, Path]]) -> str:
    """Return dest_type for `path_str` if it lives under any root, else ''."""
    if not path_str:
        return ""
    try:
        p = Path(path_str).resolve()
    except (OSError, ValueError):
        return ""
    for dtype, root in roots:
        try:
            p.relative_to(root)
            return dtype
        except ValueError:
            continue
    return ""


def _load_processed_tracks() -> List[Dict[str, str]]:
    """Load processed tracks from library.csv filtered by destination folders.

    Tracks are considered "processed" if their `old_full_path` lives under one
    of the canonical destination roots from `djlib.logistics`: library, reject,
    archive, or mixes. library.csv is regenerated by sync-dj-libraries from the
    actual Rekordbox/Traktor databases, so it reflects reality.

    Move logs (LOGS/moves-*.csv) are historical artifacts with known issues
    (duplicates, stale entries) and are NOT used here.
    """
    lib_rows = _load_library_csv()
    if not lib_rows:
        return []

    roots = _get_processed_dest_roots()

    result: List[Dict[str, str]] = []
    for row in lib_rows:
        path = row.get("old_full_path", "")
        if not path:
            continue

        dest_type = _classify_processed_path(path, roots)
        if not dest_type:
            continue  # Not a processed track (e.g. ~/Music/ from DJ imports)

        ext_src = row.get("external_source", "").strip()
        analysis_src = row.get("analysis_source", "").strip()
        rb_id = row.get("rekordbox_id", "").strip()
        # "ready for Rekordbox" = DJ software has actually analyzed this
        # track. A track imported via `apply --allow-no-rekordbox` has
        # analysis_source=tags and no rekordbox_id until the next sync
        # picks it up from Rekordbox's database.
        ready_for_rb = "yes" if rb_id else "no"
        rec: Dict[str, str] = {
            "track_id": row.get("track_id", ""),
            "file_path": path,  # for audio playback
            "artist": row.get("artist", ""),
            "title": row.get("title", ""),
            "bpm": row.get("bpm", ""),
            "key": row.get("key", ""),
            "rating": row.get("rating", ""),
            "play_count": row.get("play_count", ""),
            "external_source": ext_src,
            "analysis_source": analysis_src,
            "date_added": row.get("date_added", ""),
            "destination": dest_type,
            "in_dj_software": "yes" if ext_src else "no",
            "ready_for_rekordbox": ready_for_rb,
        }
        result.append(rec)

    # Sort by date_added descending (newest first), then artist
    result.sort(key=lambda r: (r.get("date_added", "") or "", r.get("artist", "")), reverse=True)
    return result


def _load_genres() -> List[str]:
    """Return sorted list of genre labels from genres.yml."""
    genres_path = _REPO / "genres.yml"
    if not genres_path.exists():
        return []
    with open(genres_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return []
    labels = sorted(
        {entry.get("label", key) for key, entry in data.items() if isinstance(entry, dict)}
    )
    return labels


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", v=_static_version())


@app.route("/api/tracks")
def api_tracks():
    """Return JSON list of tracks from unsorted.csv, library.csv, or processed."""
    source = request.args.get("source", "unsorted")
    if source == "unsorted":
        rows = load_unsorted_rows(UNSORTED_CSV)
    elif source == "library":
        rows = _load_library_csv()
    elif source in ("library-review", "library-fix"):
        rows = _load_library_review_csv()
    elif source == "processed":
        rows = _load_processed_tracks()
    else:
        return jsonify({"error": f"Unknown source: {source}"}), 400
    return jsonify(rows)


@app.route("/api/library-index")
def api_library_index():
    """Return normalised artist::title keys for duplicate detection.

    The client uses this to show an 'already in library' badge on
    unsorted tracks that match an existing library entry.
    """
    lib = _load_library_csv()
    keys: set[str] = set()
    for row in lib:
        a = (row.get("artist") or "").strip().lower()
        t = (row.get("title") or "").strip().lower()
        if a and t:
            keys.add(f"{a}::{t}")
    return jsonify(sorted(keys))


@app.route("/api/version-groups")
def api_version_groups():
    """Return tracks grouped by version (same song, different mix/edit).

    ?source=library|unsorted|all  (default: all — cross-source comparison)

    Response: { groups: [ { group_id, members: [track, ...] } ], ... }
    Each track has an extra ``_version_info`` field (extracted from title parens)
    and ``_source`` (which CSV it came from).

    Result is mtime-keyed: re-computed only when CSV files change on disk.
    """
    from djlib.versions import group_versions, extract_version_info

    source = request.args.get("source", "all")
    if source not in ("unsorted", "library", "all"):
        return jsonify({"error": f"Unknown source: {source}"}), 400

    cache_key = _vg_cache_key(source)
    cached = _VG_CACHE.get(source)
    if cached and cached.get("key") == cache_key:
        return jsonify(cached["result"])

    rows: List[Dict] = []

    if source in ("unsorted", "all"):
        for row in load_unsorted_rows(UNSORTED_CSV):
            row["_source"] = "unsorted"
            rows.append(row)

    if source in ("library", "all"):
        for row in _load_library_csv():
            row["_source"] = "library"
            rows.append(row)

    groups = group_versions(rows)

    result_groups = []
    for gid, members in groups.items():
        for m in members:
            title = m.get("title") or m.get("tag_title_original") or ""
            m["_version_info"] = extract_version_info(title)
        # Sort: duration descending (Extended first); ties: title alphabetically
        members.sort(key=lambda r: (
            -(float(r.get("duration_seconds") or 0) or 0),
            (r.get("title") or ""),
        ))
        result_groups.append({"group_id": gid, "members": members})

    # Sort groups: most members first, then alphabetically by first member artist+title
    result_groups.sort(key=lambda g: (
        -len(g["members"]),
        (g["members"][0].get("artist") or "") + (g["members"][0].get("title") or ""),
    ))

    result = {"groups": result_groups, "total_groups": len(result_groups)}
    _VG_CACHE[source] = {"key": cache_key, "result": result}
    return jsonify(result)


@app.route("/api/version-group-id")
def api_version_group_id():
    """Return version_group_id for a given artist + title (for JS badge building)."""
    from djlib.versions import version_group_id
    artist = request.args.get("artist", "")
    title = request.args.get("title", "")
    return jsonify({"group_id": version_group_id(artist, title)})


@app.route("/api/tracks/version-group-rating", methods=["PATCH"])
def api_version_group_rating():
    """Set preferred track to 5★, all peers to 3★ — atomic single CSV write per source.

    Request body:
      {
        "preferred_id": "track_id",
        "peer_ids": ["track_id", ...],
        "source": "unsorted"   // or "library"
      }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    preferred_id = data.get("preferred_id")
    peer_ids: List[str] = data.get("peer_ids") or []
    source = data.get("source", "unsorted")

    if not preferred_id:
        return jsonify({"error": "Missing preferred_id"}), 400

    all_ids = {preferred_id: "5"} | {pid: "3" for pid in peer_ids}

    if source == "library":
        from djlib.library_schema import load_library_csv, save_library_csv
        lib_path = _REPO / "data" / "library.csv"
        with _CSV_LOCK:
            with csv_lock(lib_path):
                rows = load_library_csv(lib_path)
                updated = 0
                for row in rows:
                    tid = row.get("track_id") or ""
                    if tid in all_ids:
                        row["rating"] = all_ids[tid]
                        updated += 1
                if updated:
                    save_library_csv(lib_path, rows)
        return jsonify({"ok": True, "updated": updated})
    else:
        csv_file = LIBRARY_REVIEW_CSV if source in ("library-review", "library-fix") else UNSORTED_CSV
        with _CSV_LOCK:
            with csv_lock(csv_file):
                rows = load_unsorted_rows(csv_file)
                updated = 0
                for row in rows:
                    tid = row.get("track_id") or row.get("file_hash") or ""
                    if tid in all_ids:
                        row["rating"] = all_ids[tid]
                        updated += 1
                if updated:
                    write_unsorted_rows(csv_file, rows, [])
        return jsonify({"ok": True, "updated": updated})


_TRANSCODE_EXTS = {".aiff", ".aif", ".flac", ".wav"}


def _stream_transcoded(p: Path) -> "Response":
    """Transcode audio to MP3 via ffmpeg, buffer fully, then serve with range support.

    Buffering (vs streaming) gives the browser a Content-Length so it can:
    - display the correct duration instead of Infinity
    - seek to arbitrary positions
    Typical AIFF/FLAC → MP3 192kbps ≈ 8–12 MB, transcodes in ~1–3 s.
    """
    import subprocess, io

    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(p), "-vn", "-f", "mp3", "-ab", "192k", "-"],
        capture_output=True,
    )
    buf = io.BytesIO(result.stdout)
    buf.seek(0)
    return send_file(buf, mimetype="audio/mpeg", conditional=True, max_age=3600)


@app.route("/api/audio")
def api_audio():
    """Stream an audio file from the local filesystem.

    Formats browsers can't play natively (AIFF, FLAC, WAV) are transcoded
    to MP3 via ffmpeg. MP3/M4A/OGG are served directly.
    Supports Range requests for seekable formats.
    """
    path_str = request.args.get("path", "")
    if not path_str:
        return jsonify({"error": "No path provided"}), 400

    p = Path(path_str).expanduser().resolve()
    if not p.exists():
        return jsonify({"error": f"File not found: {path_str}"}), 404

    if p.suffix.lower() in _TRANSCODE_EXTS:
        return _stream_transcoded(p)

    mime = mimetypes.guess_type(str(p))[0] or "audio/mpeg"
    resp = send_file(p, mimetype=mime, conditional=True)
    resp.headers["Cache-Control"] = "private, max-age=3600, immutable"
    return resp


@app.route("/api/tracks/update", methods=["POST"])
def api_update_track():
    """Update fields of one unsorted track (by track_id or file_hash).

    Request body (JSON):
        { "track_id": "...", "fields": { "status": "accept", ... } }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    tid = data.get("track_id") or data.get("file_hash")
    fields = data.get("fields", {})
    source = data.get("source", "unsorted")
    if not tid:
        return jsonify({"error": "Missing track_id or file_hash"}), 400
    if not fields:
        return jsonify({"error": "No fields to update"}), 400

    # Determine which CSV to update
    csv_file = LIBRARY_REVIEW_CSV if source in ("library-review", "library-fix") else UNSORTED_CSV

    # Lock order: in-process (_CSV_LOCK) → cross-process (csv_lock).
    # The cross-process lock prevents a CLI sync from writing between our
    # read and write. Re-entrant: write_unsorted_rows internally re-acquires.
    with _CSV_LOCK:
        with csv_lock(csv_file):
            rows = load_unsorted_rows(csv_file)
            updated = False
            for row in rows:
                if row.get("track_id") == tid or row.get("file_hash") == tid:
                    for key, value in fields.items():
                        if key in row:
                            row[key] = str(value)
                    updated = True
                    break

            if not updated:
                return jsonify({"error": f"Track not found: {tid}"}), 404

            write_unsorted_rows(csv_file, rows, [])
    return jsonify({"ok": True})


@app.route("/api/tracks/batch-update", methods=["POST"])
def api_batch_update_tracks():
    """Update fields of multiple tracks at once.

    Request body (JSON):
        { "track_ids": ["id1", "id2", ...], "fields": { "genre": "Tech House", ... }, "source": "unsorted" }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    track_ids = data.get("track_ids", [])
    fields = data.get("fields", {})
    source = data.get("source", "unsorted")
    if not track_ids:
        return jsonify({"error": "No track_ids"}), 400
    if not fields:
        return jsonify({"error": "No fields to update"}), 400

    csv_file = LIBRARY_REVIEW_CSV if source in ("library-review", "library-fix") else UNSORTED_CSV
    id_set = set(track_ids)

    with _CSV_LOCK:
        with csv_lock(csv_file):
            rows = load_unsorted_rows(csv_file)
            # Detect which requested fields actually exist in this CSV's schema
            schema_keys = set(rows[0].keys()) if rows else set()
            applied_fields = {k: v for k, v in fields.items() if k in schema_keys}
            dropped_fields = [k for k in fields.keys() if k not in schema_keys]

            if not applied_fields:
                return jsonify({
                    "ok": False,
                    "error": "None of the requested fields exist in this CSV",
                    "dropped_fields": dropped_fields,
                }), 400

            updated = 0
            for row in rows:
                tid = row.get("track_id") or row.get("file_hash")
                if tid in id_set:
                    for key, value in applied_fields.items():
                        row[key] = str(value)
                    updated += 1

            if updated == 0:
                return jsonify({"error": "No matching tracks found"}), 404

            write_unsorted_rows(csv_file, rows, [])
    return jsonify({"ok": True, "updated": updated, "dropped_fields": dropped_fields})


@app.route("/api/genres")
def api_genres():
    """Return list of valid genre labels from genres.yml."""
    return jsonify(_load_genres())


@app.route("/api/reveal", methods=["POST"])
def api_reveal():
    """Reveal a file in Finder (macOS) or file manager."""
    data = request.get_json(silent=True)
    if not data or not data.get("path"):
        return jsonify({"error": "No path provided"}), 400

    p = Path(data["path"]).expanduser().resolve()
    if not p.exists():
        return jsonify({"error": f"File not found: {data['path']}"}), 404

    try:
        import sys
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)])
        elif sys.platform == "linux":
            subprocess.Popen(["xdg-open", str(p.parent)])
        else:
            subprocess.Popen(["explorer", "/select,", str(p)])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Pending-suggestions sidecar ──────────────────────────────────────────────
#
# Ghost-row review writes proposals here before the user accepts them.
# Shape: { track_id: { field: {value, source, confidence, was}, timestamp } }
# Atomic writes (tmp + os.replace) guarded by _CSV_LOCK so concurrent Flask
# threads don't produce a torn JSON file.

_SIDECAR_PATH = LOGS_DIR / "pending-suggestions.json"
# TTL: discard suggestions older than this many seconds (24 h)
_SIDECAR_TTL_S = 86_400


def _read_sidecar() -> Dict[str, Any]:
    """Load sidecar, purging entries older than TTL. Returns {} on missing/corrupt."""
    if not _SIDECAR_PATH.exists():
        return {}
    try:
        with open(_SIDECAR_PATH, encoding="utf-8") as f:
            data: Dict[str, Any] = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    cutoff = time.time() - _SIDECAR_TTL_S
    return {
        tid: entry
        for tid, entry in data.items()
        if isinstance(entry, dict) and entry.get("_ts", 0) >= cutoff
    }


def _write_sidecar(data: Dict[str, Any]) -> None:
    """Atomically overwrite sidecar with `data`. Must be called under _CSV_LOCK."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=LOGS_DIR, suffix=".json.tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, _SIDECAR_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _sidecar_set(tid: str, fields: Dict[str, Any]) -> None:
    """Add/replace the proposal entry for one track. Thread-safe."""
    with _CSV_LOCK:
        data = _read_sidecar()
        data[tid] = {**fields, "_ts": time.time()}
        _write_sidecar(data)


def _sidecar_remove(tids: List[str]) -> None:
    """Remove entries for applied track IDs. Thread-safe."""
    with _CSV_LOCK:
        data = _read_sidecar()
        for tid in tids:
            data.pop(tid, None)
        _write_sidecar(data)


# ── Batch enrich job registry ─────────────────────────────────────────────────
#
# Jobs live in memory; they're ephemeral. The sidecar persists results.

_BATCH_JOBS: Dict[str, Dict[str, Any]] = {}
_BATCH_JOBS_LOCK = threading.Lock()
_BATCH_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="enrich-batch")
_MB_SEMAPHORE = threading.Semaphore(1)  # MusicBrainz: max 1 concurrent request (1 req/s policy)
_DISCOGS_SEMAPHORE = threading.Semaphore(1)  # Discogs: 25 req/min anon → ~2.5s gap is safe

_SEARXNG_COMPOSE = Path(__file__).resolve().parents[2] / "docker" / "docker-compose.searxng.yml"
_SEARXNG_STARTUP_LOCK = threading.Lock()  # Prevent concurrent Docker startup races


def _ensure_searxng_running(timeout_docker: int = 60, timeout_searxng: int = 30) -> bool:
    """Best-effort: ensure the SearXNG Docker container is up before batch enrichment.

    Steps:
    1. If SearXNG already responds → done immediately (no lock needed).
    2. Acquire startup lock so only one thread drives the Docker startup.
    3. On macOS only: launch Docker Desktop and wait for the daemon.
    4. Run ``docker compose up -d`` for the SearXNG service.
    5. Wait until SearXNG responds or timeout expires.
    6. Reset genre_classifier._searcher so the classifier re-checks availability.

    Returns True if SearXNG is available after this call, False otherwise.
    """
    from djlib.metadata.web_search import create_searcher

    def _searxng_up() -> bool:
        try:
            return create_searcher("searxng").is_available()
        except Exception:
            return False

    if _searxng_up():
        return True

    with _SEARXNG_STARTUP_LOCK:
        # Re-check inside lock — another thread may have just started it
        if _searxng_up():
            return True

        _log.info("SearXNG not available — attempting Docker startup")

        # Check Docker daemon
        try:
            r = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
            docker_ok = r.returncode == 0
        except Exception:
            docker_ok = False

        if not docker_ok:
            if sys.platform != "darwin":
                _log.warning(
                    "Docker daemon not running and auto-start only supported on macOS "
                    "(current platform: %s). Start Docker manually.", sys.platform
                )
                return False
            _log.info("Docker daemon not running — launching Docker Desktop")
            try:
                subprocess.Popen(["open", "-a", "Docker"])
            except Exception as exc:
                _log.warning("Could not launch Docker Desktop: %s", exc)
                return False
            deadline = time.monotonic() + timeout_docker
            while time.monotonic() < deadline:
                time.sleep(2)
                try:
                    r = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
                    if r.returncode == 0:
                        _log.info("Docker daemon ready")
                        break
                except Exception:
                    pass
            else:
                _log.warning("Docker daemon did not start within %ds", timeout_docker)
                return False

        # Start SearXNG container
        if _SEARXNG_COMPOSE.exists():
            try:
                subprocess.run(
                    ["docker", "compose", "-f", str(_SEARXNG_COMPOSE), "up", "-d"],
                    capture_output=True, timeout=60,
                )
                _log.info("docker compose up -d finished")
            except Exception as exc:
                _log.warning("docker compose up failed: %s", exc)

        # Wait for SearXNG to respond
        deadline = time.monotonic() + timeout_searxng
        while time.monotonic() < deadline:
            if _searxng_up():
                _log.info("SearXNG is now available")
                # Reset module-level searcher cache so classifier picks up the live instance
                try:
                    import djlib.metadata.genre_classifier as _gc
                    with _gc._searcher_lock:
                        _gc._searcher = None
                except Exception:
                    pass
                return True
            time.sleep(2)

        _log.warning("SearXNG did not become available within %ds", timeout_searxng)
    return False


# ── AI Genre Suggest ─────────────────────────────────────────────────────────

_ai_cache: Dict[str, Dict[str, Any]] = {}  # track_id -> {genre, confidence, reasoning}
# Cache keyed by "{track_id}|{artist_lower}|{title_lower}" so edits to
# artist/title on the same file correctly miss the cache (track_id is stable,
# but the query content changed).
_identify_cache: Dict[str, Dict[str, Any]] = {}
_log = logging.getLogger(__name__)


@app.route("/api/ai-status")
def api_ai_status():
    """Check if OpenAI API key is configured."""
    key = get_openai_api_key()
    return jsonify({"available": bool(key)})


@app.route("/api/suggest-genre", methods=["POST"])
def api_suggest_genre():
    """Ask OpenAI to classify a track's genre based on available metadata.

    Request body (JSON):
        { "track_id": "...", "context": { artist, title, version, bpm, ... } }

    Returns:
        { "genre": "Afro House", "confidence": 0.9, "reasoning": "..." }
    """
    api_key = get_openai_api_key()
    if not api_key:
        return jsonify({"error": "OpenAI API key not configured. Add openai_api_key to config.local.yml"}), 501

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    tid = data.get("track_id", "")
    ctx = data.get("context", {})

    if not ctx.get("artist") and not ctx.get("title"):
        return jsonify({"error": "Need at least artist or title"}), 400

    # Check cache
    if tid and tid in _ai_cache:
        return jsonify(_ai_cache[tid])

    # Build prompt
    genre_labels = _load_genres()
    prompt = _build_genre_prompt(ctx, genre_labels)

    try:
        result = _call_openai(api_key, prompt)
        if tid:
            _ai_cache[tid] = result
        return jsonify(result)
    except Exception as e:
        _log.warning("OpenAI API error: %s", e)
        return jsonify({"error": f"AI request failed: {e}"}), 502


def _build_genre_prompt(ctx: Dict[str, str], genre_labels: List[str]) -> str:
    """Build the system + user prompt for genre classification."""
    genre_list = ", ".join(genre_labels)

    # Collect all available context
    parts = []
    if ctx.get("artist"):
        parts.append(f"Artist: {ctx['artist']}")
    if ctx.get("title"):
        parts.append(f"Title: {ctx['title']}")
    if ctx.get("version"):
        parts.append(f"Version/Remix: {ctx['version']}")
    if ctx.get("bpm"):
        parts.append(f"BPM: {ctx['bpm']}")
    if ctx.get("key"):
        parts.append(f"Key: {ctx['key']}")
    if ctx.get("duration"):
        parts.append(f"Duration: {ctx['duration']}")
    if ctx.get("folder"):
        parts.append(f"Source folder: {ctx['folder']}")
    if ctx.get("genres_musicbrainz"):
        parts.append(f"MusicBrainz genres: {ctx['genres_musicbrainz']}")
    if ctx.get("genres_lastfm"):
        parts.append(f"Last.fm genres: {ctx['genres_lastfm']}")
    if ctx.get("genres_soundcloud"):
        parts.append(f"SoundCloud tags: {ctx['genres_soundcloud']}")
    if ctx.get("genres_beatport"):
        parts.append(f"Beatport genre: {ctx['genres_beatport']}")
    if ctx.get("genre_suggest"):
        parts.append(f"Current suggestion (may be wrong): {ctx['genre_suggest']}")

    track_info = "\n".join(parts)

    # Detect remix/edit for targeted instructions
    version_str = ctx.get("version", "")
    is_remix = bool(re.search(
        r'\b(?:remix|edit|bootleg|rework|refix|mashup|flip|rework)\b',
        version_str, re.IGNORECASE,
    ))

    remix_instruction = ""
    if is_remix:
        remix_instruction = (
            "\n\nCRITICAL — REMIX/EDIT CLASSIFICATION RULE:\n"
            "This track is a REMIX or EDIT. You MUST classify it by the REMIX STYLE, "
            "NOT by the original track's genre. The remixer transforms the track into a new genre.\n"
            "Example: a Hip-Hop track remixed at 124 BPM with a four-on-the-floor kick = Tech House, "
            "NOT Hip-Hop. A Pop ballad remixed at 130 BPM with driving bassline = House, NOT Pop.\n"
            "The version/remix field and BPM are the strongest signals for remixes. "
            "The original artist's genre is almost always WRONG for the remix."
        )

    bpm_guide = (
        "\n\nBPM genre ranges (approximate, ranges overlap — use together with other signals):\n"
        "70-100: Hip-Hop, R&B, Reggaeton, Dancehall\n"
        "100-115: Broken Beat, UK Garage, Afrobeats\n"
        "115-126: Deep House, Soulful House\n"
        "116-128: Afro House, Organic House\n"
        "120-128: House, Tech House, Jackin House\n"
        "124-132: Melodic House & Techno, Progressive House\n"
        "128-140: Techno, Hard Techno, Trance, Hard Dance\n"
        "140-150: Psytrance\n"
        "150-180: Jungle, Drum & Bass\n"
        "If BPM is available, it should STRONGLY influence your genre choice."
    )

    return (
        f"You are a DJ music genre classifier for a DJ's track library. "
        f"Classify this track into exactly ONE genre from the following list:\n{genre_list}\n\n"
        f"Track information:\n{track_info}"
        f"{remix_instruction}"
        f"{bpm_guide}\n\n"
        f"Consider BPM range (strongest signal), remixer scene/style, "
        f"source folder name (often hints at genre), and any available genre tags. "
        f"If the existing tags are artist names or nonsense, ignore them.\n\n"
        f"Respond ONLY with valid JSON (no markdown, no code fences):\n"
        f'{{"genre": "<exact genre from list>", "confidence": <0.0-1.0>, "reasoning": "<1-2 sentences>"}}'
    )


def _call_openai_json(api_key: str, prompt: str, max_tokens: int = 200) -> Dict[str, Any]:
    """Call OpenAI Chat Completions API and return parsed JSON response."""
    resp = http_requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": get_ai_quick_model(),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        },
        timeout=15,
    )
    resp.raise_for_status()

    data = resp.json()
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")

    # Parse JSON from response (handle potential markdown fences)
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```\w*\n?", "", content)
        content = re.sub(r"\n?```$", "", content)
        content = content.strip()

    return json.loads(content)


def _call_openai_responses_json(
    api_key: str, prompt: str, max_tokens: int = 400
) -> Dict[str, Any]:
    """Call OpenAI Responses API with web search and parse JSON from reply.

    Like ``_call_openai_json`` but uses the Responses API with
    ``web_search_preview`` so the model can look up information online.
    Expects the model to return valid JSON (possibly wrapped in markdown
    fences).  Returns the parsed dict.
    """
    payload: Dict[str, Any] = {
        "model": get_ai_chat_model(),
        "input": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search_preview"}],
        "temperature": 0.3,
        "max_output_tokens": max_tokens,
        "instructions": (
            "You MUST respond ONLY with valid JSON. No explanation, no markdown "
            "fences, no text before or after the JSON object. If you use web "
            "search, still respond with JSON only."
        ),
    }

    resp = http_requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=45,
    )
    resp.raise_for_status()

    data = resp.json()

    # Extract text — same fallback as _call_openai_chat
    content = (data.get("output_text") or "").strip()
    if not content:
        for item in data.get("output", []):
            if item.get("type") == "message":
                for block in item.get("content", []):
                    if block.get("type") == "output_text":
                        content = (block.get("text") or "").strip()
                        if content:
                            break
            if content:
                break

    # Strip markdown citations
    content = re.sub(r'\s*\(\[([^\]]*)\]\([^)]*\)\)\s*', ' ', content).strip()
    content = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', content)

    # Strip markdown fences
    if content.startswith("```"):
        content = re.sub(r"^```\w*\n?", "", content)
        content = re.sub(r"\n?```$", "", content)
        content = content.strip()

    return json.loads(content)


def _call_openai(api_key: str, prompt: str) -> Dict[str, Any]:
    """Call OpenAI for genre suggestion (with genre validation)."""
    result = _call_openai_json(api_key, prompt)

    # Validate genre is from our list
    genre_labels = _load_genres()
    if result.get("genre") not in genre_labels:
        # Try case-insensitive match
        lower_map = {g.lower(): g for g in genre_labels}
        matched = lower_map.get((result.get("genre") or "").lower())
        if matched:
            result["genre"] = matched
        else:
            result["warning"] = f"AI suggested '{result.get('genre')}' which is not in genres.yml"

    return result


# ── AI Track Identify ─────────────────────────────────────────────────────────


def _gather_track_context(row: Dict[str, str]) -> str:
    """Extract all available track metadata from a CSV row as formatted text.

    Used by identify prompt and AI chat system prompt to provide context.
    """
    # Extract filename and folder from file_path
    file_path = (row.get("file_path") or "").strip()
    filename = ""
    folder = ""
    if file_path:
        p = Path(file_path).expanduser()
        filename = p.name
        folder = p.parent.name

    parts = []

    if filename:
        parts.append(f"Filename: {filename}")
    if folder:
        parts.append(f"Folder: {folder} (NOTE: folder names are for file organization only, NOT reliable genre indicators)")

    # Original audio file tags
    tag_artist = (row.get("tag_artist_original") or "").strip()
    tag_title = (row.get("tag_title_original") or "").strip()
    tag_genre = (row.get("tag_genre_original") or "").strip()
    if tag_artist:
        parts.append(f"Audio tag artist: {tag_artist}")
    if tag_title:
        parts.append(f"Audio tag title: {tag_title}")
    if tag_genre:
        parts.append(f"Audio tag genre: {tag_genre}")

    # Parser results (what our system extracted from filename)
    artist_suggest = (row.get("artist_suggest") or "").strip()
    title_suggest = (row.get("title_suggest") or "").strip()
    version_suggest = (row.get("version_suggest") or "").strip()
    if artist_suggest:
        parts.append(f"Parsed artist: {artist_suggest}")
    if title_suggest:
        parts.append(f"Parsed title: {title_suggest}")
    if version_suggest:
        parts.append(f"Parsed version: {version_suggest}")

    # Current user-edited values (may differ from parsed)
    current_artist = (row.get("artist") or "").strip()
    current_title = (row.get("title") or "").strip()
    current_version = (row.get("version_info") or "").strip()
    if current_artist and current_artist != artist_suggest:
        parts.append(f"Current artist (user-edited): {current_artist}")
    if current_title and current_title != title_suggest:
        parts.append(f"Current title (user-edited): {current_title}")
    if current_version and current_version != version_suggest:
        parts.append(f"Current version (user-edited): {current_version}")

    # Audio characteristics
    bpm = (row.get("bpm") or "").strip()
    key = (row.get("key_camelot") or "").strip()
    duration = (row.get("duration_suggest") or "").strip()
    if bpm:
        parts.append(f"BPM: {bpm}")
    if key:
        parts.append(f"Key: {key}")
    if duration:
        parts.append(f"Duration: {duration}")

    # Online metadata sources
    sc_genres = (row.get("genres_soundcloud") or "").strip()
    bp_genres = (row.get("genres_beatport") or "").strip()
    mb_genres = (row.get("genres_musicbrainz") or "").strip()
    lf_genres = (row.get("genres_lastfm") or "").strip()
    if sc_genres:
        parts.append(f"SoundCloud tags: {sc_genres}")
    if bp_genres:
        parts.append(f"Beatport genre: {bp_genres}")
    if mb_genres:
        parts.append(f"MusicBrainz genres: {mb_genres}")
    if lf_genres:
        parts.append(f"Last.fm genres: {lf_genres}")

    # Other metadata
    genre_suggest = (row.get("genre_suggest") or "").strip()
    year_suggest = (row.get("year_suggest") or "").strip()
    meta_source = (row.get("meta_source") or "").strip()
    if genre_suggest:
        parts.append(f"Genre suggestion: {genre_suggest}")
    if year_suggest:
        parts.append(f"Year suggestion: {year_suggest}")
    if meta_source:
        parts.append(f"Metadata sources that found results: {meta_source}")

    return "\n".join(parts)


def _build_identify_prompt(row: Dict[str, str]) -> str:
    """Build the prompt for AI track identification from CSV row data."""
    track_info = _gather_track_context(row)

    return (
        "You are a music track identification expert specializing in electronic "
        "and dance music. Your job is to determine the correct artist name, track "
        "title, version/remix information, and release year based on all available clues.\n\n"
        f"Available information about this track:\n{track_info}\n\n"
        "IDENTIFICATION RULES:\n"
        "1. The filename is often the strongest clue. DJ naming convention: "
        "\"Artist - Title (Remix/Edit).ext\"\n"
        "2. \"w/\" or \"w_\" in filenames means \"featuring\" — format as \"feat.\"\n"
        "3. SoundCloud uploader is NOT necessarily the artist — it could be a repost "
        "channel, label, remixer, or fan upload\n"
        "4. For remixes, the version field should contain the remix info: "
        "\"Remixer Name Remix\" (without parentheses)\n"
        "5. Featuring artists go in the title: \"Track Title feat. Artist B\"\n"
        "6. Use proper capitalization (Title Case for names and titles)\n"
        "7. If you cannot determine a field with reasonable confidence, return an "
        "empty string for that field\n"
        "8. Year should be the original release year. If uncertain, leave empty\n"
        "9. If original audio tags and filename disagree, prefer the more "
        "complete/structured source\n"
        "10. Separate the main title from version info — don't include remix/edit "
        "in the title field\n\n"
        "Respond ONLY with valid JSON (no markdown, no code fences):\n"
        '{"artist": "<Artist Name>", "title": "<Track Title>", '
        '"version": "<Remix/Edit info or empty>", "year": "<YYYY or empty>", '
        '"confidence": <0.0-1.0>, '
        '"reasoning": "<1-2 sentences explaining your identification>"}'
    )


@app.route("/api/identify-track", methods=["POST"])
def api_identify_track():
    """Ask OpenAI to identify a track's artist, title, version, and year.

    Loads all available metadata from CSV (filename, tags, BPM, key, online
    sources) and builds context for the AI to identify the track.

    Request body (JSON):
        { "track_id": "..." }

    Returns:
        { "artist": "...", "title": "...", "version": "...", "year": "...",
          "confidence": 0.8, "reasoning": "..." }
    """
    api_key = get_openai_api_key()
    if not api_key:
        return jsonify({"error": "OpenAI API key not configured. Add openai_api_key to config.local.yml"}), 501

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    tid = data.get("track_id", "")
    if not tid:
        return jsonify({"error": "Missing track_id"}), 400

    # Load row from CSV first so we can build the composite cache key
    with _CSV_LOCK:
        rows = load_unsorted_rows(UNSORTED_CSV)

    row = None
    for r in rows:
        if r.get("track_id") == tid:
            row = r
            break

    if not row:
        return jsonify({"error": f"Track not found: {tid}"}), 404

    # Cache key includes artist+title so edits to those fields correctly miss
    # the cache (track_id is UUID5 from file hash — unchanged by edits).
    cache_key = f"{tid}|{(row.get('artist') or '').lower()}|{(row.get('title') or '').lower()}"
    if cache_key in _identify_cache:
        return jsonify(_identify_cache[cache_key])

    # Build prompt with all available context
    prompt = _build_identify_prompt(row)

    try:
        result = _call_openai_responses_json(api_key, prompt, max_tokens=400)
        _identify_cache[cache_key] = result
        return jsonify(result)
    except Exception as e:
        _log.warning("OpenAI identify error: %s", e)
        return jsonify({"error": f"AI request failed: {e}"}), 502


# ── Unified AI Classify (identify + genre in one call) ───────────────────────

_classify_cache: Dict[str, Dict[str, Any]] = {}


@app.route("/api/ai-classify", methods=["POST"])
def api_ai_classify():
    """Unified AI classification: artist, title, version[], genre in one call.

    Request body (JSON):
        { "track_id": "..." }

    Returns:
        { "artist": "...", "title": "...", "version": ["Token1", "Token2"],
          "genre": "Tech House", "confidence": 0.85, "reasoning": "..." }
    """
    api_key = get_openai_api_key()
    if not api_key:
        return jsonify({"error": "OpenAI API key not configured"}), 501

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    tid = data.get("track_id", "")
    if not tid:
        return jsonify({"error": "Missing track_id"}), 400

    # Determine source CSV (library-review or unsorted)
    source = data.get("source", "unsorted")

    # Load row from CSV
    with _CSV_LOCK:
        if source in ("library-review", "library-fix"):
            rows = load_unsorted_rows(LIBRARY_REVIEW_CSV)
        else:
            rows = load_unsorted_rows(UNSORTED_CSV)

    row = None
    for r in rows:
        if r.get("track_id") == tid:
            row = r
            break

    if not row:
        return jsonify({"error": f"Track not found: {tid}"}), 404

    # Composite cache key: artist+title edits must miss the cache
    cache_key = f"{tid}|{(row.get('artist') or '').lower()}|{(row.get('title') or '').lower()}"
    if cache_key in _classify_cache:
        return jsonify(_classify_cache[cache_key])

    try:
        from djlib.ai_classify import classify_track
        # For library-review, exclude the file's ID3 genre tag which is often
        # a bulk-applied generic value (e.g. "Afro House" for 75% of tracks).
        # External sources (Beatport, SoundCloud, Last.fm) are always kept.
        exclude_file_tag = source in ("library-review", "library-fix")
        web_search = bool(data.get("web_search", False))
        result = classify_track(
            row,
            api_key=api_key,
            exclude_file_genre_tag=exclude_file_tag,
            use_web_search=web_search,
        )
        _classify_cache[cache_key] = result
        # Strip internal _usage from API response
        result_safe = {k: v for k, v in result.items() if not k.startswith("_")}
        return jsonify(result_safe)
    except Exception as e:
        _log.warning("AI classify error: %s", e)
        return jsonify({"error": f"AI request failed: {e}"}), 502


# ── AI Chat ──────────────────────────────────────────────────────────────────

# Session storage with TTL. Each entry: {"messages": [...], "last_access": float}
_chat_sessions: Dict[str, Dict[str, Any]] = {}
_CHAT_SESSION_TTL = 3600  # 1 hour
_CHAT_MAX_SESSIONS = 100  # max concurrent sessions (LRU eviction)


def _cleanup_chat_sessions() -> None:
    """Remove expired sessions and enforce LRU cap."""
    now = time.time()
    # Remove expired
    expired = [k for k, v in _chat_sessions.items()
               if now - v["last_access"] > _CHAT_SESSION_TTL]
    for k in expired:
        del _chat_sessions[k]
    # LRU eviction if over cap
    if len(_chat_sessions) > _CHAT_MAX_SESSIONS:
        sorted_keys = sorted(_chat_sessions, key=lambda k: _chat_sessions[k]["last_access"])
        for k in sorted_keys[:len(_chat_sessions) - _CHAT_MAX_SESSIONS]:
            del _chat_sessions[k]


def _build_chat_system_prompt(row: Dict[str, str]) -> str:
    """Build system prompt for AI chat about a specific track."""
    track_info = _gather_track_context(row)
    genre_labels = _load_genres()
    genre_list = ", ".join(genre_labels)

    return (
        "You are a DJ music metadata assistant with web search capabilities. "
        "You help identify and classify tracks in a DJ's music library. You are "
        "having a conversation with an experienced DJ who may correct your "
        "initial analysis.\n\n"
        "You have access to web search. USE IT proactively when:\n"
        "- The track cannot be confidently identified from metadata alone\n"
        "- The user asks you to search or look up a track\n"
        "- You need to verify release year, remix credits, or artist spelling\n"
        "- Genre classification is uncertain and online sources could help\n"
        "Do NOT search when you already have high-confidence metadata.\n\n"
        f"Available information about this track:\n{track_info}\n\n"
        "IMPORTANT: The folder name where a track is stored is NOT a reliable genre indicator. "
        "Folders are used for file organization only. Base your genre analysis on BPM, "
        "audio tags, online metadata sources, and artist/track knowledge instead.\n\n"
        "FORMATTING RULES:\n"
        "1. DJ naming convention: Artist - Title (Version/Remix)\n"
        "2. For mashups/edits combining multiple tracks: the edit creator is the artist, "
        "combined track names form the title. Example: "
        "\"Loup Musa\" artist, \"Ethnica x We Dem Boyz\" title, \"Edit\" version\n"
        "3. For remixes: original artist is the artist, version contains remixer name\n"
        "4. \"feat.\" for featuring artists, goes in the title\n"
        "5. Use Title Case for names and titles\n"
        "6. \"x\" between track names means mashup/combined\n\n"
        f"Valid genres (pick EXACTLY from this list): {genre_list}\n\n"
        "ALWAYS end your message with a suggestion JSON block when your reply "
        "contains ANY factual metadata (year, artist name, title, genre, version). "
        "Even for simple questions like 'year?' — give a short answer AND the block.\n"
        "Include ONLY the fields you want to change:\n"
        '```suggestion\n'
        '{"artist": "...", "title": "...", "version_info": "...", "year": "...", "genre": "..."}\n'
        '```\n'
        "Omit fields you are not changing. Use \"version_info\" (not \"version\") for version/remix info. "
        "For genre, use EXACT name from the list above.\n"
        "Only skip the suggestion block for purely conversational replies that don't "
        "reference specific metadata values.\n"
        "Keep responses concise (1-2 sentences + suggestion block)."
    )


def _call_openai_chat(
    api_key: str,
    messages: List[Dict[str, str]],
    max_tokens: int = 400,
) -> Dict[str, Any]:
    """Call OpenAI Responses API with web search and full message history.

    Uses the Responses API (``/v1/responses``) with the ``web_search_preview``
    tool so the model can look up track information online when its training
    data is insufficient.

    Returns a dict with keys:
        ``text``  – the assistant's reply text
        ``web_search_used`` – whether web search was invoked
        ``annotations`` – list of URL citations ``[{url, title}, ...]``
    """
    # Separate system prompt → instructions parameter (Responses API convention)
    instructions = None
    conversation: List[Dict[str, str]] = []
    for msg in messages:
        if msg["role"] == "system":
            instructions = msg["content"]
        else:
            conversation.append(msg)

    payload: Dict[str, Any] = {
        "model": get_ai_chat_model(),
        "input": conversation,
        "tools": [{"type": "web_search_preview"}],
        "temperature": 0.3,
        "max_output_tokens": max_tokens,
    }
    if instructions:
        payload["instructions"] = instructions

    resp = http_requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()

    data = resp.json()

    # Extract reply text.  The top-level ``output_text`` convenience field is
    # sometimes empty for web-search responses, so we also dig into the nested
    # ``output → message → content → output_text`` structure as a fallback.
    reply_text = (data.get("output_text") or "").strip()
    if not reply_text:
        for item in data.get("output", []):
            if item.get("type") == "message":
                for content_block in item.get("content", []):
                    if content_block.get("type") == "output_text":
                        reply_text = (content_block.get("text") or "").strip()
                        if reply_text:
                            break
            if reply_text:
                break

    # Strip markdown link citations that the model injects for web-search
    # sources, e.g. "([music.apple.com](https://...))".  We already surface
    # these as separate clickable source chips in the frontend.
    reply_text = re.sub(
        r'\s*\(\[([^\]]*)\]\([^)]*\)\)\s*',
        ' ',
        reply_text,
    ).strip()
    # Also handle bare markdown links: [text](url)
    reply_text = re.sub(
        r'\[([^\]]*)\]\([^)]*\)',
        r'\1',
        reply_text,
    )

    # Detect if web search was used and extract citations
    web_search_used = False
    annotations: List[Dict[str, str]] = []
    for item in data.get("output", []):
        if item.get("type") == "web_search_call":
            web_search_used = True
        if item.get("type") == "message":
            for content_block in item.get("content", []):
                for ann in content_block.get("annotations", []):
                    if ann.get("type") == "url_citation":
                        annotations.append({
                            "url": ann.get("url", ""),
                            "title": ann.get("title", ""),
                        })

    return {
        "text": reply_text,
        "web_search_used": web_search_used,
        "annotations": annotations,
    }


def _parse_suggestion_block(text: str) -> Optional[Dict[str, str]]:
    """Extract suggestion JSON from AI response if present.

    Looks for ```suggestion ... ``` or ```json ... ``` fenced blocks.
    Normalizes 'version' key to 'version_info' for CSV compatibility.
    """
    # Try ```suggestion ... ``` first, then ```json ... ```
    # (no generic ``` fallback — avoids false positives from code examples)
    patterns = [
        r'```suggestion\s*\n(.*?)\n\s*```',
        r'```json\s*\n(.*?)\n\s*```',
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(1).strip())
                if isinstance(obj, dict):
                    # Normalize version → version_info (AI may use either)
                    if "version" in obj and "version_info" not in obj:
                        obj["version_info"] = obj.pop("version")
                    # Validate genre if present
                    if "genre" in obj:
                        genre_labels = _load_genres()
                        if obj["genre"] not in genre_labels:
                            lower_map = {g.lower(): g for g in genre_labels}
                            matched = lower_map.get(obj["genre"].lower())
                            if matched:
                                obj["genre"] = matched
                    return obj
            except (json.JSONDecodeError, AttributeError):
                continue
    return None


@app.route("/api/ai-chat", methods=["POST"])
def api_ai_chat():
    """Conversational AI assistant for refining track metadata.

    Maintains per-track conversation history. The system prompt includes
    all available track context. User can ask follow-up questions,
    correct the AI, or request genre re-evaluation.

    Request body (JSON):
        { "track_id": "...", "message": "..." }
        or to reset:
        { "track_id": "...", "reset": true }

    Returns:
        { "reply": "...", "suggestion": { "artist": "...", ... } | null,
          "history_length": 3 }
    """
    api_key = get_openai_api_key()
    if not api_key:
        return jsonify({"error": "OpenAI API key not configured"}), 501

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    tid = data.get("track_id", "")
    if not tid:
        return jsonify({"error": "Missing track_id"}), 400

    # Handle reset
    if data.get("reset"):
        _chat_sessions.pop(tid, None)
        return jsonify({"ok": True, "history_length": 0})

    # Periodic cleanup
    _cleanup_chat_sessions()

    user_msg = (data.get("message") or "").strip()
    if not user_msg:
        return jsonify({"error": "Empty message"}), 400

    # Load row from CSV for system prompt context
    with _CSV_LOCK:
        rows = load_unsorted_rows(UNSORTED_CSV)

    row = None
    for r in rows:
        if r.get("track_id") == tid:
            row = r
            break

    if not row:
        # Track deleted while chatting
        _chat_sessions.pop(tid, None)
        return jsonify({"error": "Track no longer exists in unsorted.csv"}), 404

    # Build current system prompt (reflects latest field edits)
    system_prompt = _build_chat_system_prompt(row)

    # Get or create session
    if tid not in _chat_sessions:
        _chat_sessions[tid] = {
            "messages": [{"role": "system", "content": system_prompt}],
            "last_access": time.time(),
        }
    else:
        # Refresh system prompt to reflect any field edits made via table
        _chat_sessions[tid]["messages"][0] = {"role": "system", "content": system_prompt}
        _chat_sessions[tid]["last_access"] = time.time()

    session = _chat_sessions[tid]["messages"]

    # Cap conversation length (keep system + last 18 user/assistant messages)
    if len(session) > 20:
        session = [session[0]] + session[-18:]
        _chat_sessions[tid]["messages"] = session

    # Add user message
    session.append({"role": "user", "content": user_msg})

    try:
        result = _call_openai_chat(api_key, session)
        reply = result["text"]
        session.append({"role": "assistant", "content": reply})
        _chat_sessions[tid]["last_access"] = time.time()

        # Parse suggestion block from reply
        suggestion = _parse_suggestion_block(reply)

        # Clean display text (remove suggestion block for UI)
        display_text = reply
        for pattern in [
            r'\n*```suggestion\s*\n.*?\n\s*```\s*',
            r'\n*```json\s*\n.*?\n\s*```\s*',
        ]:
            display_text = re.sub(pattern, '', display_text, flags=re.DOTALL)
        display_text = display_text.strip()

        response_data: Dict[str, Any] = {
            "reply": display_text,
            "suggestion": suggestion,
            "history_length": len(session) - 1,  # exclude system prompt
        }
        if result["web_search_used"]:
            response_data["web_search"] = True
            if result["annotations"]:
                response_data["sources"] = result["annotations"]

        return jsonify(response_data)
    except Exception as e:
        # Remove the failed user message
        session.pop()
        _log.warning("AI chat error: %s", e)
        return jsonify({"error": f"AI request failed: {e}"}), 502


# ── Re-enrich (context menu) ─────────────────────────────────────────────────

_enrich_lock = threading.Lock()


def _classify_genre(
    artist: str,
    title: str,
    *,
    version: str = "",
    bpm: str = "",
    key: str = "",
    filename: str = "",
):
    """Lazy wrapper for the production genre_classifier (nano+WS+LF).

    Same pipeline that ``enrich-online`` (workflow 2) uses, so manual
    re-enrich in REVIEW produces identical results for the same inputs.
    """
    from djlib.metadata.genre_classifier import classify_genre
    return classify_genre(
        artist=artist,
        title=title,
        version=version,
        bpm=bpm,
        key=key,
        filename=filename,
    )


def _detect_artist_title_swap(row: Dict[str, str]) -> Optional[Dict[str, str]]:
    """Detect if artist and title might be swapped based on filename parsing.

    Returns a dict with swap suggestion if detected, None otherwise.
    """
    file_path = row.get("file_path", "")
    if not file_path:
        return None

    p = Path(file_path).expanduser()
    if not p.suffix:
        return None

    fn_artist, fn_title, _fn_version = parse_from_filename(p)
    if not fn_artist or not fn_title:
        return None

    current_artist = (row.get("artist") or "").strip().lower()
    current_title = (row.get("title") or "").strip().lower()
    fn_artist_low = fn_artist.strip().lower()
    fn_title_low = fn_title.strip().lower()

    if not current_artist or not current_title:
        return None

    # Check if current values are swapped vs filename parsing
    # i.e., current_artist matches fn_title AND current_title matches fn_artist
    artist_matches_fn_title = (
        current_artist == fn_title_low
        or fn_title_low.startswith(current_artist)
        or current_artist.startswith(fn_title_low)
    )
    title_matches_fn_artist = (
        current_title == fn_artist_low
        or fn_artist_low.startswith(current_title)
        or current_title.startswith(fn_artist_low)
    )

    if artist_matches_fn_title and title_matches_fn_artist:
        return {
            "swapped": True,
            "suggested_artist": row.get("title", "").strip(),
            "suggested_title": row.get("artist", "").strip(),
            "reason": f"Filename suggests: {fn_artist} — {fn_title}",
        }

    return None


@app.route("/api/scrape-url", methods=["POST"])
def api_scrape_url():
    """Scrape metadata from a URL (SoundCloud, YouTube, Beatport, etc.).

    Extracts artist, title, version, genre, year from the linked page.
    For SoundCloud (JS-rendered) falls back to URL slug parsing.

    Request body (JSON):
        { "track_id": "...", "url": "https://soundcloud.com/..." }

    Returns:
        { "artist": "...", "title": "...", "version": "...",
          "genre": "...", "year": "...", "source": "soundcloud",
          "artwork_url": "...", "url": "..." }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    url = (data.get("url") or "").strip()
    tid = (data.get("track_id") or "").strip()

    if not url:
        return jsonify({"error": "Missing url"}), 400

    try:
        from djlib.metadata.url_scraper import scrape_url
        result = scrape_url(url)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        _log.error("URL scrape failed: %s", e)
        return jsonify({"error": f"Failed to scrape URL: {e}"}), 502

    # If track_id provided, save scraped data to CSV
    if tid and (result.get("artist") or result.get("title")):
        with _CSV_LOCK, csv_lock(UNSORTED_CSV):
            rows = load_unsorted_rows(UNSORTED_CSV)
            for r in rows:
                if r.get("track_id") == tid:
                    changed = False
                    if result.get("artist") and not (r.get("artist_suggest") or "").strip():
                        r["artist_suggest"] = result["artist"]
                        r["artist"] = result["artist"]
                        changed = True
                    if result.get("title"):
                        r["title_suggest"] = result["title"]
                        r["title"] = result["title"]
                        changed = True
                    if result.get("version"):
                        r["version_suggest"] = result["version"]
                        r["version_info"] = result["version"]
                        changed = True
                    if result.get("genre") and not (r.get("genre_suggest") or "").strip():
                        r["genre_suggest"] = result["genre"]
                        changed = True
                    if result.get("year") and not (r.get("year_suggest") or "").strip():
                        r["year_suggest"] = result["year"]
                        changed = True
                    if result.get("url"):
                        r["source_url"] = result["url"]
                        changed = True
                    if changed:
                        r["meta_source"] = f"url_scrape({result.get('source', 'generic')})"
                        write_unsorted_rows(UNSORTED_CSV, rows, [])
                    break

    return jsonify(result)


@app.route("/api/enrich-track", methods=["POST"])
def api_enrich_track():
    """Re-enrich a single track using user-edited artist/title values.

    Uses the production WS-based classifier (same path as workflow 2) to find
    genre based on the CURRENT artist/title in the CSV (which may have been
    edited by the user in the UI).

    Request body (JSON):
        { "track_id": "...", "fields": ["genre", "year"] }   # fields defaults to all

    The optional `fields` list limits which sources are queried.  Currently
    recognised values: "genre", "year", "artist", "title", "version_info".
    Unknown field names are silently ignored.  When omitted, all fields are
    enriched (backward-compatible behaviour).

    Returns:
        { "genre": "Afro House", "genre_full": "Afro House, Deep House",
          "confidence": 0.85, "sources": ["ai_classifier(nano+WS+LF)"],
          "year": "2024",
          "swap_suggestion": { "swapped": true, ... } | null }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    tid = data.get("track_id", "")
    requested_fields: Optional[List[str]] = data.get("fields")  # None = all
    if not tid:
        return jsonify({"error": "Missing track_id"}), 400

    # Load current row from CSV (with user edits)
    with _CSV_LOCK:
        rows = load_unsorted_rows(UNSORTED_CSV)

    row = None
    for r in rows:
        if r.get("track_id") == tid:
            row = r
            break

    if not row:
        return jsonify({"error": f"Track not found: {tid}"}), 404

    # Use user-edited values (NOT artist_suggest which may be stale)
    artist = (row.get("artist") or "").strip()
    title = (row.get("title") or "").strip()
    version = (row.get("version_info") or "").strip()

    if not artist and not title:
        return jsonify({"error": "No artist or title to search for"}), 400

    # Check for swap suggestion
    swap_suggestion = _detect_artist_title_swap(row)

    # Parse duration from CSV
    dur_s: Optional[int] = None
    dur_str = (row.get("duration_suggest") or "").strip()
    if dur_str:
        try:
            parts = dur_str.split(":")
            if len(parts) == 2:
                dur_s = int(parts[0]) * 60 + int(parts[1])
        except Exception:
            pass

    # Run genre resolver in a thread-safe way
    if not _enrich_lock.acquire(timeout=0):
        return jsonify({"error": "Another enrichment is in progress, try again"}), 429

    try:
        _log.info("Re-enriching: %s - %s (%s)", artist, title, version)
        bpm_str = (row.get("bpm") or row.get("tag_bpm_original") or "").strip()
        key_str = (row.get("key_camelot") or row.get("tag_key_original") or "").strip()
        fp = row.get("file_path", "")
        filename_hint = Path(fp).name if fp else ""

        cls = _classify_genre(
            artist, title,
            version=version,
            bpm=bpm_str,
            key=key_str,
            filename=filename_hint,
        )
    except Exception as e:
        _log.warning("Enrich failed: %s", e)
        return jsonify({"error": f"Enrichment failed: {e}"}), 502
    finally:
        _enrich_lock.release()

    genre_main = (cls.get("genre") or "").strip()
    confidence = float(cls.get("confidence") or 0.0)
    classifier_source = cls.get("source", "nano+WS+LF")

    if not genre_main or confidence < 0.01:
        return jsonify({
            "genre": None,
            "genre_full": None,
            "confidence": 0,
            "sources": [],
            "swap_suggestion": swap_suggestion,
        })

    # Classifier produces one canonical label (no subs), so genre_full == genre.
    genre_full = genre_main
    sources = [f"ai_classifier({classifier_source})"]

    # Display breakdown: show the classifier's reasoning and raw Last.fm tags.
    source_details: Dict[str, str] = {}
    reasoning = (cls.get("reasoning") or "").strip()
    if reasoning:
        source_details["classifier"] = reasoning[:200]
    lf_tags_raw = (cls.get("lastfm_tags") or "").strip()
    if lf_tags_raw:
        source_details["lastfm"] = lf_tags_raw[:200]

    # Per-source CSV columns: classifier only surfaces Last.fm tags directly.
    # Other per-source columns (genres_beatport, genres_musicbrainz, etc.) are
    # owned by batch enrichment and intentionally left untouched here.
    source_genres: Dict[str, str] = {}
    if lf_tags_raw:
        lf_top = [name.strip() for name in re.split(r',\s*', lf_tags_raw) if name.strip()][:5]
        if lf_top:
            source_genres["genres_lastfm"] = ", ".join(s.split(" (")[0] for s in lf_top)

    meta_source = f"ai_classifier({classifier_source})"
    year = (cls.get("year") or "").strip() or None
    year_evidence = (cls.get("year_evidence") or "").strip()
    if year_evidence:
        source_details["year"] = year_evidence[:200]

    return jsonify({
        "genre": genre_main,
        "genre_full": genre_full,
        "confidence": round(confidence, 3),
        "sources": sources,
        "source_details": source_details,
        "source_genres": source_genres,
        "meta_source": meta_source,
        "year": year,
        "swap_suggestion": swap_suggestion,
    })


@app.route("/api/swap-artist-title", methods=["POST"])
def api_swap_artist_title():
    """Swap artist and title fields for a track, recalculating version_info.

    Instead of a naive field swap, this re-parses the filename to correctly
    separate artist, title, and version.  When the filename has reversed
    order (e.g. ``Title (Remix - Extended) - Artist``), the parser auto-detects
    this and returns all three fields correctly.

    Request body (JSON):
        { "track_id": "..." }

    Returns:
        { "ok": true, "artist": "...", "title": "...", "version_info": "..." }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    tid = data.get("track_id", "")
    if not tid:
        return jsonify({"error": "Missing track_id"}), 400

    with _CSV_LOCK, csv_lock(UNSORTED_CSV):
        rows = load_unsorted_rows(UNSORTED_CSV)
        found = False
        new_artist = ""
        new_title = ""
        new_version = ""
        for row in rows:
            if row.get("track_id") == tid:
                file_path = (row.get("file_path") or "").strip()

                # Strategy: re-parse from filename to get correct artist/title/version.
                # The parser has swap detection built-in, so if the filename has
                # reversed order it will produce the correct result.
                # We only do a naive swap as fallback when there's no file_path.
                if file_path:
                    p = Path(file_path).expanduser()
                    fn_a, fn_t, fn_v = parse_from_filename(p)

                    old_artist = (row.get("artist") or "").strip()
                    old_title = (row.get("title") or "").strip()

                    # If parser result matches current values, do a simple swap
                    # (user explicitly wants to override the parser)
                    if (fn_a.lower() == old_artist.lower()
                            and fn_t.lower() == old_title.lower()):
                        new_artist = old_title
                        new_title = old_artist
                        # Re-extract version from the new title if it contains
                        # version-like content in parentheses
                        from djlib.filename import split_title_and_version
                        base_t, ver = split_title_and_version(new_title)
                        if ver:
                            new_title = base_t
                            new_version = ver
                        else:
                            new_version = (row.get("version_info") or "").strip()
                    else:
                        # Parser result differs from current values — use parser
                        # (this handles the case where initial parse was broken)
                        new_artist = fn_a or old_title
                        new_title = fn_t or old_artist
                        new_version = fn_v
                else:
                    # No file_path — naive swap
                    old_artist = (row.get("artist") or "").strip()
                    old_title = (row.get("title") or "").strip()
                    new_artist = old_title
                    new_title = old_artist
                    new_version = (row.get("version_info") or "").strip()

                row["artist"] = new_artist
                row["title"] = new_title
                row["version_info"] = new_version
                # Also swap suggest fields if they exist
                old_as = (row.get("artist_suggest") or "").strip()
                old_ts = (row.get("title_suggest") or "").strip()
                if old_as or old_ts:
                    row["artist_suggest"] = old_ts
                    row["title_suggest"] = old_as
                found = True
                break

        if not found:
            return jsonify({"error": f"Track not found: {tid}"}), 404

        write_unsorted_rows(UNSORTED_CSV, rows, [])

    return jsonify({
        "ok": True,
        "artist": new_artist,
        "title": new_title,
        "version_info": new_version,
    })


# ── Ghost-row review: batch enrich endpoints ─────────────────────────────────


_ENRICH_STEP_LABELS: Dict[str, str] = {
    "web_search": "Web search",
    "lastfm": "Last.fm tags",
    "classifying": "Classifying genre",
    "year_mb": "Year · MusicBrainz",
    "year_discogs": "Year · Discogs",
    "year_ai": "Year · AI",
    "identify": "Identifying artist/title",
    "writing": "Writing result",
}


def _enrich_one_for_batch(
    row: Dict[str, str],
    fields: List[str],
    job: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Worker: enrich a single row and write result to sidecar. Returns payload or None."""
    if job.get("cancelled"):
        return None

    tid = row.get("track_id", "")
    artist = (row.get("artist") or "").strip()
    title = (row.get("title") or "").strip()
    version = (row.get("version_info") or "").strip()

    if not artist and not title:
        return None

    def _report(step: str, sub: float) -> None:
        with _BATCH_JOBS_LOCK:
            job["current_track"] = f"{artist} — {title}"
            job["current_step"] = _ENRICH_STEP_LABELS.get(step, step)
            job["sub_progress"] = round(sub, 3)

    _report("web_search", 0.0)

    try:
        bpm_str = (row.get("bpm") or row.get("tag_bpm_original") or "").strip()
        key_str = (row.get("key_camelot") or row.get("tag_key_original") or "").strip()
        fp = row.get("file_path", "")
        filename_hint = Path(fp).name if fp else ""

        from djlib.metadata.genre_classifier import classify_genre
        cls = classify_genre(
            artist, title,
            version=version,
            bpm=bpm_str,
            key=key_str,
            filename=filename_hint,
            on_step=lambda step, sub: _report(step, sub),
        )
    except Exception as exc:
        _log.warning("Batch enrich failed for %s: %s", tid, exc)
        return None

    payload: Dict[str, Any] = {}

    # genre
    if "genre" in fields:
        genre_val = (cls.get("genre") or "").strip()
        if genre_val:
            payload["genre"] = {
                "value": genre_val,
                "source": f"ai_classifier:{cls.get('source', 'nano+WS+LF')}",
                "confidence": round(float(cls.get("confidence") or 0), 3),
                "was": (row.get("genre") or ""),
            }

    if "year" in fields:
        year_val = ""
        year_src = ""
        year_conf = 0.0

        # 1. MusicBrainz — most reliable, skip for remixes/edits (like enrich.py does)
        # Rate-limited via _MB_SEMAPHORE: MusicBrainz enforces max 1 req/s
        _report("year_mb", 0.88)
        if not version:
            with _MB_SEMAPHORE:
                try:
                    from djlib.metadata import mb_client
                    mb_info = mb_client.get_original_release_info(artist, title)
                    if mb_info:
                        mb_year, _album, _rg = mb_info
                        if mb_year:
                            year_val = mb_year
                            year_src = "musicbrainz"
                            year_conf = 0.92
                except Exception as exc:
                    _log.debug("MB year lookup failed for %s: %s", tid, exc)
                finally:
                    time.sleep(1.1)  # ensure 1 req/s across all workers

        # 2. Discogs — skip for remixes/edits: Discogs searches by original artist+title
        #    and returns the original release year, not the remix upload year.
        #    Only query when there is no version string (i.e. this is an original track).
        _report("year_discogs", 0.92)
        if not year_val and not version:
            with _DISCOGS_SEMAPHORE:
                try:
                    from djlib.metadata import discogs
                    dc_year = discogs.get_release_year(artist, title)
                    if dc_year:
                        year_val = dc_year
                        year_src = "discogs"
                        year_conf = 0.88
                except Exception as exc:
                    _log.debug("Discogs year lookup failed for %s: %s", tid, exc)
                finally:
                    time.sleep(2.5)  # ensure 25 req/min across all workers

        # 3. nano classifier (already called above).
        # When SearXNG was unavailable, the classifier used training knowledge —
        # cap confidence lower to signal uncertainty vs. web-backed results.
        if not year_val:
            cls_year = (cls.get("year") or "").strip()
            ws_was_used = bool(cls.get("lastfm_tags") or cls.get("year_evidence"))
            if cls_year:
                year_val = cls_year
                year_src = f"ai_classifier:{cls.get('source', 'nano+WS+LF')}"
                year_conf = 0.8 if ws_was_used else 0.6

        # 4. Last.fm fallback — skip for remixes (unreliable for remix release dates)
        if not year_val and not version:
            try:
                from djlib.metadata import lastfm
                lf = lastfm.track_info(artist, title)
                if lf.get("year"):
                    year_val = str(lf["year"]).strip()
                    year_src = "lastfm"
                    year_conf = 0.75
            except Exception as exc:
                _log.debug("Last.fm year lookup failed for %s: %s", tid, exc)

        if year_val:
            payload["year"] = {
                "value": year_val,
                "source": year_src,
                "confidence": year_conf,
                "was": (row.get("year") or ""),
            }

    # artist / title / version_info — from identify if requested
    _report("identify", 0.96)
    id_needed = any(f in fields for f in ("artist", "title", "version_info"))
    if id_needed:
        api_key = get_openai_api_key()
        if api_key:
            try:
                prompt = _build_identify_prompt(row)
                id_result = _call_openai_responses_json(api_key, prompt, max_tokens=300)
                for f in ("artist", "title"):
                    if f in fields:
                        val = (id_result.get(f) or "").strip()
                        if val:
                            payload[f] = {
                                "value": val,
                                "source": "ai_identify",
                                "confidence": round(float(id_result.get("confidence") or 0.7), 3),
                                "was": (row.get(f) or ""),
                            }
                if "version_info" in fields:
                    ver_val = (id_result.get("version") or "").strip()
                    if ver_val:
                        payload["version_info"] = {
                            "value": ver_val,
                            "source": "ai_identify",
                            "confidence": round(float(id_result.get("confidence") or 0.7), 3),
                            "was": (row.get("version_info") or ""),
                        }
            except Exception as exc:
                _log.warning("Batch identify failed for %s: %s", tid, exc)

    _report("writing", 0.99)
    if payload:
        _sidecar_set(tid, payload)

    with _BATCH_JOBS_LOCK:
        job["done"] += 1
        job["sub_progress"] = 0.0
        job["current_step"] = ""
        job["results"][tid] = {"fields": list(payload.keys()), "ok": True}

    return payload


@app.route("/api/enrich-batch", methods=["POST"])
def api_enrich_batch():
    """Start an async batch enrich job for N selected tracks.

    Request body (JSON):
        { "track_ids": ["...", ...], "fields": ["genre", "year", "artist", "title", "version_info"] }

    Returns:
        { "job_id": "...", "total": N }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    track_ids: List[str] = data.get("track_ids") or []
    fields: List[str] = data.get("fields") or ["genre", "year", "artist", "title", "version_info"]

    if not track_ids:
        return jsonify({"error": "No track_ids provided"}), 400

    # Load rows once for all workers
    with _CSV_LOCK:
        all_rows = load_unsorted_rows(UNSORTED_CSV)
    tid_to_row = {r.get("track_id", ""): r for r in all_rows if r.get("track_id")}

    rows_to_enrich = [tid_to_row[tid] for tid in track_ids if tid in tid_to_row]
    if not rows_to_enrich:
        return jsonify({"error": "None of the track_ids found in unsorted.csv"}), 404

    # GC: remove finished jobs to prevent unbounded memory growth
    with _BATCH_JOBS_LOCK:
        finished = [jid for jid, j in _BATCH_JOBS.items() if j.get("state") in ("done", "cancelled")]
        for jid in finished:
            del _BATCH_JOBS[jid]

    job_id = str(uuid.uuid4())
    job: Dict[str, Any] = {
        "id": job_id,
        "total": len(rows_to_enrich),
        "done": 0,
        "cancelled": False,
        "state": "running",
        "results": {},
        "current_track": "",
        "current_step": "",
        "sub_progress": 0.0,
    }

    with _BATCH_JOBS_LOCK:
        _BATCH_JOBS[job_id] = job

    def _run_job() -> None:
        _ensure_searxng_running()
        futures = [
            _BATCH_EXECUTOR.submit(_enrich_one_for_batch, row, fields, job)
            for row in rows_to_enrich
        ]
        concurrent.futures.wait(futures)
        with _BATCH_JOBS_LOCK:
            job["state"] = "cancelled" if job.get("cancelled") else "done"

    threading.Thread(target=_run_job, daemon=True, name=f"enrich-batch-{job_id[:8]}").start()

    return jsonify({"job_id": job_id, "total": len(rows_to_enrich)})


@app.route("/api/enrich-status")
def api_enrich_status():
    """Poll batch enrich job progress.

    Query params: job_id
    Returns: { "job_id": ..., "done": N, "total": N, "state": "running"|"done"|"cancelled",
               "new_results": {track_id: {fields: [...], ok: bool}} }
    """
    job_id = request.args.get("job_id", "")
    with _BATCH_JOBS_LOCK:
        job = _BATCH_JOBS.get(job_id)

    if not job:
        return jsonify({"error": f"Unknown job_id: {job_id}"}), 404

    with _BATCH_JOBS_LOCK:
        snapshot = {
            "job_id": job_id,
            "done": job["done"],
            "total": job["total"],
            "state": job["state"],
            "new_results": dict(job["results"]),
            "current_track": job.get("current_track", ""),
            "current_step": job.get("current_step", ""),
            "sub_progress": job.get("sub_progress", 0.0),
        }
        # Delta delivery: clear results after sending so next poll only gets new ones
        job["results"] = {}

    return jsonify(snapshot)


@app.route("/api/enrich-cancel", methods=["POST"])
def api_enrich_cancel():
    """Cancel a running batch enrich job.

    Request body (JSON): { "job_id": "..." }
    """
    data = request.get_json(silent=True)
    job_id = (data or {}).get("job_id", "")
    with _BATCH_JOBS_LOCK:
        job = _BATCH_JOBS.get(job_id)
        if job:
            job["cancelled"] = True
            job["state"] = "cancelled"
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/pending-suggestions")
def api_pending_suggestions():
    """Return current sidecar contents for the frontend to hydrate ghost rows on load.

    Returns: { track_id: { field: {value, source, confidence, was}, _ts: float } }
    """
    return jsonify(_read_sidecar())


@app.route("/api/apply-enrichment", methods=["POST"])
def api_apply_enrichment():
    """Write accepted fields from ghost-row review back to unsorted.csv.

    Request body (JSON):
        { "applications": [ { "track_id": "...", "fields": {"genre": true, "year": false, ...} } ] }

    For each accepted field:
      - Writes the proposed value to the canonical CSV column
      - Updates field_sources JSON column with provenance
      - Removes the track's entry from the pending-suggestions sidecar

    Returns: { "ok": true, "applied": N }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    applications: List[Dict[str, Any]] = data.get("applications") or []
    if not applications:
        return jsonify({"error": "No applications provided"}), 400

    sidecar = _read_sidecar()
    applied_count = 0
    applied_tids: List[str] = []

    with _CSV_LOCK, csv_lock(UNSORTED_CSV):
        rows = load_unsorted_rows(UNSORTED_CSV)
        tid_to_idx: Dict[str, int] = {
            r.get("track_id", ""): i for i, r in enumerate(rows) if r.get("track_id")
        }

        for app_item in applications:
            tid = app_item.get("track_id", "")
            accepted_fields: Dict[str, bool] = app_item.get("fields") or {}
            if not tid or tid not in tid_to_idx:
                continue

            proposals = sidecar.get(tid, {})
            idx = tid_to_idx[tid]
            row = rows[idx]

            # Parse existing field_sources (may be JSON string or empty)
            try:
                fs: Dict[str, str] = json.loads(row.get("field_sources") or "{}")
            except (json.JSONDecodeError, TypeError):
                fs = {}

            for field, accepted in accepted_fields.items():
                if not accepted:
                    continue
                proposal = proposals.get(field)
                if not proposal:
                    continue
                value = proposal.get("value", "")
                source = proposal.get("source", "manual")

                row[field] = value
                fs[field] = source

                # Mirror genre → genre_suggest for the existing UI convention
                if field == "genre":
                    row["genre_suggest"] = value

            row["field_sources"] = json.dumps(fs, ensure_ascii=False) if fs else ""
            rows[idx] = row
            applied_tids.append(tid)
            applied_count += 1

        write_unsorted_rows(UNSORTED_CSV, rows, [])

    # Remove applied entries from sidecar outside the CSV lock
    if applied_tids:
        _sidecar_remove(applied_tids)

    return jsonify({"ok": True, "applied": applied_count})


@app.route("/api/dedup-staging", methods=["POST"])
def api_dedup_staging():
    """Deduplicate unsorted.csv by fingerprint / file hash.

    Safe to call while the UI is running — executes within Flask's request
    cycle, so no external process races against the file write.

    Request body (JSON, optional):
        { "dry_run": true }   # default false

    Response:
        { "ok": true, "removed": N, "total_before": N, "total_after": N,
          "duplicates": [{"winner": "...", "duplicate": "..."}] }
    """
    import json as _json
    data = request.get_json(silent=True) or {}
    dry_run = bool(data.get("dry_run", False)) or request.args.get("dry_run") == "true"

    rows = _load_unsorted()
    total_before = len(rows)

    seen_fps: Dict[str, int] = {}
    seen_hashes: Dict[str, int] = {}
    to_remove: List[int] = []
    report: List[Dict[str, str]] = []

    def _add_dup_path(winner_row: Dict[str, str], dup_path: str) -> None:
        existing = winner_row.get("duplicate_paths") or "[]"
        try:
            dup_list: list = _json.loads(existing) if existing.strip() else []
        except Exception:
            dup_list = []
        if dup_path and dup_path not in dup_list:
            dup_list.append(dup_path)
            winner_row["duplicate_paths"] = _json.dumps(dup_list)

    for i, row in enumerate(rows):
        fp = row.get("fingerprint") or ""
        fhash = row.get("file_hash") or ""
        fp_dup = fp and fp in seen_fps
        hash_dup = (not fp_dup) and bool(fhash) and fhash in seen_hashes

        if fp_dup or hash_dup:
            winner_idx = seen_fps[fp] if fp_dup else seen_hashes[fhash]
            dup_path = row.get("file_path") or ""
            _add_dup_path(rows[winner_idx], dup_path)
            to_remove.append(i)
            report.append({
                "winner": rows[winner_idx].get("file_path") or "",
                "duplicate": dup_path,
            })
        else:
            if fp:
                seen_fps[fp] = i
            if fhash:
                seen_hashes[fhash] = i

    if not dry_run and to_remove:
        for i in reversed(to_remove):
            rows.pop(i)
        _save_unsorted(rows)

    return jsonify({
        "ok": True,
        "dry_run": dry_run,
        "removed": len(to_remove),
        "total_before": total_before,
        "total_after": total_before - len(to_remove),
        "duplicates": report,
    })


# ── Artist normalization ─────────────────────────────────────────────────────

_ALIASES_PATH = _REPO / "data" / "artist_aliases.yml"


@app.route("/api/artist-clusters")
def api_artist_clusters() -> Response:
    from djlib.artist_normalizer import (
        collect_artists, cluster_artists, load_aliases, _split_compound, _normalize_key,
    )

    show_dismissed = request.args.get("show_dismissed", "0") == "1"
    library_rows = _load_library_csv()
    unsorted_rows = load_unsorted_rows(UNSORTED_CSV)
    aliases = load_aliases(_ALIASES_PATH)
    artists = collect_artists(library_rows, unsorted_rows)

    track_counts: Dict[str, int] = {}
    for row in list(library_rows) + list(unsorted_rows):
        raw = (row.get("artist") or "").strip()
        for atom in _split_compound(raw):
            key = _normalize_key(atom)
            track_counts[key] = track_counts.get(key, 0) + 1

    clusters = cluster_artists(artists, aliases, show_dismissed=show_dismissed)
    for c in clusters:
        c["track_count"] = sum(track_counts.get(_normalize_key(m), 0) for m in c["members"])

    return jsonify(clusters)


@app.route("/api/artist-clusters/merge", methods=["POST"])
def api_artist_clusters_merge() -> Response:
    from djlib.artist_normalizer import (
        write_pending_entry, promote_pending_to_canonical,
        write_artist_tags, write_audit_log, _normalize_key, _split_compound,
        _cluster_fingerprint,
    )
    from djlib.library_schema import load_library_csv, save_library_csv

    body = request.get_json(silent=True) or {}
    canonical = (body.get("canonical") or "").strip()
    variants = [v.strip() for v in (body.get("variants") or []) if str(v).strip()]
    apply_tags = bool(body.get("apply_tags", True))

    if not canonical or not variants:
        return jsonify({"ok": False, "error": "canonical and variants required"}), 400

    fingerprint = _cluster_fingerprint(variants)

    # Collect affected file paths from both CSVs
    variant_keys = {_normalize_key(v) for v in variants}
    affected_paths: List[str] = []
    for row in list(load_unsorted_rows(UNSORTED_CSV)) + list(_load_library_csv()):
        raw = (row.get("artist") or "").strip()
        atoms = _split_compound(raw)
        if any(_normalize_key(a) in variant_keys for a in atoms):
            p = row.get("file_path") or row.get("old_full_path") or ""
            if p:
                affected_paths.append(p)

    affected_paths = list(dict.fromkeys(affected_paths))  # deduplicate, preserve order

    # Write pending entry before any tag writes
    write_pending_entry(_ALIASES_PATH, fingerprint, canonical, variants)

    failed_tags: List[str] = []
    if apply_tags and affected_paths:
        failed_tags = write_artist_tags(affected_paths, canonical)
        if failed_tags:
            return jsonify({"ok": False, "error": f"{len(failed_tags)} tag write(s) failed", "failed_tags": failed_tags}), 500

    # Promote pending → canonical
    promote_pending_to_canonical(_ALIASES_PATH, fingerprint, canonical, variants)

    # Update unsorted.csv
    with _CSV_LOCK, csv_lock(UNSORTED_CSV):
        u_rows = load_unsorted_rows(UNSORTED_CSV)
        updated_unsorted = 0
        for row in u_rows:
            raw = (row.get("artist") or "").strip()
            atoms = _split_compound(raw)
            if any(_normalize_key(a) in variant_keys for a in atoms):
                row["artist"] = canonical
                updated_unsorted += 1
        if updated_unsorted:
            write_unsorted_rows(UNSORTED_CSV, u_rows, [])

    # Update library.csv
    lib_path = _REPO / "data" / "library.csv"
    updated_library = 0
    with _CSV_LOCK, csv_lock(lib_path):
        lib_rows = load_library_csv(lib_path)
        for row in lib_rows:
            raw = (row.get("artist") or "").strip()
            atoms = _split_compound(raw)
            if any(_normalize_key(a) in variant_keys for a in atoms):
                row["artist"] = canonical
                row["artist_normalized"] = "yes"
                updated_library += 1
        if updated_library:
            save_library_csv(lib_path, lib_rows)

    write_audit_log(
        LOGS_DIR, canonical, variants,
        tracks_affected=updated_unsorted + updated_library,
        method="manual",
        confidence=100,
    )

    return jsonify({
        "ok": True,
        "canonical": canonical,
        "updated_unsorted": updated_unsorted,
        "updated_library": updated_library,
        "failed_tags": failed_tags,
    })


@app.route("/api/artist-clusters/dismiss", methods=["POST"])
def api_artist_clusters_dismiss() -> Response:
    from djlib.artist_normalizer import dismiss_cluster

    body = request.get_json(silent=True) or {}
    members = [m.strip() for m in (body.get("members") or []) if str(m).strip()]
    if not members:
        return jsonify({"ok": False, "error": "members required"}), 400

    dismiss_cluster(_ALIASES_PATH, members)
    return jsonify({"ok": True})


# ── Playlists ─────────────────────────────────────────────────────────────────

@app.route("/api/playlists")
def api_playlists() -> Response:
    """Return sorted list of all playlist names currently in use across library.csv."""
    rows = _load_library_csv()
    names: set = set()
    for row in rows:
        raw = (row.get("playlists") or "").strip()
        if raw:
            for part in raw.split("|"):
                part = part.strip()
                if part:
                    names.add(part)
    return jsonify(sorted(names, key=str.lower))


@app.route("/api/track/<track_id>/playlists", methods=["POST"])
def api_track_playlists(track_id: str) -> Response:
    """Set the playlists for a track. Body: {"playlists": ["PornoStar", "BiA"], "source": "unsorted"|"library"}"""
    body = request.get_json(silent=True) or {}
    new_playlists: List[str] = [c.strip() for c in (body.get("playlists") or []) if str(c).strip()]
    for name in new_playlists:
        if "|" in name:
            return jsonify({"ok": False, "error": f"Playlist name may not contain '|': {name}"}), 400

    source = (body.get("source") or "library").strip()
    if source == "unsorted":
        with _CSV_LOCK, csv_lock(UNSORTED_CSV):
            rows = load_unsorted_rows(UNSORTED_CSV)
            found = False
            for row in rows:
                if row.get("track_id") == track_id or row.get("file_hash") == track_id:
                    row["playlists"] = "|".join(new_playlists)
                    found = True
                    break
            if not found:
                return jsonify({"ok": False, "error": "track not found"}), 404
            write_unsorted_rows(UNSORTED_CSV, rows, [])
    else:
        csv_path = _REPO / "data" / "library.csv"
        with csv_lock(csv_path):
            rows = _load_library_csv()
            found = False
            for row in rows:
                if row.get("track_id") == track_id:
                    row["playlists"] = "|".join(new_playlists)
                    found = True
                    break
            if not found:
                return jsonify({"ok": False, "error": "track not found"}), 404
            from djlib.library_schema import save_library_csv
            save_library_csv(csv_path, rows)

    return jsonify({"ok": True, "playlists": new_playlists})


# ── Scan ─────────────────────────────────────────────────────────────────────

_scan_lock = threading.Lock()
_scan_running = False


@app.route("/api/scan-start", methods=["POST"])
def api_scan_start() -> Response:
    """Start a background scan of the inbox folder.

    cmd_scan writes progress to LOGS/scan_status.json automatically.
    Poll /api/scan-status for progress.
    """
    global _scan_running
    with _scan_lock:
        if _scan_running:
            return jsonify({"error": "Scan already running"}), 409
        _scan_running = True

    # Reset status file immediately so the first poll sees a clean "running" state
    # (not stale data from a previous scan whose state="done" + processed=total).
    status_path = LOGS_DIR / "scan_status.json"
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with status_path.open("w", encoding="utf-8") as f:
            json.dump({"state": "running", "total": 0, "processed": 0, "added": 0}, f)
    except Exception:
        pass

    def _run() -> None:
        global _scan_running
        try:
            import argparse as _ap
            from djlib.cli import cmd_scan
            cmd_scan(_ap.Namespace(strict=False))
        except Exception as e:
            log.error("scan failed: %s", e)
            try:
                with status_path.open("w", encoding="utf-8") as f:
                    json.dump({"state": "error", "message": str(e)}, f)
            except Exception:
                pass
        finally:
            _scan_running = False

    threading.Thread(target=_run, daemon=True, name="scan").start()
    return jsonify({"ok": True})


@app.route("/api/scan-status")
def api_scan_status() -> Response:
    """Return current scan progress by reading scan_status.json."""
    status_path = LOGS_DIR / "scan_status.json"
    running = _scan_running
    if status_path.exists():
        try:
            with status_path.open(encoding="utf-8") as f:
                data = json.load(f)
            if running:
                data["state"] = "running"
            return jsonify(data)
        except Exception:
            pass
    return jsonify({
        "state": "running" if running else "idle",
        "total": 0,
        "processed": 0,
        "added": 0,
    })


# ── Export ────────────────────────────────────────────────────────────────────

_export_lock = threading.Lock()
_export_status: Dict[str, Any] = {"state": "idle"}


@app.route("/api/export-start", methods=["POST"])
def api_export_start() -> Response:
    """Move disposed tracks to library/mixes/reject and sync Rekordbox.

    Runs cmd_apply in a background thread. Poll /api/export-status for state.
    """
    global _export_status

    # Single atomic check-and-claim to prevent TOCTOU: two concurrent POSTs
    # both passing the first check then both launching cmd_apply in parallel.
    with _export_lock:
        if _export_status.get("state") in ("running", "starting"):
            return jsonify({"error": "Export already running"}), 409
        _export_status = {"state": "starting", "total": 0, "moved": 0, "message": ""}

    # I/O outside the lock — another request hitting here will see state="starting" and bail.
    rows = load_unsorted_rows(UNSORTED_CSV)
    ready_count = sum(
        1 for r in rows
        if (r.get("disposition") or "").lower().strip() in EXPORT_DISPOSITIONS
    )
    if ready_count == 0:
        with _export_lock:
            _export_status = {"state": "idle"}
        return jsonify({"error": "No tracks ready to export (set disposition first)"}), 400

    with _export_lock:
        _export_status = {
            "state": "running",
            "total": ready_count,
            "moved": 0,
            "message": "",
        }

    def _run() -> None:
        global _export_status
        try:
            import argparse as _ap
            from djlib.cli import cmd_apply
            cmd_apply(_ap.Namespace(dry_run=False))  # dry_run required — Namespace() raises AttributeError
            after_rows = load_unsorted_rows(UNSORTED_CSV)
            after_ready = sum(
                1 for r in after_rows
                if (r.get("disposition") or "").lower().strip() in EXPORT_DISPOSITIONS
            )
            moved = ready_count - after_ready
            with _export_lock:
                _export_status = {
                    "state": "done",
                    "total": ready_count,
                    "moved": moved,
                    "message": f"Exported {moved} of {ready_count} tracks",
                }
        except Exception as e:
            log.error("export failed: %s", e)
            with _export_lock:
                _export_status = {
                    "state": "error",
                    "total": ready_count,
                    "moved": 0,
                    "message": str(e),
                }

    threading.Thread(target=_run, daemon=True, name="export").start()
    return jsonify({"ok": True, "total": ready_count})


@app.route("/api/export-status")
def api_export_status() -> Response:
    """Poll export job status."""
    with _export_lock:
        return jsonify(dict(_export_status))


# ── Push Playlists ───────────────────────────────────────────────────────────

_push_lock = threading.Lock()
_push_status: Dict[str, Any] = {"state": "idle"}


@app.route("/api/push-playlists", methods=["POST"])
def api_push_playlists() -> Response:
    """Push djlib playlists field to Rekordbox. Rekordbox must be closed."""
    global _push_status
    with _push_lock:
        if _push_status.get("state") in ("running", "starting"):
            return jsonify({"error": "Push already running"}), 409
        _push_status = {"state": "running", "message": "Pushing playlists to Rekordbox…"}

    def _run() -> None:
        global _push_status
        try:
            from djlib.rekordbox_playlists import push_playlists
            result = push_playlists(library_csv_path=CSV_PATH, dry_run=False, only=None)
            total_tracks = sum(result.values())
            pl_count = len(result)
            msg = f"Pushed {pl_count} playlist{'s' if pl_count != 1 else ''}, {total_tracks} tracks"
            with _push_lock:
                _push_status = {
                    "state": "done",
                    "pushed": total_tracks,
                    "playlists": pl_count,
                    "message": msg,
                }
        except Exception as e:
            log.error("push-playlists failed: %s", e)
            with _push_lock:
                _push_status = {"state": "error", "message": str(e)}

    threading.Thread(target=_run, daemon=True, name="push-playlists").start()
    return jsonify({"ok": True})


@app.route("/api/push-playlists-status")
def api_push_playlists_status() -> Response:
    with _push_lock:
        return jsonify(dict(_push_status))


# ── Unapply ──────────────────────────────────────────────────────────────────

_unapply_lock = threading.Lock()
_unapply_status: Dict[str, Any] = {"state": "idle"}


@app.route("/api/unapply-last-run", methods=["POST"])
def api_unapply_last_run() -> Response:
    """Move all tracks from the most recent apply run back to unsorted."""
    global _unapply_status
    with _unapply_lock:
        if _unapply_status.get("state") in ("running", "starting"):
            return jsonify({"error": "Unapply already running"}), 409
        _unapply_status = {"state": "starting", "message": "Finding last apply run…"}
    # "starting" → "running" happens inside the thread; concurrent POSTs between
    # here and thread start are blocked by the "starting" guard above.

    def _run() -> None:
        global _unapply_status
        with _unapply_lock:
            _unapply_status = {"state": "running", "message": "Finding last apply run…"}
        try:
            from djlib.unapply import find_move_entries, run_unapply
            entries = find_move_entries(logs_dir=LOGS_DIR, last_run=True)
            if not entries:
                with _unapply_lock:
                    _unapply_status = {"state": "error", "message": "No apply log found"}
                return
            with _unapply_lock:
                _unapply_status["message"] = f"Moving {len(entries)} track(s) back…"
            with _CSV_LOCK:
                result = run_unapply(
                    entries=entries,
                    unsorted_csv=UNSORTED_CSV,
                    library_csv_path=CSV_PATH,
                    logs_dir=LOGS_DIR,
                    inbox_dir=INBOX_DIR,
                )
            moved = result.moved + result.skipped_wal_resumed
            msg = f"Unapplied {moved} track{'s' if moved != 1 else ''}"
            if result.failed_hash_mismatch or result.failed_other:
                msg += f" ({result.failed_hash_mismatch + result.failed_other} failed)"
            with _unapply_lock:
                _unapply_status = {"state": "done", "moved": moved, "message": msg}
        except Exception as e:
            log.error("unapply-last-run failed: %s", e)
            with _unapply_lock:
                _unapply_status = {"state": "error", "message": str(e)}

    threading.Thread(target=_run, daemon=True, name="unapply-last-run").start()
    return jsonify({"ok": True})


@app.route("/api/unapply-status")
def api_unapply_status() -> Response:
    with _unapply_lock:
        return jsonify(dict(_unapply_status))


# ── Playlist diff ─────────────────────────────────────────────────────────────


def _build_playlist_diff(rb_playlists: Dict[str, Any], lib_rows: list) -> Dict[str, Any]:
    """Compute diff between Rekordbox playlists and library.csv playlists field.

    Returns {playlist_name: [track_entry]} and rb_only_playlists list.
    Four states per track:
      "both"            — in RB playlist AND tagged in library.csv
      "rb_only"         — in RB playlist, track IS in library.csv but not tagged
      "rb_only_unknown" — in RB playlist, track NOT in library.csv at all
      "djlib_only"      — tagged in library.csv, NOT in RB playlist
    """
    # Build indexes from library.csv
    rb_id_to_row: Dict[str, Dict] = {}
    for row in lib_rows:
        rb_id = str(row.get("rekordbox_id") or "").strip()
        if rb_id:
            rb_id_to_row[rb_id] = row

    # For each track in a djlib playlist, note which playlists it's tagged for
    # {rekordbox_id: set(playlist_names)}
    rb_id_to_djlib_playlists: Dict[str, set] = {}
    for row in lib_rows:
        rb_id = str(row.get("rekordbox_id") or "").strip()
        if not rb_id:
            continue
        raw = (row.get("playlists") or "").strip()
        if raw:
            rb_id_to_djlib_playlists[rb_id] = {p.strip() for p in raw.split("|") if p.strip()}
        else:
            rb_id_to_djlib_playlists[rb_id] = set()

    result_playlists: Dict[str, list] = {}
    # Track which (rb_id, playlist_name) pairs we saw in RB
    seen_in_rb: Dict[str, set] = {}  # rb_id → set(playlist_names)

    def _lib_fields(row: Dict, fallback_artist: str = "", fallback_title: str = "") -> Dict:
        """Extract all display fields from a library.csv row."""
        dur_raw = (row.get("duration_seconds") or "").strip()
        dur_fmt = ""
        if dur_raw:
            try:
                secs = int(float(dur_raw))
                dur_fmt = f"{secs // 60}:{secs % 60:02d}"
            except (ValueError, TypeError):
                pass
        return {
            "artist":       row.get("artist") or fallback_artist,
            "title":        row.get("title") or fallback_title,
            "version_info": row.get("version_info") or "",
            "genre":        row.get("genre") or "",
            "year":         row.get("year") or "",
            "bpm":          row.get("bpm") or "",
            "key_camelot":  row.get("key_camelot") or "",
            "duration":     dur_fmt,
            "rating":       row.get("rating") or "",
        }

    for pl_name, tracks in rb_playlists.items():
        entries = []
        for track in tracks:
            rb_id = str(track.get("rb_id") or "")
            if not rb_id:
                continue
            seen_in_rb.setdefault(rb_id, set()).add(pl_name)

            row = rb_id_to_row.get(rb_id)
            if row is None:
                # Track in RB playlist but not in library.csv
                entries.append({
                    "track_id": None,
                    "rekordbox_id": rb_id,
                    "artist": track.get("artist") or "?",
                    "title": track.get("title") or "?",
                    "version_info": "",
                    "genre": "", "year": "", "bpm": "",
                    "key_camelot": "", "duration": "", "rating": "",
                    "state": "rb_only_unknown",
                })
            else:
                djlib_pls = rb_id_to_djlib_playlists.get(rb_id, set())
                state = "both" if pl_name in djlib_pls else "rb_only"
                entry = _lib_fields(row, track.get("artist") or "", track.get("title") or "")
                entry["track_id"] = row.get("track_id")
                entry["rekordbox_id"] = rb_id
                entry["state"] = state
                entries.append(entry)
        result_playlists[pl_name] = entries

    # djlib_only: tagged in library.csv but NOT seen in any RB playlist.
    # Deduplicate by (track_id, playlist_name) to avoid double-entries when
    # library.csv has duplicate rows for the same track.
    djlib_only_seen: set = set()  # set of (track_id, pl_name) already emitted
    for row in lib_rows:
        rb_id = str(row.get("rekordbox_id") or "").strip()
        tid = row.get("track_id") or ""
        raw = (row.get("playlists") or "").strip()
        if not raw:
            continue
        for pl_name in (p.strip() for p in raw.split("|") if p.strip()):
            rb_pls_for_track = seen_in_rb.get(rb_id, set())
            if pl_name not in rb_pls_for_track:
                dedup_key = (tid, pl_name)
                if dedup_key in djlib_only_seen:
                    continue
                djlib_only_seen.add(dedup_key)
                pl_entries = result_playlists.setdefault(pl_name, [])
                entry = _lib_fields(row)
                entry["track_id"] = tid or None
                entry["rekordbox_id"] = rb_id or None
                entry["state"] = "djlib_only"
                pl_entries.append(entry)

    # rb_only_playlists: playlists that exist in RB but have no djlib representation
    rb_playlist_names = set(rb_playlists.keys())
    djlib_playlist_names: set = set()
    for row in lib_rows:
        raw = (row.get("playlists") or "").strip()
        for p in raw.split("|"):
            p = p.strip()
            if p:
                djlib_playlist_names.add(p)

    rb_only_playlists = sorted(rb_playlist_names - djlib_playlist_names)

    return {
        "playlists": result_playlists,
        "rb_only_playlists": rb_only_playlists,
    }


@app.route("/api/playlists/diff")
def api_playlists_diff() -> Response:
    """Compare djlib playlist tags vs Rekordbox playlist membership.

    Returns rb_open flag (push disabled if True), full diff, and
    rb_only_playlists (playlists only in Rekordbox, not tagged in djlib).
    """
    from djlib.rekordbox_playlist_reader import fetch_rb_playlists
    from djlib.library_schema import load_library_csv

    # Detect if Rekordbox is open (push should be disabled, diff still works)
    rb_open = False
    try:
        from pyrekordbox.utils import get_rekordbox_pid
        rb_open = bool(get_rekordbox_pid())
    except Exception:
        pass

    try:
        rb_playlists = fetch_rb_playlists()
    except FileNotFoundError:
        return jsonify({"error": "Rekordbox master6.db not found", "rb_open": rb_open}), 404
    except Exception as exc:
        return jsonify({"error": str(exc), "rb_open": rb_open}), 500

    lib_rows = list(load_library_csv(CSV_PATH))
    diff = _build_playlist_diff(rb_playlists, lib_rows)

    return jsonify({
        "rb_open": rb_open,
        "playlists": diff["playlists"],
        "rb_only_playlists": diff["rb_only_playlists"],
    })


@app.route("/api/track/<track_id>/adopt-from-rb", methods=["POST"])
def api_adopt_from_rb(track_id: str) -> Response:
    """Add a playlist tag to a library.csv track (adopt from Rekordbox).

    Body: {"playlist": "playlist_name"}
    Atomically updates library.csv via save_library_csv() + csv_lock.
    """
    from djlib.library_schema import load_library_csv, save_library_csv
    from djlib.locks import csv_lock

    body = request.get_json(silent=True) or {}
    playlist_name = str(body.get("playlist") or "").strip()
    if not playlist_name:
        return jsonify({"error": "playlist field required"}), 400
    if "|" in playlist_name:
        return jsonify({"error": "playlist name must not contain '|'"}), 400

    with csv_lock(CSV_PATH):
        rows = load_library_csv(CSV_PATH)
        found = False
        for row in rows:
            if row.get("track_id") == track_id:
                existing = (row.get("playlists") or "").strip()
                existing_set = {p.strip() for p in existing.split("|") if p.strip()}
                if playlist_name not in existing_set:
                    existing_set.add(playlist_name)
                    row["playlists"] = "|".join(sorted(existing_set))
                found = True
                break
        if not found:
            return jsonify({"error": f"track_id {track_id!r} not found in library"}), 404
        save_library_csv(CSV_PATH, rows)

    return jsonify({"ok": True, "track_id": track_id, "playlist": playlist_name})


# ── Server entry point ───────────────────────────────────────────────────────

def run_server(
    host: str = "127.0.0.1",
    port: int = 8899,
    no_browser: bool = False,
) -> None:
    """Start the review UI server."""
    from djlib.library_schema import verify_library_sha256
    if not verify_library_sha256(CSV_PATH):
        print(
            f"\n⚠️  WARNING: library.csv SHA-256 mismatch — file may be corrupted.\n"
            f"   Check data/backups/ and restore if needed before using the Review UI.\n"
        )

    url = f"http://{host}:{port}"
    print(f"\n🎧  Review UI: {url}")
    print(f"   Source:  unsorted.csv → {UNSORTED_CSV}")
    print(f"   Library: data/library.csv")
    print(f"\n   Keyboard shortcuts:")
    print(f"   [Space] Play/Pause  [↑↓] Navigate  [Enter] Play selected")
    print(f"   [A] Accept  [R] Reject  [V] Review  [D] Toggle Done")
    print(f"   [Ctrl+K] AI Chat")
    print(f"\n   Press Ctrl+C to stop\n")

    if not no_browser:
        threading.Timer(1.0, webbrowser.open, args=[url]).start()

    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    run_server()
