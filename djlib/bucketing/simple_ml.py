# NOTE: See docs/BUCKETING_LEGACY.md – bucketing/ML here is legacy and currently not active.
"""
Legacy placeholder for historical FMA-based ML helpers.

The original SimpleMLBucketAssigner and related utilities have been removed.
Future implementations will train local models using Essentia features and
the DJ's own bucket/genre labels. Until that pipeline ships, any direct calls
into this module raise an explicit error so users do not accidentally rely on
the retired FMA models.
"""

from __future__ import annotations


def train_local_model(*args, **kwargs) -> None:
    """Placeholder for the future Essentia-based trainer."""
    raise RuntimeError("Legacy ML removed – new Essentia-based model not implemented yet.")


def predict_buckets(*args, **kwargs):
    """Placeholder for the future Essentia-based predictor."""
    raise RuntimeError("Legacy ML removed – new Essentia-based model not implemented yet.")
