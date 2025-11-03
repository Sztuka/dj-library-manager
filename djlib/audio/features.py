from __future__ import annotations

import hashlib
import json
from typing import Dict, Tuple


def bpm_correct_into_range(bpm: float | None, lo: int = 80, hi: int = 180) -> Tuple[float | None, float | None]:
    """Corrects BPM by multiplying/dividing by 2 into [lo, hi].
    Returns (corrected_bpm, correction_factor).
    """
    if bpm is None or bpm <= 0:
        return None, None
    corr = 1.0
    val = float(bpm)
    # Pull up
    while val < lo:
        val *= 2.0
        corr *= 2.0
        if val > 8 * hi:  # safety
            break
    # Push down
    while val > hi:
        val /= 2.0
        corr /= 2.0
        if val < lo / 8:  # safety
            break
    return round(val, 2), corr


def config_hash(config: Dict) -> str:
    """Stable hash of relevant config to guard cache consistency."""
    payload = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()  # nosec: B303


def energy_score_from_metrics(metrics: Dict[str, float] | None, weights: Dict[str, float] | None = None) -> float | None:
    """Combine normalized metrics into a single energy score [0..1].
    This is a placeholder; real implementation will include per‑library calibration.
    """
    if not metrics:
        return None
    w = weights or {
        "lufs": 0.25,
        "dyn_complex": 0.25,
        "onset_rate": 0.25,
        "spec_centroid": 0.125,
        "spec_rolloff": 0.125,
    }
    total_w = sum(w.values()) or 1.0
    score = 0.0
    for k, wk in w.items():
        v = metrics.get(k)
        if v is None:
            continue
        # Assume v is already normalized 0..1 (to be calibrated later)
        score += wk * max(0.0, min(1.0, float(v)))
    return round(score / total_w, 4)
