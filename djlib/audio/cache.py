from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

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
        if data.get("extras"):
            try:
                data["extras"] = json.loads(data["extras"])  # type: ignore
            except Exception:
                pass
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

        # JSON encode extras if present
        extras = payload.get("extras")
        if extras is not None and not isinstance(extras, str):
            try:
                payload["extras"] = json.dumps(extras)
            except Exception:
                payload["extras"] = str(extras)

        cols = [
            "algo_version", "config_hash",
            "bpm", "bpm_conf", "bpm_corr",
            "key_camelot", "key_strength",
            "lufs", "dyn_complex", "onset_rate", "spec_centroid", "spec_rolloff",
            "energy", "energy_var",
            "analyzed_at", "source", "extras",
        ]
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
