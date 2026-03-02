"""Review UI server — Flask app for track preview and approval workflow.

Usage:
    python -m djlib.cli review [--port 8899] [--no-browser]

Serves unsorted.csv (editable) and library.csv (read-only) as interactive
tables with inline audio playback. Keyboard-driven: Space to play/pause,
arrows to navigate, A/R/V for accept/reject/review.
"""
from __future__ import annotations

import csv
import json
import logging
import mimetypes
import os
import re
import subprocess
import threading
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests as http_requests
import yaml
from flask import Flask, Response, jsonify, render_template, request, send_file

from djlib.config import UNSORTED_CSV, get_openai_api_key
from djlib.filename import parse_from_filename
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
    """Load processed tracks from library.csv filtered by destination folders.

    Tracks are considered "processed" if their old_full_path is in one of the
    workflow output folders: Music Library, Music Rejected, Music Archive, or
    Music Mixes.  This is the authoritative source — library.csv is regenerated
    by sync-dj-libraries from the actual Rekordbox/Traktor databases, so it
    reflects reality (no duplicates, correct metadata).

    Move logs (LOGS/moves-*.csv) are historical artifacts with known issues
    (duplicates, stale entries) and are NOT used here.
    """
    _DEST_PATTERNS = [
        ("library", "Music Library"),
        ("rejected", "Music Rejected"),
        ("archive", "Music Archive"),
        ("mixes", "Music Mixes"),
    ]

    lib_rows = _load_library_csv()
    if not lib_rows:
        return []

    result: List[Dict[str, str]] = []
    for row in lib_rows:
        path = row.get("old_full_path", "")
        if not path:
            continue

        # Classify destination from path
        dest_type = ""
        for dtype, pattern in _DEST_PATTERNS:
            if pattern in path:
                dest_type = dtype
                break

        if not dest_type:
            continue  # Not a processed track (e.g. ~/Music/ from DJ imports)

        ext_src = row.get("external_source", "").strip()
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
            "date_added": row.get("date_added", ""),
            "destination": dest_type,
            "in_dj_software": "yes" if ext_src else "no",
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


# ── AI Genre Suggest ─────────────────────────────────────────────────────────

_ai_cache: Dict[str, Dict[str, Any]] = {}  # track_id -> {genre, confidence, reasoning}
_identify_cache: Dict[str, Dict[str, Any]] = {}  # track_id -> {artist, title, version, year, ...}
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
        "\n\nBPM genre ranges (approximate, use as strong signal):\n"
        "70-100: Hip-Hop, R&B, Reggaeton, Dancehall\n"
        "100-115: Broken Beat, UK Garage, Afrobeats\n"
        "115-122: Deep House, Soulful House\n"
        "120-128: House, Tech House, Afro House, Jackin House\n"
        "126-132: Melodic House & Techno, Progressive House\n"
        "130-140: Techno, Hard Techno, Trance, Hard Dance\n"
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
            "model": "gpt-4o-mini",
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


def _build_identify_prompt(row: Dict[str, str]) -> str:
    """Build the prompt for AI track identification from CSV row data."""
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
        parts.append(f"Folder: {folder}")

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

    track_info = "\n".join(parts)

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

    # Check cache
    if tid in _identify_cache:
        return jsonify(_identify_cache[tid])

    # Load row from CSV
    with _CSV_LOCK:
        rows = load_unsorted_rows(UNSORTED_CSV)

    row = None
    for r in rows:
        if r.get("track_id") == tid:
            row = r
            break

    if not row:
        return jsonify({"error": f"Track not found: {tid}"}), 404

    # Build prompt with all available context
    prompt = _build_identify_prompt(row)

    try:
        result = _call_openai_json(api_key, prompt, max_tokens=300)
        if tid:
            _identify_cache[tid] = result
        return jsonify(result)
    except Exception as e:
        _log.warning("OpenAI identify error: %s", e)
        return jsonify({"error": f"AI request failed: {e}"}), 502


# ── Re-enrich (context menu) ─────────────────────────────────────────────────

_enrich_lock = threading.Lock()


def _resolve_genres(artist: str, title: str, **kwargs):
    """Lazy wrapper for genre_resolver.resolve (avoids heavy import at startup)."""
    from djlib.metadata.genre_resolver import resolve
    return resolve(artist, title, **kwargs)


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


@app.route("/api/enrich-track", methods=["POST"])
def api_enrich_track():
    """Re-enrich a single track using user-edited artist/title values.

    Uses genre resolver (Beatport, Last.fm, SoundCloud, MusicBrainz) to find
    genre based on the CURRENT artist/title in the CSV (which may have been
    edited by the user in the UI).

    Request body (JSON):
        { "track_id": "..." }

    Returns:
        { "genre": "Afro House", "genre_full": "Afro House, Deep House",
          "confidence": 0.85, "sources": ["beatport", "lastfm"],
          "year": "2024", "album": "...",
          "swap_suggestion": { "swapped": true, ... } | null }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    tid = data.get("track_id", "")
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
        tag_genre = (row.get("tag_genre_original") or "").strip()
        _log.info("Re-enriching: %s - %s (%s)", artist, title, version)

        genre_res = _resolve_genres(
            artist, title,
            version=version,
            duration_s=dur_s,
            tag_genre=tag_genre,
        )
    except Exception as e:
        _log.warning("Enrich failed: %s", e)
        return jsonify({"error": f"Enrichment failed: {e}"}), 502
    finally:
        _enrich_lock.release()

    if not genre_res or genre_res.confidence < 0.01:
        return jsonify({
            "genre": None,
            "genre_full": None,
            "confidence": 0,
            "sources": [],
            "swap_suggestion": swap_suggestion,
        })

    genres = [genre_res.main] + genre_res.subs[:2]
    genre_full = ", ".join(genres)
    sources = list({s.source for s in genre_res.breakdown})

    # Collect per-source raw tags for display
    source_details: Dict[str, str] = {}
    for s in genre_res.breakdown:
        top_tags = sorted(s.tags.items(), key=lambda kv: kv[1], reverse=True)[:5]
        source_details[s.source] = ", ".join(t[0] for t in top_tags)

    # Map source names to CSV column names for saving
    _SOURCE_TO_CSV = {
        "beatport": "genres_beatport",
        "lastfm": "genres_lastfm",
        "mb": "genres_musicbrainz",
        "soundcloud": "genres_soundcloud",
    }
    source_genres: Dict[str, str] = {}
    for s in genre_res.breakdown:
        csv_col = _SOURCE_TO_CSV.get(s.source)
        if csv_col:
            top_tags = sorted(s.tags.items(), key=lambda kv: kv[1], reverse=True)[:8]
            source_genres[csv_col] = ", ".join(t[0] for t in top_tags)

    meta_source = "|".join(sorted(sources))

    return jsonify({
        "genre": genre_res.main,
        "genre_full": genre_full,
        "confidence": round(genre_res.confidence, 3),
        "sources": sources,
        "source_details": source_details,
        "source_genres": source_genres,
        "meta_source": meta_source,
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

    with _CSV_LOCK:
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


if __name__ == "__main__":
    run_server()
