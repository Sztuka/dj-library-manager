from __future__ import annotations

from djlib.audio.features import bpm_correct_into_range, config_hash, energy_score_from_metrics
from djlib.tags import _to_camelot
from djlib.audio.essentia_backend import check_env


def test_bpm_correct_into_range_basic():
    # Pull up
    bpm, corr = bpm_correct_into_range(60, 80, 180)
    assert bpm == 120.0 and corr == 2.0
    # Push down
    bpm, corr = bpm_correct_into_range(220, 80, 180)
    assert bpm == 110.0 and corr is not None and round(corr, 2) == 0.5
    # In range stays
    bpm, corr = bpm_correct_into_range(128, 80, 180)
    assert bpm == 128.0 and corr == 1.0
    # None/invalid
    bpm, corr = bpm_correct_into_range(None, 80, 180)
    assert bpm is None and corr is None


def test_to_camelot_mapping():
    assert _to_camelot("C") == "8B"
    assert _to_camelot("Am") == "8A"
    assert _to_camelot("7d") == "7B"  # 'd' treated as major
    assert _to_camelot("F# minor") == "11A"
    assert _to_camelot("Bb major") in ("6B", "6B")


def test_config_hash_stability():
    a = {"target_bpm": [80, 180], "ver": 1}
    b = {"ver": 1, "target_bpm": [80, 180]}  # different order
    assert config_hash(a) == config_hash(b)


def test_check_env_shape():
    info = check_env()
    assert isinstance(info, dict)
    # Must report at least binding availability
    assert "essentia_available" in info
    # Optional CLI keys (present in our implementation)
    assert "essentia_cli_available" in info