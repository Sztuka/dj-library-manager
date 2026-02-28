"""Review UI server — Flask app for track preview and approval workflow.

Usage:
    python -m djlib.cli review [--port 8899] [--no-browser]

Serves unsorted.csv (editable) and library.csv (read-only) as interactive
tables with inline audio playback. Keyboard-driven: Space to play/pause,
arrows to navigate, A/R/V for accept/reject/review.
"""
from __future__ import annotations

import csv
import mimetypes
import os
import re
import threading
import webbrowser
from pathlib import Path
from typing import Any, Dict, List

import yaml
from flask import Flask, Response, jsonify, render_template, request, send_file

from djlib.config import UNSORTED_CSV
from djlib.unsorted import load_unsorted_rows, write_unsorted_rows

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


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_library_csv() -> List[Dict[str, str]]:
    """Load library.csv (sync-dj-libraries format) via stdlib csv."""
    csv_path = _REPO / "data" / "library.csv"
    if not csv_path.exists():
        return []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def _load_processed_tracks() -> List[Dict[str, str]]:
    """Load processed tracks from LOGS/moves-*.csv, enriched with library.csv.

    Each move log has columns: src, dest, track_id.  We deduplicate by
    track_id (last move wins), classify destination from the path, then
    cross-reference with library.csv by track_id and by path to pull
    metadata (artist, title, bpm, key, rating, play_count, etc.).
    """
    logs_dir = _REPO / "LOGS"
    if not logs_dir.exists():
        return []

    # 1. Gather moves (sorted chronologically → last write wins)
    move_data: Dict[str, Dict[str, str]] = {}  # track_id → info
    _date_re = re.compile(r"moves-(\d{8})-(\d{6})")
    for f in sorted(logs_dir.glob("moves-*.csv")):
        m = _date_re.search(f.stem)
        date_str = m.group(1) if m else ""
        time_str = m.group(2) if m else ""
        with open(f, newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            next(reader, None)  # skip header
            for row in reader:
                if row and len(row) >= 3:
                    tid = row[-1].strip()
                    if tid:
                        move_data[tid] = {
                            "src": row[0].strip(),
                            "dest": row[1].strip(),
                            "move_date": date_str,
                            "move_time": time_str,
                        }

    if not move_data:
        return []

    # 2. Build library lookup (by track_id and by normalised path)
    lib_rows = _load_library_csv()
    lib_by_tid: Dict[str, Dict[str, str]] = {}
    lib_by_path: Dict[str, Dict[str, str]] = {}
    for row in lib_rows:
        tid = row.get("track_id", "").strip()
        path = row.get("old_full_path", "").strip()
        if tid:
            lib_by_tid[tid] = row
        if path:
            lib_by_path[os.path.normpath(path)] = row

    # 3. Build processed list with enrichment
    result: List[Dict[str, str]] = []
    for tid, info in move_data.items():
        dest = info["dest"]

        # Classify destination
        if "Music Library" in dest:
            dest_type = "library"
        elif "Music Archive" in dest:
            dest_type = "archive"
        elif "Music Rejected" in dest:
            dest_type = "rejected"
        elif "Music Mixes" in dest:
            dest_type = "mixes"
        else:
            dest_type = "other"

        # Cross-reference: try track_id first, then path
        lib_row = lib_by_tid.get(tid)
        if not lib_row:
            norm_dest = os.path.normpath(dest)
            lib_row = lib_by_path.get(norm_dest)

        # Format move_date as YYYY-MM-DD
        d = info["move_date"]
        move_date_fmt = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d

        rec: Dict[str, str] = {
            "track_id": tid,
            "move_date": move_date_fmt,
            "destination": dest_type,
            "original_path": info["src"],
            "file_path": dest,  # for audio playback
        }

        if lib_row:
            # Enrich from library
            rec["artist"] = lib_row.get("artist", "")
            rec["title"] = lib_row.get("title", "")
            rec["bpm"] = lib_row.get("bpm", "")
            rec["key"] = lib_row.get("key", "")
            rec["rating"] = lib_row.get("rating", "")
            rec["play_count"] = lib_row.get("play_count", "")
            rec["external_source"] = lib_row.get("external_source", "")
            rec["date_added"] = lib_row.get("date_added", "")
            rec["in_dj_software"] = "yes"
        else:
            # Parse artist / title from filename: "Artist - Title [Key BPM].ext"
            fname = os.path.splitext(os.path.basename(dest))[0]
            # Remove trailing [Key BPM] bracket
            fname_clean = re.sub(r"\s*\[.*\]\s*$", "", fname)
            if " - " in fname_clean:
                parts = fname_clean.split(" - ", 1)
                rec["artist"] = parts[0].strip()
                rec["title"] = parts[1].strip()
            else:
                rec["artist"] = ""
                rec["title"] = fname_clean.strip()
            rec["bpm"] = ""
            rec["key"] = ""
            rec["rating"] = ""
            rec["play_count"] = ""
            rec["external_source"] = ""
            rec["date_added"] = ""
            rec["in_dj_software"] = "no"

        result.append(rec)

    # Sort by move_date descending (newest first)
    result.sort(key=lambda r: r.get("move_date", ""), reverse=True)
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
    return render_template("index.html")


@app.route("/api/tracks")
def api_tracks():
    """Return JSON list of tracks from unsorted.csv, library.csv, or processed."""
    source = request.args.get("source", "unsorted")
    if source == "unsorted":
        rows = load_unsorted_rows(UNSORTED_CSV)
    elif source == "library":
        rows = _load_library_csv()
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


@app.route("/api/audio")
def api_audio():
    """Stream an audio file from the local filesystem.

    Supports Range requests for seeking (handled by Flask's send_file
    with conditional=True).
    """
    path_str = request.args.get("path", "")
    if not path_str:
        return jsonify({"error": "No path provided"}), 400

    p = Path(path_str).expanduser().resolve()
    if not p.exists():
        return jsonify({"error": f"File not found: {path_str}"}), 404

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
    if not tid:
        return jsonify({"error": "Missing track_id or file_hash"}), 400
    if not fields:
        return jsonify({"error": "No fields to update"}), 400

    with _CSV_LOCK:
        rows = load_unsorted_rows(UNSORTED_CSV)
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

        write_unsorted_rows(UNSORTED_CSV, rows, [])
    return jsonify({"ok": True})


@app.route("/api/genres")
def api_genres():
    """Return list of valid genre labels from genres.yml."""
    return jsonify(_load_genres())


# ── Server entry point ───────────────────────────────────────────────────────

def run_server(
    host: str = "127.0.0.1",
    port: int = 8899,
    no_browser: bool = False,
) -> None:
    """Start the review UI server."""
    url = f"http://{host}:{port}"
    print(f"\n🎧  Review UI: {url}")
    print(f"   Source:  unsorted.csv → {UNSORTED_CSV}")
    print(f"   Library: data/library.csv")
    print(f"\n   Keyboard shortcuts:")
    print(f"   [Space] Play/Pause  [↑↓] Navigate  [Enter] Play selected")
    print(f"   [A] Accept  [R] Reject  [V] Review  [D] Toggle Done")
    print(f"\n   Press Ctrl+C to stop\n")

    if not no_browser:
        threading.Timer(1.0, webbrowser.open, args=[url]).start()

    app.run(host=host, port=port, debug=False, use_reloader=False)
