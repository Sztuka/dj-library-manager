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


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_library_csv() -> List[Dict[str, str]]:
    """Load library.csv (sync-dj-libraries format) via stdlib csv."""
    csv_path = _REPO / "data" / "library.csv"
    if not csv_path.exists():
        return []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


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
    """Return JSON list of tracks from unsorted.csv or library.csv."""
    source = request.args.get("source", "unsorted")
    if source == "unsorted":
        rows = load_unsorted_rows(UNSORTED_CSV)
    elif source == "library":
        rows = _load_library_csv()
    else:
        return jsonify({"error": f"Unknown source: {source}"}), 400
    return jsonify(rows)


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
    return send_file(p, mimetype=mime, conditional=True)


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
