from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import json
import shutil
import subprocess
import tempfile
import os

from .cache import compute_audio_id, get_analysis, upsert_analysis, init_db
from .features import bpm_correct_into_range, config_hash, energy_score_from_metrics
from . import ALGO_VERSION
from djlib.tags import _to_camelot  # reuse existing Camelot mapping
from djlib.config import LOGS_DIR
import yaml


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
    # Check repo-local vendor path: <repo>/bin/mac/
    try:
        repo_root = Path(__file__).resolve().parents[2]
        candidates = [
            repo_root / "bin" / "mac" / "essentia_streaming_extractor_music",
            repo_root / "bin" / "mac" / "streaming_extractor_music",
        ]
        for c in candidates:
            if c.exists() and c.is_file():
                return str(c)
    except Exception:
        pass
    return None


def check_env() -> Dict[str, Any]:
    """Report availability of Essentia and basic runtime details."""
    ess, es = _try_import_essentia()
    cli_bin = _find_extractor_binary()
    docker_bin = shutil.which("docker")
    docker_img = os.getenv("DJLIB_ESSENTIA_IMAGE", "djlib-essentia:local")
    docker_enabled = os.getenv("DJLIB_ESSENTIA_DOCKER", "0").strip() in {"1", "true", "yes"}
    if ess is None:
        return {
            "essentia_available": False,
            "essentia_cli_available": bool(cli_bin),
            "cli_binary": cli_bin,
            "essentia_docker_available": bool(docker_bin),
            "essentia_docker_enabled": docker_enabled,
            "docker_image": docker_img if docker_enabled else None,
            "details": "Essentia Python bindings not available. Use Conda or Homebrew. CLI fallback supported if extractor binary is installed.",
        }
    ver = getattr(ess, "__version__", "?")
    return {
        "essentia_available": True,
        "essentia_cli_available": bool(cli_bin),
        "cli_binary": cli_bin,
        "essentia_docker_available": bool(docker_bin),
        "essentia_docker_enabled": docker_enabled,
        "docker_image": docker_img if docker_enabled else None,
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
    try:
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
            # Use Python Essentia MusicExtractor
            src = "essentia"
            try:
                # Create MusicExtractor and run on file
                extractor = es.MusicExtractor()
                audio, results = extractor(str(p))
                
                # Debug: print available keys
                # print(f"DEBUG: results type: {type(results)}, descriptors: {results.descriptorNames()[:10]}...")
                
                # Helper to get scalar value from Pool
                def get_scalar(key):
                    val = results[key]
                    if isinstance(val, (int, float)):
                        return float(val)
                    elif hasattr(val, '__len__'):
                        if len(val) == 1:
                            return float(val[0])
                        elif len(val) > 1:
                            # For multi-element arrays, take mean or first value
                            import numpy as np
                            return float(np.mean(val))
                    return float(val)
                
                # Extract BPM
                bpm = get_scalar('rhythm.bpm')
                
                # Extract Key - strings are arrays of chars or single string
                key_key_val = results['tonal.key_edma.key']
                key_scale_val = results['tonal.key_edma.scale']
                if hasattr(key_key_val, '__len__') and not isinstance(key_key_val, str):
                    key_key = ''.join(str(c) for c in key_key_val)
                else:
                    key_key = str(key_key_val)
                if hasattr(key_scale_val, '__len__') and not isinstance(key_scale_val, str):
                    key_scale = ''.join(str(c) for c in key_scale_val)
                else:
                    key_scale = str(key_scale_val)
                key_strength = get_scalar('tonal.key_edma.strength')
                key_raw = f"{key_key} {key_scale}".strip()
                key_camelot = _to_camelot(key_raw)
                
                # Extract Energy - no direct mood_energy in this version, use spectral energy
                energy = get_scalar('lowlevel.spectral_energy')
                
                # Extract low-level metrics
                metrics["dyn_complex"] = get_scalar('lowlevel.dynamic_complexity')
                metrics["lufs"] = get_scalar('lowlevel.loudness_ebu128.integrated')
                metrics["spec_centroid"] = get_scalar('lowlevel.spectral_centroid')
                metrics["spec_rolloff"] = get_scalar('lowlevel.spectral_rolloff')
                metrics["onset_rate"] = get_scalar('rhythm.onset_rate')
                
            except Exception as e:
                print(f"Python Essentia analysis failed: {e}")
                # Fall back to None
                pass
        elif cli_bin or shutil.which("docker"):
            # Fallback: call Essentia streaming extractor binary and parse JSON/YAML output.
            src = "essentia-cli"
            debug_mode = os.getenv("DJLIB_ESSENTIA_DEBUG", "0").strip() in {"1", "true", "yes"}
            print(f"DEBUG: debug_mode={debug_mode}, cli_bin={cli_bin}, docker={shutil.which('docker')}")  # DEBUG
            if debug_mode:
                # Use persistent LOGS/essentia_tmp/<audio_id>/features.json for debugging
                debug_dir = LOGS_DIR / "essentia_tmp" / aid
                debug_dir.mkdir(parents=True, exist_ok=True)
                out_json = debug_dir / "features.json"
                td = None  # no cleanup needed
            else:
                # Use ephemeral temp dir
                td = tempfile.mkdtemp()
                out_json = Path(td) / "features.json"

            try:
                # Prepare command
                if cli_bin:
                    cmd = [cli_bin, str(p), str(out_json)]
                else:
                    docker_img = os.getenv("DJLIB_ESSENTIA_IMAGE", "djlib-essentia:local")
                    docker_enabled = os.getenv("DJLIB_ESSENTIA_DOCKER", "0").strip() in {"1", "true", "yes"}
                    if not docker_enabled:
                        cmd = []
                    else:
                        in_name = p.name
                        cmd = [
                            shutil.which("docker") or "docker",
                            "run",
                            "--rm",
                            "-v",
                            f"{str(p)}:/in/{in_name}:ro",
                            "-v",
                            f"{str(out_json.parent)}:/out",
                            docker_img,
                            f"/in/{in_name}",
                            "/out/features.json",
                        ]

                data: Dict[str, Any] = {}
                if cmd:
                    proc = subprocess.run(cmd, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    # Write stdout/stderr to logs for debugging
                    try:
                        LOGS_DIR.mkdir(parents=True, exist_ok=True)
                        (LOGS_DIR / "essentia_cli_last.stdout").write_bytes(proc.stdout or b"")
                        (LOGS_DIR / "essentia_cli_last.stderr").write_bytes(proc.stderr or b"")
                    except Exception:
                        pass

                # If extractor produced an output file, parse it (JSON first, YAML fallback)
                if out_json.exists():
                    raw = out_json.read_text(encoding="utf-8", errors="ignore")
                    try:
                        data = json.loads(raw)
                    except Exception:
                        try:
                            data = yaml.safe_load(raw) or {}
                        except Exception:
                            data = {}
                    # also copy raw output to logs
                    try:
                        LOGS_DIR.mkdir(parents=True, exist_ok=True)
                        (LOGS_DIR / "essentia_cli_last.output.json").write_text(raw, encoding="utf-8")
                    except Exception:
                        pass

                # Extract BPM
                try:
                    bpm = float(data.get("rhythm", {}).get("bpm"))
                except Exception:
                    pass
                # Extract Key and strength
                try:
                    tonal = data.get("tonal", {})
                    key_key = (tonal.get("key_key") or "").strip()
                    key_scale = (tonal.get("key_scale") or "").strip()
                    key_strength = tonal.get("key_strength")
                    if key_key:
                        key_raw = f"{key_key} {key_scale}".strip()
                        cam = _to_camelot(key_raw)
                        key_camelot = cam or key_camelot
                except Exception:
                    pass
                # Low-level metrics
                try:
                    low = data.get("lowlevel", {})
                    metrics["dyn_complex"] = low.get("dynamic_complexity")
                    lufs = None
                    try:
                        ebu = low.get("loudness_ebu128", {})
                        lufs = ebu.get("integrated")
                    except Exception:
                        pass
                    if lufs is None:
                        lufs = low.get("loudness")
                    metrics["lufs"] = lufs
                    sc = low.get("spectral_centroid", {})
                    sr = low.get("spectral_rolloff", {})
                    metrics["spec_centroid"] = sc.get("mean") if isinstance(sc, dict) else None
                    metrics["spec_rolloff"] = sr.get("mean") if isinstance(sr, dict) else None
                    rhy = data.get("rhythm", {})
                    metrics["onset_rate"] = rhy.get("onset_rate")
                except Exception:
                    pass
                # Highlevel mood/energy (0..1)
                try:
                    hl = data.get("highlevel", {})
                    mood_energy = hl.get("mood_energy", {})
                    allvals = mood_energy.get("all", {}) if isinstance(mood_energy, dict) else {}
                    e_high = allvals.get("high")
                    if isinstance(e_high, (int, float)):
                        energy = float(e_high)
                except Exception:
                    pass

            except subprocess.CalledProcessError as e:
                try:
                    LOGS_DIR.mkdir(parents=True, exist_ok=True)
                    (LOGS_DIR / "essentia_cli_last.stderr").write_bytes(e.stderr or b"")
                    (LOGS_DIR / "essentia_cli_last.stdout").write_bytes(e.stdout or b"")
                except Exception:
                    pass
            finally:
                # Cleanup temp dir if used
                if td and not debug_mode:
                    shutil.rmtree(td, ignore_errors=True)

        # Apply BPM correction into target range
        bpm_corr_val, corr_factor = bpm_correct_into_range(bpm, *target_bpm_range)

        # If no direct energy from highlevel, compute a rough score from metrics
        if energy is None:
            energy = energy_score_from_metrics({k: v for k, v in metrics.items() if isinstance(v, (int, float))})

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
    except Exception as e:
        # Log any exception in analyze
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            (LOGS_DIR / "analyze_exception.txt").write_text(str(e), encoding="utf-8")
        except Exception:
            pass
        # Return empty result to avoid crashing CLI
        return {}
