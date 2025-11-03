from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import json
import shutil
import subprocess
import tempfile

from .cache import compute_audio_id, get_analysis, upsert_analysis, init_db
from .features import bpm_correct_into_range, config_hash
from . import ALGO_VERSION
from djlib.tags import _to_camelot  # reuse existing Camelot mapping


def _try_import_essentia():
    try:
        import essentia  # type: ignore
        from essentia import standard as es  # type: ignore
        return essentia, es
    except Exception:
        return None, None


def _find_extractor_binary() -> Optional[str]:
    """Try to find Essentia's streaming extractor binary on PATH.

    Common names:
    - 'essentia_streaming_extractor_music' (Homebrew)
    - 'streaming_extractor_music' (generic builds)
    """
    for name in ("essentia_streaming_extractor_music", "streaming_extractor_music"):
        p = shutil.which(name)
        if p:
            return p
    return None


def check_env() -> Dict[str, Any]:
    """Report availability of Essentia and basic runtime details."""
    ess, es = _try_import_essentia()
    cli_bin = _find_extractor_binary()
    if ess is None:
        return {
            "essentia_available": False,
            "essentia_cli_available": bool(cli_bin),
            "cli_binary": cli_bin,
            "details": "Essentia Python bindings not available. Use Conda or Homebrew. CLI fallback supported if extractor binary is installed.",
        }
    ver = getattr(ess, "__version__", "?")
    return {
        "essentia_available": True,
        "essentia_cli_available": bool(cli_bin),
        "cli_binary": cli_bin,
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
    cli_bin = _find_extractor_binary()

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
    elif cli_bin:
        # Fallback: call Essentia streaming extractor binary and parse JSON output.
        src = "essentia-cli"
        with tempfile.TemporaryDirectory() as td:
            out_json = Path(td) / "features.json"
            # Run: extractor <input> <output.json>
            cmd = [cli_bin, str(p), str(out_json)]
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if out_json.exists():
                    with out_json.open("r", encoding="utf-8") as f:
                        data = json.load(f)
                    # Extract BPM
                    try:
                        bpm = float(data.get("rhythm", {}).get("bpm"))
                    except Exception:
                        pass
                    # Extract Key and strength
                    try:
                        tonal = data.get("tonal", {})
                        key_key = (tonal.get("key_key") or "").strip()
                        key_scale = (tonal.get("key_scale") or "").strip()  # 'minor' or 'major'
                        key_strength = tonal.get("key_strength")
                        if key_key:
                            key_raw = f"{key_key} {key_scale}".strip()
                            cam = _to_camelot(key_raw)
                            key_camelot = cam or key_camelot
                    except Exception:
                        pass
                    # Optional: a few low-level metrics for future energy calculation
                    low = data.get("lowlevel", {})
                    try:
                        metrics["dyn_complex"] = low.get("dynamic_complexity")
                    except Exception:
                        pass
            except subprocess.CalledProcessError as e:
                # Leave as skeleton if CLI fails
                pass

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
