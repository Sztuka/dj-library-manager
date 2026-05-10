"""Versioned cue point serialization.

Format stored in library.csv:
    {"v": 1, "cues": [...]}   — Rekordbox (cue_points_rb)
    {"v": 1, "cues": [...]}   — Traktor   (cue_points_tk)

An empty track (no cues in DJ software) stores:
    {"v": 1, "cues": []}

Never stores the empty string or null — readers must handle "" by treating
it as no data (field not yet populated), distinct from [] (track has no cues).

Rekordbox cue fields (native units):
    pos_ms      int   — InMsec: cue position in milliseconds
    in_frame    int   — InFrame
    kind        int   — 0=memory cue, 1-8=hot cue slot number
    color       int   — Color (raw RB color int)
    color_idx   int   — ColorTableIndex
    active_loop bool  — ActiveLoop
    label       str   — Comment (cue name/label)
    loop_size   float — BeatLoopSize
    out_ms      int   — OutMsec: loop end in ms (0 if not a loop)
    out_frame   int   — OutFrame
    pos_us      int   — CueMicrosec: microsecond precision position
    seek_info   str   — InPointSeekInfo
    out_seek    str   — OutPointSeekInfo
    (dropped: InMpegFrame/Abs, OutMpegFrame/Abs — recomputed by RB on analysis)

Traktor cue fields (native Traktor units: time in seconds as float):
    label       str   — name
    displ_order int   — displ_order
    kind        int   — type: 0=cue,1=fade-in,2=fade-out,3=load,4=grid,5=loop
    pos_s       float — start (seconds, Traktor native)
    len_s       float — len (loop length in seconds)
    repeats     int   — repeats (-1 = infinite)
    hotcue      int   — hotcue slot (-1=memory cue, 0-7=hot cue)
    color       int   — color (may be None → stored as null)
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

log = logging.getLogger(__name__)

CUE_SCHEMA_VERSION = 1

_EMPTY = json.dumps({"v": CUE_SCHEMA_VERSION, "cues": []}, separators=(",", ":"))


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _safe_str(val: Any) -> str:
    return str(val) if val is not None else ""


def _safe_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return bool(val)
    return False


# ── Rekordbox ────────────────────────────────────────────────────────────────

def _rb_cue_to_dict(cue: Any) -> Dict[str, Any]:
    """Serialize one Rekordbox DjmdCue SQLAlchemy row to a plain dict."""
    return {
        "pos_ms":     _safe_int(getattr(cue, "InMsec", None)),
        "in_frame":   _safe_int(getattr(cue, "InFrame", None)),
        "kind":       _safe_int(getattr(cue, "Kind", None)),
        "color":      _safe_int(getattr(cue, "Color", None)),
        "color_idx":  _safe_int(getattr(cue, "ColorTableIndex", None)),
        "active_loop": _safe_bool(getattr(cue, "ActiveLoop", False)),
        "label":      _safe_str(getattr(cue, "Comment", None)),
        "loop_size":  _safe_float(getattr(cue, "BeatLoopSize", None)),
        "out_ms":     _safe_int(getattr(cue, "OutMsec", None)),
        "out_frame":  _safe_int(getattr(cue, "OutFrame", None)),
        "pos_us":     _safe_int(getattr(cue, "CueMicrosec", None)),
        "seek_info":  _safe_str(getattr(cue, "InPointSeekInfo", None)),
        "out_seek":   _safe_str(getattr(cue, "OutPointSeekInfo", None)),
    }


def serialize_rb_cues(cues: Any) -> str:
    """Serialize Rekordbox DjmdCue rows to JSON string for library.csv.

    READ-ONLY: never calls db.commit(). Safe to call with Rekordbox open.
    Returns '{"v":1,"cues":[]}' on empty or error — never raises.
    """
    try:
        cue_list = list(cues) if cues is not None else []
        serialized = [_rb_cue_to_dict(c) for c in cue_list]
        return json.dumps({"v": CUE_SCHEMA_VERSION, "cues": serialized},
                          ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        log.warning("Failed to serialize Rekordbox cues: %s", exc)
        return _EMPTY


def deserialize_rb_cues(json_str: str) -> List[Dict[str, Any]]:
    """Parse cue_points_rb JSON back to list of dicts.

    Returns [] on empty string, malformed JSON, or wrong schema version.
    Never raises.
    """
    if not json_str or json_str.strip() == "":
        return []
    try:
        data = json.loads(json_str)
        if not isinstance(data, dict):
            log.warning("cue_points_rb: expected JSON object, got %s", type(data).__name__)
            return []
        if data.get("v") != CUE_SCHEMA_VERSION:
            log.warning("cue_points_rb: unknown schema version %r", data.get("v"))
            return []
        cues = data.get("cues", [])
        if not isinstance(cues, list):
            log.warning("cue_points_rb: 'cues' is not a list")
            return []
        return cues
    except json.JSONDecodeError as exc:
        log.warning("cue_points_rb: malformed JSON: %s", exc)
        return []


# ── Traktor ──────────────────────────────────────────────────────────────────

def _tk_cue_to_dict(cue: Any) -> Dict[str, Any]:
    """Serialize one Traktor CueV2Type to a plain dict."""
    color = getattr(cue, "color", None)
    return {
        "label":      _safe_str(getattr(cue, "name", None)),
        "displ_order": _safe_int(getattr(cue, "displ_order", None)),
        "kind":       _safe_int(getattr(cue, "type", None)),
        "pos_s":      _safe_float(getattr(cue, "start", None)),
        "len_s":      _safe_float(getattr(cue, "len", None)),
        "repeats":    _safe_int(getattr(cue, "repeats", -1)),
        "hotcue":     _safe_int(getattr(cue, "hotcue", -1)),
        "color":      _safe_int(color) if color is not None else None,
    }


def serialize_tk_cues(cues: Any) -> str:
    """Serialize Traktor CueV2Type list to JSON string for library.csv.

    READ-ONLY: does not modify the Traktor collection. Never raises.
    """
    try:
        cue_list = list(cues) if cues is not None else []
        serialized = [_tk_cue_to_dict(c) for c in cue_list]
        return json.dumps({"v": CUE_SCHEMA_VERSION, "cues": serialized},
                          ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        log.warning("Failed to serialize Traktor cues: %s", exc)
        return _EMPTY


def deserialize_tk_cues(json_str: str) -> List[Dict[str, Any]]:
    """Parse cue_points_tk JSON back to list of dicts.

    Returns [] on empty string, malformed JSON, or wrong schema version.
    Never raises.
    """
    if not json_str or json_str.strip() == "":
        return []
    try:
        data = json.loads(json_str)
        if not isinstance(data, dict):
            log.warning("cue_points_tk: expected JSON object, got %s", type(data).__name__)
            return []
        if data.get("v") != CUE_SCHEMA_VERSION:
            log.warning("cue_points_tk: unknown schema version %r", data.get("v"))
            return []
        cues = data.get("cues", [])
        if not isinstance(cues, list):
            log.warning("cue_points_tk: 'cues' is not a list")
            return []
        return cues
    except json.JSONDecodeError as exc:
        log.warning("cue_points_tk: malformed JSON: %s", exc)
        return []
