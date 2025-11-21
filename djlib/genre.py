from __future__ import annotations
from typing import Dict, Tuple, List
import re
from djlib.extern import lastfm_toptags
from djlib.taxonomy import normalize_label  # reuse normalization
from pathlib import Path
import yaml

BASE_DIR = Path(__file__).resolve().parents[1]
TAXONOMY_MAP_PATH = BASE_DIR / "taxonomy_map.yml"


def load_taxonomy_map() -> Dict[str, str]:
    if TAXONOMY_MAP_PATH.exists():
        try:
            data = yaml.safe_load(TAXONOMY_MAP_PATH.read_text(encoding="utf-8")) or {}
            m = data.get("map", {}) if isinstance(data, dict) else {}
            out = {}
            for k, v in (m or {}).items():
                out[(k or "").strip().lower()] = normalize_label(v)
            return out
        except Exception:
            return {}
    return {}


def _simplify(tag: str) -> str:
    t = (tag or "").strip().lower()
    # common cleanups
    t = re.sub(r"[^a-z0-9&+\-/\s]", " ", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    # unify synonyms quickly
    repl = {
        "dnb": "drum and bass",
        "drum & bass": "drum and bass",
        "rnb": "r&b",
        "ukg": "uk garage",
        "edm": "electronic",
    }
    return repl.get(t, t)


def external_genre_votes(artist: str, title: str) -> Dict[str, float]:
    """Aggregate external tags from Last.fm into votes with weights."""
    artist = (artist or "").strip()
    title = (title or "").strip()
    votes: Dict[str, float] = {}
    # Last.fm weighted by log(count)
    lf = lastfm_toptags(artist, title)
    for tag, cnt in (lf or {}).items():
        if not tag:
            continue
        x = _simplify(tag)
        # log scale; cap
        import math
        w = min(0.9, math.log(max(1, cnt + 1), 10))
        votes[x] = votes.get(x, 0.0) + w
    # Discogs removed
    # normalize small noise
    return {k: v for k, v in votes.items() if v >= 0.2}


def suggest_bucket_from_votes(votes: Dict[str, float], mapping: Dict[str, str]) -> Tuple[str, float, List[Tuple[str, float, str]]]:
    """Map external tags to taxonomy buckets using mapping; return (bucket, confidence, breakdown)
    Breakdown is list of (tag, weight, mapped_bucket|"").
    Confidence is sum of weights to the winning bucket divided by total weights (0..1).
    """
    if not votes:
        return "", 0.0, []
    bucket_scores: Dict[str, float] = {}
    breakdown: List[Tuple[str, float, str]] = []
    total = 0.0
    for tag, w in votes.items():
        total += w
        b = mapping.get(tag) or ""
        breakdown.append((tag, w, b))
        if b:
            bucket_scores[b] = bucket_scores.get(b, 0.0) + w
    if not bucket_scores:
        # no mapping configured
        return "", 0.0, breakdown
    # pick best
    best_bucket, best_score = max(bucket_scores.items(), key=lambda kv: kv[1])
    conf = (best_score / total) if total > 0 else 0.0
    return best_bucket, conf, breakdown
