from __future__ import annotations

from pathlib import Path
from typing import Dict, Any

import numpy as np

from djlib.bucketing.simple_ml import SimpleMLBucketAssigner


def test_feature_vector_length_consistency():
    assigner = SimpleMLBucketAssigner()
    names = assigner._get_feature_names()

    # Build a track dict with all features present
    track: Dict[str, Any] = {name: 0.0 for name in names}
    # Also include 'bucket' to mimic training sample shape
    track['bucket'] = 'TEST'

    vec = assigner._extract_features(track)
    assert vec.shape[0] == len(names)


def test_predict_with_minimal_features():
    # Minimal track dict (many features missing) should not crash
    assigner = SimpleMLBucketAssigner()
    # Fake a very simple model: create a stub with predict_proba signature
    class StubModel:
        def predict_proba(self, X):
            # two classes, equal probability
            return np.array([[0.5, 0.5] for _ in range(X.shape[0])])

        @property
        def feature_importances_(self):
            return np.zeros(len(assigner._get_feature_names()))

    assigner.model = StubModel()
    assigner.reverse_encoder = {0: 'A', 1: 'B'}

    bucket, conf = assigner.predict({'bpm_detected': 120})
    assert bucket in {'A', 'B'}
    assert 0.0 <= conf <= 1.0
