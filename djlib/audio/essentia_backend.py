from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .cache import compute_audio_id, get_analysis, upsert_analysis, init_db
from .features import bpm_correct_into_range, config_hash
from . import ALGO_VERSION


def _try_import_essentia():
    try:
        import essentia  # type: ignore
        from essentia import standard as es  # type: ignore
        return essentia, es
    except Exception:
        return None, None


def check_env() -> Dict[str, Any]:
    """Report availability of Essentia and basic runtime details."""
    ess, es = _try_import_essentia()
    if ess is None:
        return {
            "essentia_available": False,
            "details": "Essentia not installed. Install via Homebrew (brew install essentia) or conda.",
        }
    ver = getattr(ess, "__version__", "?")
    return {
        "essentia_available": True,
        "version": ver,
    }


def analyze(
    path: Path | str,
    *,
    target_bpm_range: Tuple[int, int] = (80, 180),
    recompute: bool = False,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Analyze one audio file and return a dictionary with detected metrics.

    This is a skeleton implementation: it integrates cache and wiring, and
    returns empty metrics if Essentia is unavailable. Real detectors will be
    plugged in in subsequent iterations.
    """
    p = Path(path)
    init_db()
    aid = compute_audio_id(p)
    cfg = config or {"target_bpm": list(target_bpm_range)}
    ch = config_hash(cfg)

    if not recompute:
        cached = get_analysis(aid)
        if cached and cached.get("config_hash") == ch and int(cached.get("algo_version") or 0) == ALGO_VERSION:
            return cached

    ess, es = _try_import_essentia()

    bpm = None
    bpm_conf = None
    key_camelot = None
    key_strength = None
    energy = None
    metrics: Dict[str, Any] = {}
    src = "stub"

    if ess is not None and es is not None:
        # Placeholder: we don't compute real features yet; wire stays the same.
        # In future iterations, load audio, compute BPM/Key/Energy via Essentia.
        src = "essentia"
        # metrics[...] could include raw measures for debug once implemented

    # Apply BPM correction into target range
    bpm_corr_val, corr_factor = bpm_correct_into_range(bpm, *target_bpm_range)

    payload = {
        "algo_version": ALGO_VERSION,
        "config_hash": ch,
        "bpm": bpm_corr_val,
        "bpm_conf": bpm_conf,
        "bpm_corr": corr_factor,
        "key_camelot": key_camelot,
        "key_strength": key_strength,
        "lufs": metrics.get("lufs"),
        "dyn_complex": metrics.get("dyn_complex"),
        "onset_rate": metrics.get("onset_rate"),
        "spec_centroid": metrics.get("spec_centroid"),
        "spec_rolloff": metrics.get("spec_rolloff"),
        "energy": energy,
        "energy_var": metrics.get("energy_var"),
        "analyzed_at": datetime.utcnow().isoformat(),
        "source": src,
        "extras": {"notes": "skeleton backend"},
    }

    upsert_analysis(aid, payload)
    result = dict(payload)
    result["audio_id"] = aid
    return result
