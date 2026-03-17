#!/usr/bin/env python3
"""Export comprehensive AB test data for manual review.

Combines all data sources into a single CSV:
- Track metadata (artist, title, version, bpm, key, folder genre)
- Earlier AB test: nano, nano+E, nano+D400, mini, mini+E, full, full+E predictions
- Web search AB test: ddg, brave, serper, searxng, none predictions
- Web search stats per backend
- Full system prompt + user prompt (reconstructed)

Output: data/ab_test/comprehensive_review.csv
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

AB_DIR = PROJECT_ROOT / "data" / "ab_test"
RESULTS_JSON = AB_DIR / "results.json"
WEB_SEARCH_JSON = AB_DIR / "web_search_results.json"
ESSENTIA_DB = PROJECT_ROOT / "LOGS" / "audio_analysis.sqlite"
OUTPUT_CSV = AB_DIR / "comprehensive_review.csv"

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aiff", ".aif", ".m4a", ".ogg"}


# ── Parse filename to match with web_search track keys ───────────────────────

def _parse_filename(filename: str) -> Dict[str, str]:
    """Parse 'Artist - Title [key bpm].ext' or 'Artist - Title (Version).ext'."""
    stem = Path(filename).stem
    key_bpm = re.search(r'\[(\d{1,2}[AB]|--)\s+(--|\d{2,3})\]', stem)
    key = key_bpm.group(1) if key_bpm and key_bpm.group(1) != "--" else ""
    bpm = key_bpm.group(2) if key_bpm and key_bpm.group(2) != "--" else ""
    stem_clean = re.sub(r'\s*\[(?:\d{1,2}[AB]|--)\s+(?:--|\d{2,3})\]\s*', '', stem).strip()

    parts = [p.strip() for p in stem_clean.split(" - ")]
    artist = parts[0] if len(parts) >= 1 else ""
    title = parts[1] if len(parts) >= 2 else stem_clean
    version = ""
    if len(parts) >= 3:
        version = " - ".join(parts[2:])

    paren = re.search(
        r'\(([^)]+(?:Remix|Edit|Mix|Version|Bootleg|Rework|Refix|Flip)[^)]*)\)',
        title, re.IGNORECASE,
    )
    if paren and not version:
        version = paren.group(1)
        title = title[:paren.start()].strip()
    return {"artist": artist, "title": title, "version": version, "bpm": bpm, "key": key}


def make_ws_key(artist: str, title: str, version: str) -> str:
    """Build web_search track key."""
    return f"{artist.strip().lower()}|{title.strip().lower()}|{version.strip().lower()}"


# ── Reconstruct the prompt that was sent to LLM ─────────────────────────────

def reconstruct_prompt(
    track: Dict[str, str],
    search_context: str,
    genre_labels: List[str],
) -> Dict[str, str]:
    """Reconstruct the system + user prompts from track data."""
    genre_list = ", ".join(genre_labels)

    # Track info
    parts = []
    if track.get("artist"):
        parts.append(f"Artist: {track['artist']}")
    if track.get("title"):
        parts.append(f"Title: {track['title']}")
    if track.get("version"):
        parts.append(f"Version/Remix: {track['version']}")
    if track.get("bpm"):
        parts.append(f"BPM: {track['bpm']}")
    if track.get("key"):
        parts.append(f"Key: {track['key']}")
    track_info = "\n".join(parts)

    version_str = track.get("version", "")
    is_remix = bool(re.search(
        r'\b(?:remix|edit|bootleg|rework|refix|mashup|flip)\b',
        version_str, re.IGNORECASE,
    ))

    remix_note = ""
    if is_remix:
        remix_note = (
            "\n\nCRITICAL — this is a REMIX/EDIT. Classify by the REMIX STYLE, "
            "NOT the original track's genre. The remixer's known scene is the strongest signal."
        )

    bpm_guide = (
        "\nBPM ranges: 70-100 Hip-Hop/R&B/Reggaeton, 100-115 UK Garage/Afrobeats, "
        "115-126 Deep House, 120-128 House/Tech House, 124-132 Melodic Techno/Prog House, "
        "128-140 Techno/Trance, 150-180 D&B"
    )

    system_msg = (
        f"You are an expert DJ music classifier. Classify tracks into exactly ONE of these genres:\n"
        f"{genre_list}\n\n"
        f"Genre signals (strongest to weakest):\n"
        f"1. Remixer identity (for remixes)\n"
        f"2. Beatport genre (strongest for EDM)\n"
        f"3. SoundCloud tags (strong for remixes)\n"
        f"4. BPM + artist identity\n"
        f"5. Discogs style (good for non-EDM)\n"
        f"6. General knowledge"
        f"{remix_note}{bpm_guide}\n\n"
        f"Respond ONLY with valid JSON:\n"
        f'{{"genre": "Exact Genre", "confidence": 0.0-1.0, "reasoning": "1-2 sentences"}}'
    )

    user_msg = f"Track:\n{track_info}\n\n"
    if search_context and search_context != "(no search)":
        user_msg += f"Web search results:\n{search_context}\n\n"
    user_msg += "Classify this track's genre."

    return {"system": system_msg, "user": user_msg}


# ── Load data sources ────────────────────────────────────────────────────────

def load_genre_labels() -> List[str]:
    """Load genre labels from genres.yml."""
    from djlib.ai_classify import load_genre_labels as _load
    return _load()


def load_results_json() -> Dict[str, Any]:
    """Load earlier AB test results (nano/mini/full variants)."""
    if not RESULTS_JSON.exists():
        return {}
    with open(RESULTS_JSON) as f:
        return json.load(f)


def load_web_search_json() -> Dict[str, Any]:
    """Load web search AB test results."""
    if not WEB_SEARCH_JSON.exists():
        return {}
    with open(WEB_SEARCH_JSON) as f:
        return json.load(f)


# ── Essentia data ────────────────────────────────────────────────────────────

def _file_sha256(path: Path) -> str:
    """Compute SHA256 hash of file contents (matches Essentia audio_id)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_essentia_for_ab_tracks() -> Dict[str, Dict[str, Any]]:
    """Load Essentia data for AB test tracks, keyed by ws_key.

    Scans data/ab_test/<genre>/*.mp3 files, computes SHA256, looks up in SQLite.
    Returns: {ws_key: {essentia_bpm, essentia_key, essentia_lufs, ...}}
    """
    if not ESSENTIA_DB.exists() or not AB_DIR.exists():
        return {}

    conn = sqlite3.connect(str(ESSENTIA_DB))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    essentia_data: Dict[str, Dict[str, Any]] = {}
    matched = 0

    for genre_dir in sorted(AB_DIR.iterdir()):
        if not genre_dir.is_dir() or genre_dir.name.startswith("."):
            continue
        for audio_file in sorted(genre_dir.iterdir()):
            if audio_file.suffix.lower() not in AUDIO_EXTENSIONS or audio_file.name.startswith("."):
                continue

            # Build ws_key from filename
            parsed = _parse_filename(audio_file.name)
            ws_key = make_ws_key(parsed["artist"], parsed["title"], parsed["version"])

            # Compute SHA256 and look up in Essentia DB
            sha = _file_sha256(audio_file)
            cur.execute("SELECT * FROM audio_analysis WHERE audio_id=?", (sha,))
            row = cur.fetchone()
            if not row:
                continue

            matched += 1
            extras = json.loads(row["extras"]) if row["extras"] else {}
            features_ext = extras.get("features_ext", {})

            essentia_data[ws_key] = {
                "essentia_bpm": round(row["bpm"], 1) if row["bpm"] else "",
                "essentia_bpm_conf": round(row["bpm_conf"], 2) if row["bpm_conf"] else "",
                "essentia_key": row["key_camelot"] or "",
                "essentia_key_strength": round(row["key_strength"], 2) if row["key_strength"] else "",
                "essentia_lufs": round(row["lufs"], 1) if row["lufs"] else "",
                "essentia_dyn_complex": round(row["dyn_complex"], 2) if row["dyn_complex"] else "",
                "essentia_onset_rate": round(row["onset_rate"], 2) if row["onset_rate"] else "",
                "essentia_danceability": round(features_ext.get("danceability", 0), 2) if features_ext.get("danceability") else "",
                "essentia_spec_centroid": round(row["spec_centroid"], 0) if row["spec_centroid"] else "",
                "essentia_energy": round(row["energy"], 4) if row["energy"] else "",
            }

    conn.close()
    print(f"Essentia matched: {matched} tracks", file=sys.stderr)
    return essentia_data


# ── Helpers ──────────────────────────────────────────────────────────────────

def _oneline(s: str) -> str:
    """Collapse multiline text to single line for CSV/spreadsheet safety."""
    if not s:
        return ""
    return re.sub(r'\s+', ' ', s.replace('\n', ' ').replace('\r', '')).strip()


# ── Build unified track list ─────────────────────────────────────────────────

def build_track_list(
    results: Dict[str, Any],
    ws_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Build unified list of all unique tracks across data sources.

    Returns list of dicts with unified metadata.
    """
    tracks = {}  # ws_key -> track data

    # From web_search_results.json (primary, has clean metadata)
    ws_tracks = ws_data.get("tracks", {})
    for ws_key, tdata in ws_tracks.items():
        tracks[ws_key] = {
            "ws_key": ws_key,
            "artist": tdata.get("artist", ""),
            "title": tdata.get("title", ""),
            "version": tdata.get("version", ""),
            "bpm": tdata.get("bpm", ""),
            "key": "",  # Not stored in ws data
            "folder_genre": tdata.get("expected_genre", ""),
            "filename": "",
        }

    # From results.json (has filenames with key/bpm in brackets)
    rj_tracks = results.get("tracks", {})
    filename_to_ws_key = {}

    for rj_key, rj_val in rj_tracks.items():
        fn = rj_val.get("filename", "")
        if not fn:
            parts = rj_key.rsplit(":", 1)
            fn = parts[0] if len(parts) == 2 else rj_key

        parsed = _parse_filename(fn)
        ws_key = make_ws_key(parsed["artist"], parsed["title"], parsed["version"])
        filename_to_ws_key[fn] = ws_key

        if ws_key not in tracks:
            tracks[ws_key] = {
                "ws_key": ws_key,
                "artist": parsed["artist"],
                "title": parsed["title"],
                "version": parsed["version"],
                "bpm": parsed.get("bpm", "") or rj_val.get("bpm", ""),
                "key": parsed.get("key", ""),
                "folder_genre": rj_val.get("expected_genre", ""),
                "filename": fn,
            }
        else:
            # Enrich existing with key/filename
            if parsed.get("key") and not tracks[ws_key].get("key"):
                tracks[ws_key]["key"] = parsed["key"]
            if fn and not tracks[ws_key].get("filename"):
                tracks[ws_key]["filename"] = fn
            if parsed.get("bpm") and not tracks[ws_key].get("bpm"):
                tracks[ws_key]["bpm"] = parsed["bpm"]

    return list(tracks.values()), filename_to_ws_key


# ── Extract predictions ──────────────────────────────────────────────────────

EARLIER_VARIANTS = ["nano", "nano+E", "nano+D400", "mini", "mini+E", "full", "full+E"]
WS_VARIANTS = ["ddg", "brave", "serper", "searxng", "none"]


def get_earlier_predictions(
    rj_tracks: Dict[str, Any],
    filename_to_ws_key: Dict[str, str],
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Extract per-ws_key, per-variant predictions from results.json.

    Returns: {ws_key: {variant: {genre, confidence, reasoning}}}
    """
    predictions: Dict[str, Dict[str, Dict[str, Any]]] = {}

    for rj_key, rj_val in rj_tracks.items():
        fn = rj_val.get("filename", "")
        if not fn:
            parts = rj_key.rsplit(":", 1)
            fn = parts[0] if len(parts) == 2 else rj_key

        variant = rj_val.get("variant", "")
        ws_key = filename_to_ws_key.get(fn)
        if not ws_key:
            parsed = _parse_filename(fn)
            ws_key = make_ws_key(parsed["artist"], parsed["title"], parsed["version"])

        predictions.setdefault(ws_key, {})[variant] = {
            "genre": rj_val.get("predicted_genre", ""),
            "confidence": rj_val.get("confidence", 0),
            "reasoning": rj_val.get("reasoning", ""),
            "model": rj_val.get("model", ""),
            "elapsed": rj_val.get("elapsed", 0),
        }

    return predictions


def get_ws_predictions(ws_data: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Extract per-ws_key, per-variant predictions from web_search_results.json.

    Returns: {ws_key: {variant: {genre, confidence, reasoning}}}
    """
    predictions: Dict[str, Dict[str, Dict[str, Any]]] = {}
    classify = ws_data.get("classify", {})

    for variant, variant_data in classify.items():
        for tk, cdata in variant_data.items():
            predictions.setdefault(tk, {})[variant] = {
                "genre": cdata.get("genre", ""),
                "confidence": cdata.get("confidence", 0),
                "reasoning": cdata.get("reasoning", ""),
                "model": cdata.get("usage", {}).get("model", ""),
                "time_ms": cdata.get("usage", {}).get("time_ms", 0),
                "input_tokens": cdata.get("usage", {}).get("input_tokens", 0),
                "output_tokens": cdata.get("usage", {}).get("output_tokens", 0),
                "reasoning_tokens": cdata.get("usage", {}).get("reasoning_tokens", 0),
            }

    return predictions


def get_search_stats(ws_data: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Extract per-ws_key, per-backend search stats.

    Returns: {ws_key: {backend: {num_results, time_ms, beatport_hits, sources}}}
    """
    stats: Dict[str, Dict[str, Dict[str, Any]]] = {}
    search = ws_data.get("search", {})

    for backend, backend_data in search.items():
        for tk, sdata in backend_data.items():
            stats.setdefault(tk, {})[backend] = {
                "num_results": sdata.get("num_results", 0),
                "time_ms": sdata.get("time_ms", 0),
                "beatport_hits": sdata.get("beatport_hits", 0),
                "sources": sdata.get("sources", {}),
                "top_results": sdata.get("top_results", []),
            }

    return stats


# ── Main export ──────────────────────────────────────────────────────────────

def main():
    print("Loading data sources...", file=sys.stderr)
    results = load_results_json()
    ws_data = load_web_search_json()
    genre_labels = load_genre_labels()

    print("Building unified track list...", file=sys.stderr)
    track_list, filename_to_ws_key = build_track_list(results, ws_data)

    print("Extracting predictions...", file=sys.stderr)
    earlier_preds = get_earlier_predictions(results.get("tracks", {}), filename_to_ws_key)
    ws_preds = get_ws_predictions(ws_data)
    search_stats = get_search_stats(ws_data)

    print("Loading Essentia data...", file=sys.stderr)
    essentia_data = load_essentia_for_ab_tracks()

    # Sort by folder_genre then artist
    track_list.sort(key=lambda t: (t["folder_genre"], t["artist"].lower()))

    # Count how many tracks have data from each source
    has_earlier = sum(1 for t in track_list if t["ws_key"] in earlier_preds)
    has_ws = sum(1 for t in track_list if t["ws_key"] in ws_preds)
    print(f"Tracks with earlier AB test data: {has_earlier}", file=sys.stderr)
    print(f"Tracks with web search data: {has_ws}", file=sys.stderr)
    print(f"Total unique tracks: {len(track_list)}", file=sys.stderr)

    # Build CSV
    SEARCH_BACKENDS = ["ddg", "brave", "serper", "searxng"]

    fieldnames = [
        # Track metadata
        "folder_genre", "artist", "title", "version", "bpm", "key", "filename",
    ]

    # Earlier AB test predictions
    for v in EARLIER_VARIANTS:
        fieldnames.extend([f"{v}_genre", f"{v}_confidence", f"{v}_reasoning"])

    # Web search predictions
    for v in WS_VARIANTS:
        fieldnames.extend([f"ws_{v}_genre", f"ws_{v}_confidence", f"ws_{v}_reasoning"])

    # Search stats
    for b in SEARCH_BACKENDS:
        fieldnames.extend([f"search_{b}_results", f"search_{b}_beatport", f"search_{b}_time_ms", f"search_{b}_sources"])

    # Essentia audio features
    ESSENTIA_COLS = [
        "essentia_bpm", "essentia_bpm_conf", "essentia_key", "essentia_key_strength",
        "essentia_lufs", "essentia_dyn_complex", "essentia_onset_rate",
        "essentia_danceability", "essentia_spec_centroid", "essentia_energy",
    ]
    fieldnames.extend(ESSENTIA_COLS)

    # Prompt reconstruction (for "none" variant — base case)
    fieldnames.extend(["prompt_system", "prompt_user_base"])

    # Agreement analysis
    fieldnames.extend(["all_genres_predicted", "consensus_genre", "consensus_count"])

    print(f"Writing CSV with {len(fieldnames)} columns...", file=sys.stderr)

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        for track in track_list:
            ws_key = track["ws_key"]
            row: Dict[str, Any] = {
                "folder_genre": track["folder_genre"],
                "artist": track["artist"],
                "title": track["title"],
                "version": track["version"],
                "bpm": track["bpm"],
                "key": track["key"],
                "filename": track["filename"],
            }

            # Earlier predictions
            ep = earlier_preds.get(ws_key, {})
            for v in EARLIER_VARIANTS:
                pred = ep.get(v, {})
                row[f"{v}_genre"] = pred.get("genre", "")
                row[f"{v}_confidence"] = pred.get("confidence", "")
                row[f"{v}_reasoning"] = _oneline(pred.get("reasoning", ""))

            # Web search predictions
            wp = ws_preds.get(ws_key, {})
            for v in WS_VARIANTS:
                pred = wp.get(v, {})
                row[f"ws_{v}_genre"] = pred.get("genre", "")
                row[f"ws_{v}_confidence"] = pred.get("confidence", "")
                row[f"ws_{v}_reasoning"] = _oneline(pred.get("reasoning", ""))

            # Search stats
            ss = search_stats.get(ws_key, {})
            for b in SEARCH_BACKENDS:
                bstats = ss.get(b, {})
                row[f"search_{b}_results"] = bstats.get("num_results", "")
                row[f"search_{b}_beatport"] = bstats.get("beatport_hits", "")
                row[f"search_{b}_time_ms"] = bstats.get("time_ms", "")
                sources = bstats.get("sources", {})
                row[f"search_{b}_sources"] = "; ".join(f"{k}:{v}" for k, v in sources.items()) if sources else ""

            # Essentia features
            ess = essentia_data.get(ws_key, {})
            for col in ESSENTIA_COLS:
                row[col] = ess.get(col, "")

            # Reconstruct prompt (for "none" baseline)
            prompt = reconstruct_prompt(track, "(no search)", genre_labels)
            row["prompt_system"] = _oneline(prompt["system"])
            row["prompt_user_base"] = _oneline(prompt["user"])

            # Agreement analysis: collect all predicted genres
            all_genres = []
            for v in EARLIER_VARIANTS:
                g = ep.get(v, {}).get("genre", "")
                if g:
                    all_genres.append(f"{v}:{g}")
            for v in WS_VARIANTS:
                g = wp.get(v, {}).get("genre", "")
                if g:
                    all_genres.append(f"ws_{v}:{g}")

            row["all_genres_predicted"] = " | ".join(all_genres)

            # Find consensus
            genre_counts: Dict[str, int] = {}
            for v in EARLIER_VARIANTS:
                g = ep.get(v, {}).get("genre", "")
                if g:
                    genre_counts[g] = genre_counts.get(g, 0) + 1
            for v in WS_VARIANTS:
                g = wp.get(v, {}).get("genre", "")
                if g:
                    genre_counts[g] = genre_counts.get(g, 0) + 1

            if genre_counts:
                consensus = max(genre_counts, key=genre_counts.get)
                row["consensus_genre"] = consensus
                row["consensus_count"] = f"{genre_counts[consensus]}/{sum(genre_counts.values())}"
            else:
                row["consensus_genre"] = ""
                row["consensus_count"] = ""

            writer.writerow(row)

    print(f"\n✅ Exported {len(track_list)} tracks to {OUTPUT_CSV}", file=sys.stderr)
    print(f"   Columns: {len(fieldnames)}", file=sys.stderr)

    # Print summary stats
    print("\n── TRACK SOURCE DISTRIBUTION ──", file=sys.stderr)
    both = sum(1 for t in track_list if t["ws_key"] in earlier_preds and t["ws_key"] in ws_preds)
    only_earlier = sum(1 for t in track_list if t["ws_key"] in earlier_preds and t["ws_key"] not in ws_preds)
    only_ws = sum(1 for t in track_list if t["ws_key"] not in earlier_preds and t["ws_key"] in ws_preds)
    print(f"  Both datasets: {both}", file=sys.stderr)
    print(f"  Only earlier AB test: {only_earlier}", file=sys.stderr)
    print(f"  Only web search test: {only_ws}", file=sys.stderr)

    # Genre disagreement report
    print("\n── DISAGREEMENT HIGHLIGHTS ──", file=sys.stderr)
    disagreements = []
    for track in track_list:
        ws_key = track["ws_key"]
        ep = earlier_preds.get(ws_key, {})
        wp = ws_preds.get(ws_key, {})

        all_genres = set()
        for v in EARLIER_VARIANTS:
            g = ep.get(v, {}).get("genre", "")
            if g:
                all_genres.add(g)
        for v in WS_VARIANTS:
            g = wp.get(v, {}).get("genre", "")
            if g:
                all_genres.add(g)

        folder = track["folder_genre"]
        if len(all_genres) >= 3 or (folder and folder not in all_genres):
            disagreements.append({
                "artist": track["artist"],
                "title": track["title"],
                "folder": folder,
                "genres": all_genres,
                "count": len(all_genres),
            })

    disagreements.sort(key=lambda d: -d["count"])
    for d in disagreements[:20]:
        genres_str = ", ".join(sorted(d["genres"]))
        mismatch = " ⚠️ FOLDER NOT IN PREDICTIONS" if d["folder"] and d["folder"] not in d["genres"] else ""
        print(f"  {d['artist']} - {d['title']}: folder={d['folder']}, predicted: {genres_str}{mismatch}", file=sys.stderr)


if __name__ == "__main__":
    main()
