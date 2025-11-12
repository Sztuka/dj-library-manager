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
import numpy as np
try:
    from scipy import stats  # type: ignore
except Exception:  # scipy may be missing; gate features that require it
    stats = None  # type: ignore


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
                
                # Additional features for genre classification
                metrics["zero_crossing_rate"] = get_scalar('lowlevel.zerocrossingrate')
                metrics["danceability"] = get_scalar('rhythm.danceability')
                metrics["chords_changes_rate"] = get_scalar('tonal.chords_changes_rate')
                metrics["tuning_diatonic_strength"] = get_scalar('tonal.tuning_diatonic_strength')
                
                # Aggregate MFCC coefficients (mean across time)
                try:
                    mfcc_vals = results['lowlevel.mfcc']
                    if hasattr(mfcc_vals, '__len__') and len(mfcc_vals) > 0:
                        mfcc_mean = np.mean(mfcc_vals, axis=0) if getattr(mfcc_vals, 'ndim', 1) > 1 else mfcc_vals
                        if hasattr(mfcc_mean, '__len__') and len(mfcc_mean) >= 13:
                            for i in range(13):  # First 13 MFCC coefficients (means)
                                metrics[f"mfcc_{i}"] = float(mfcc_mean[i])
                            # Per-coefficient std
                            mfcc_std = np.std(mfcc_vals, axis=0) if getattr(mfcc_vals, 'ndim', 1) > 1 else np.zeros(13)
                            for i in range(min(13, len(mfcc_std))):
                                metrics[f"mfcc_std_{i}"] = float(mfcc_std[i])
                            # Aggregate kurtosis/skew if scipy is available
                            if stats is not None and getattr(mfcc_vals, 'ndim', 1) > 1:
                                try:
                                    mfcc_kurtosis = float(np.mean([stats.kurtosis(mfcc_vals[:, i]) for i in range(13)]))
                                    mfcc_skew = float(np.mean([stats.skew(mfcc_vals[:, i]) for i in range(13)]))
                                    metrics["mfcc_kurtosis_mean"] = mfcc_kurtosis
                                    metrics["mfcc_skew_mean"] = mfcc_skew
                                except Exception:
                                    pass
                except Exception as e:
                    print(f"MFCC extraction failed: {e}")
                
                # Aggregate chroma features (HPCP - Harmonic Pitch Class Profile)
                try:
                    hpcp_vals = results['tonal.hpcp']
                    if hasattr(hpcp_vals, '__len__') and len(hpcp_vals) > 0:
                        hpcp_mean = np.mean(hpcp_vals, axis=0) if getattr(hpcp_vals, 'ndim', 1) > 1 else hpcp_vals
                        if hasattr(hpcp_mean, '__len__') and len(hpcp_mean) >= 12:
                            for i in range(12):  # 12 chroma bins (means)
                                metrics[f"chroma_{i}"] = float(hpcp_mean[i])
                            # Per-bin std
                            if getattr(hpcp_vals, 'ndim', 1) > 1:
                                hpcp_std = np.std(hpcp_vals, axis=0)
                                for i in range(min(12, len(hpcp_std))):
                                    metrics[f"chroma_std_{i}"] = float(hpcp_std[i])
                                # Aggregate kurtosis if scipy is available
                                if stats is not None:
                                    try:
                                        chroma_kurtosis = float(np.mean([stats.kurtosis(hpcp_vals[:, i]) for i in range(12)]))
                                        metrics["chroma_kurtosis_mean"] = chroma_kurtosis
                                    except Exception:
                                        pass
                except Exception as e:
                    print(f"Chroma extraction failed: {e}")
                
                # Additional spectral features (robust to Pool semantics)
                try:
                    # Spectral centroid std
                    try:
                        centroid_vals = results['lowlevel.spectral_centroid']
                        if hasattr(centroid_vals, '__len__') and len(centroid_vals) > 0:
                            metrics["spec_centroid_std"] = float(np.std(centroid_vals))
                    except Exception:
                        pass
                    # Spectral rolloff std
                    try:
                        rolloff_vals = results['lowlevel.spectral_rolloff']
                        if hasattr(rolloff_vals, '__len__') and len(rolloff_vals) > 0:
                            metrics["spec_rolloff_std"] = float(np.std(rolloff_vals))
                    except Exception:
                        pass
                    # Spectral bandwidth (approximate)
                    try:
                        _ = results['lowlevel.spectral_energyband_low']
                        _ = results['lowlevel.spectral_energyband_high']
                        metrics["spec_bandwidth_mean"] = metrics.get("spec_centroid", 0)
                        metrics["spec_bandwidth_std"] = metrics.get("spec_centroid_std", 0)
                    except Exception:
                        pass
                    # Spectral contrast
                    try:
                        contrast_vals = results['lowlevel.spectral_contrast']
                        if hasattr(contrast_vals, '__len__') and len(contrast_vals) > 0:
                            metrics["spec_contrast_mean"] = float(np.mean(contrast_vals))
                            metrics["spec_contrast_std"] = float(np.std(contrast_vals))
                    except Exception:
                        pass
                    # Tonnetz
                    try:
                        tonnetz_vals = results['tonal.tonnetz']
                        if hasattr(tonnetz_vals, '__len__') and len(tonnetz_vals) > 0:
                            metrics["tonnetz_mean"] = float(np.mean(tonnetz_vals))
                            metrics["tonnetz_std"] = float(np.std(tonnetz_vals))
                    except Exception:
                        pass
                    # Spectral dynamics
                    try:
                        flux_vals = results['lowlevel.spectral_flux']
                        if hasattr(flux_vals, '__len__') and len(flux_vals) > 0:
                            metrics["spec_flux_mean"] = float(np.mean(flux_vals))
                            metrics["spec_flux_std"] = float(np.std(flux_vals))
                    except Exception:
                        pass
                    try:
                        flat_vals = results['lowlevel.spectral_flatness_db']
                        if hasattr(flat_vals, '__len__') and len(flat_vals) > 0:
                            metrics["spec_flatness_mean"] = float(np.mean(flat_vals))
                            metrics["spec_flatness_std"] = float(np.std(flat_vals))
                    except Exception:
                        pass
                    try:
                        hfc_vals = results['lowlevel.hfc']
                        if hasattr(hfc_vals, '__len__') and len(hfc_vals) > 0:
                            metrics["hfc_mean"] = float(np.mean(hfc_vals))
                            metrics["hfc_std"] = float(np.std(hfc_vals))
                    except Exception:
                        pass
                except Exception as e:
                    print(f"Additional spectral features extraction failed: {e}")
                
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
            "zero_crossing_rate": metrics.get("zero_crossing_rate"),
            "danceability": metrics.get("danceability"),
            "chords_changes_rate": metrics.get("chords_changes_rate"),
            "tuning_diatonic_strength": metrics.get("tuning_diatonic_strength"),
            "energy": energy,
            "energy_var": metrics.get("energy_var"),
            "analyzed_at": datetime.utcnow().isoformat(),
            "source": src,
            "extras": {"notes": "with genre features"},
        }
        
        # Add MFCC coefficients
        for i in range(13):
            mfcc_key = f"mfcc_{i}"
            if mfcc_key in metrics:
                payload[mfcc_key] = metrics[mfcc_key]
        
        # Add MFCC statistics
        mfcc_stat_keys = [f"mfcc_std_{i}" for i in range(13)] + ["mfcc_kurtosis_mean", "mfcc_skew_mean"]
        for key in mfcc_stat_keys:
            if key in metrics:
                payload[key] = metrics[key]
        
        # Add chroma features
        for i in range(12):
            chroma_key = f"chroma_{i}"
            if chroma_key in metrics:
                payload[chroma_key] = metrics[chroma_key]
        
        # Add chroma statistics
        chroma_stat_keys = [f"chroma_std_{i}" for i in range(12)] + ["chroma_kurtosis_mean"]
        for key in chroma_stat_keys:
            if key in metrics:
                payload[key] = metrics[key]
        
        # Add additional spectral features
        spectral_keys = [
            "spec_centroid_std", "spec_rolloff_std", "spec_bandwidth_mean", "spec_bandwidth_std",
            "spec_contrast_mean", "spec_contrast_std", "tonnetz_mean", "tonnetz_std",
            "spec_flux_mean", "spec_flux_std", "spec_flatness_mean", "spec_flatness_std",
            "hfc_mean", "hfc_std"
        ]
        for key in spectral_keys:
            if key in metrics:
                payload[key] = metrics[key]

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
