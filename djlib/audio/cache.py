from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import numpy as _np  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    _np = None  # type: ignore


_EXTRA_FEATURES_KEY = "features_ext"

from djlib.config import LOGS_DIR
from djlib.fingerprint import file_sha256


def db_path() -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR / "audio_analysis.sqlite"


def init_db() -> None:
    p = db_path()
    conn = sqlite3.connect(p)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS audio_analysis (
                audio_id TEXT PRIMARY KEY,
                algo_version INTEGER,
                config_hash TEXT,
                bpm REAL,
                bpm_conf REAL,
                bpm_corr REAL,
                key_camelot TEXT,
                key_strength REAL,
                lufs REAL,
                dyn_complex REAL,
                onset_rate REAL,
                spec_centroid REAL,
                spec_rolloff REAL,
                energy REAL,
                energy_var REAL,
                analyzed_at TEXT,
                source TEXT,
                extras TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def compute_audio_id(path: Path) -> str:
    """Stable identifier for the audio file. Uses full SHA256 of the file contents.
    This is slower than partial hashing but robust and already available in the project.
    """
    return file_sha256(path)


def get_analysis(audio_id: str) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(db_path())
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM audio_analysis WHERE audio_id=?", (audio_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [c[1] for c in cur.execute("PRAGMA table_info(audio_analysis)").fetchall()]
        data = dict(zip(cols, row))
        # decode extras JSON if present
        extras = data.get("extras")
        if extras:
            try:
                extras_dict = json.loads(extras)  # type: ignore
            except Exception:
                extras_dict = extras
            if isinstance(extras_dict, dict):
                data["extras"] = extras_dict
                features_ext = extras_dict.get(_EXTRA_FEATURES_KEY)
                if isinstance(features_ext, dict):
                    for k, v in features_ext.items():
                        data.setdefault(k, v)
        return data
    finally:
        conn.close()


def upsert_analysis(audio_id: str, payload: Dict[str, Any]) -> None:
    init_db()
    conn = sqlite3.connect(db_path())
    try:
        cur = conn.cursor()
        # ensure analyzed_at
        payload = dict(payload)
        payload.setdefault("analyzed_at", datetime.utcnow().isoformat())

        extras_obj = payload.get("extras")
        extras_dict: Dict[str, Any] = {}
        if isinstance(extras_obj, dict):
            extras_dict = dict(extras_obj)
        elif extras_obj is not None and not isinstance(extras_obj, str):
            # Preserve non-dict payload for debugging
            extras_dict["legacy_extras"] = extras_obj

        cols = [
            "algo_version", "config_hash",
            "bpm", "bpm_conf", "bpm_corr",
            "key_camelot", "key_strength",
            "lufs", "dyn_complex", "onset_rate", "spec_centroid", "spec_rolloff",
            "energy", "energy_var",
            "analyzed_at", "source", "extras",
        ]
        extra_metrics = {
            k: _to_jsonable(payload.get(k))
            for k in payload.keys()
            if k not in cols and k not in {"extras"}
        }
        if extra_metrics:
            feat_bucket = extras_dict.get(_EXTRA_FEATURES_KEY)
            if not isinstance(feat_bucket, dict):
                feat_bucket = {}
            for k, v in extra_metrics.items():
                if v is not None:
                    feat_bucket[k] = v
            extras_dict[_EXTRA_FEATURES_KEY] = feat_bucket

        extras_json: str | None = None
        if extras_dict:
            try:
                extras_json = json.dumps(extras_dict)
            except Exception:
                extras_json = json.dumps({"error": "failed to encode extras"})
        payload["extras"] = extras_json

        values = [payload.get(c) for c in cols]
        cur.execute(
            """
            INSERT INTO audio_analysis (
                audio_id, algo_version, config_hash, bpm, bpm_conf, bpm_corr, key_camelot, key_strength,
                lufs, dyn_complex, onset_rate, spec_centroid, spec_rolloff, energy, energy_var,
                analyzed_at, source, extras
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(audio_id) DO UPDATE SET
                algo_version=excluded.algo_version,
                config_hash=excluded.config_hash,
                bpm=excluded.bpm,
                bpm_conf=excluded.bpm_conf,
                bpm_corr=excluded.bpm_corr,
                key_camelot=excluded.key_camelot,
                key_strength=excluded.key_strength,
                lufs=excluded.lufs,
                dyn_complex=excluded.dyn_complex,
                onset_rate=excluded.onset_rate,
                spec_centroid=excluded.spec_centroid,
                spec_rolloff=excluded.spec_rolloff,
                energy=excluded.energy,
                energy_var=excluded.energy_var,
                analyzed_at=excluded.analyzed_at,
                source=excluded.source,
                extras=excluded.extras
            """,
            [audio_id] + values,
        )
        conn.commit()
    finally:
        conn.close()


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float)) or value is None:
        return value
    if isinstance(value, bool):
        return value
    if _np is not None and isinstance(value, _np.generic):  # type: ignore[arg-type]
        try:
            return value.item()
        except Exception:
            return float(value)
    return value
