#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Any, List

import numpy as np
import pandas as pd

from djlib.bucketing.simple_ml import SimpleMLBucketAssigner


DJ_GENRES = ['Electronic', 'Hip-Hop', 'Pop', 'Rock', 'Jazz', 'Classical', 'Folk', 'International', 'Experimental']
GENRE_TO_BUCKET = {
    'Electronic': 'CLUB TECHNO',
    'Hip-Hop': 'URBAN HIP HOP',
    'Pop': 'MAIN POP',
    'Rock': 'ROCK ALTERNATIVE',
    'Jazz': 'JAZZ CLASSIC',
    'Classical': 'CLASSICAL',
    'Folk': 'FOLK INDIE',
    'International': 'WORLD',
    'Experimental': 'EXPERIMENTAL',
}


def build_track_dict(fma_row: pd.Series, label: str) -> Dict[str, Any]:
    d: Dict[str, Any] = {'bucket': label}

    # BPM
    if ('rhythm', 'bpm') in fma_row.index:
        val = fma_row[('rhythm', 'bpm')]
        if isinstance(val, pd.Series):
            val = val.iloc[0]
        d['bpm_detected'] = float(val)

    # MFCC means (13)
    for i in range(13):
        key = ('mfcc', 'mean', f'{i+1:02d}')
        if key in fma_row.index:
            val = fma_row[key]
            if isinstance(val, pd.Series):
                val = val.iloc[0]
            d[f'mfcc_{i}'] = float(val)
    # We don't have mfcc_std in FMA features.csv; leave zeros

    # Chroma (CENS) means (12)
    for i in range(12):
        key = ('chroma_cens', 'mean', f'{i+1:02d}')
        if key in fma_row.index:
            val = fma_row[key]
            if isinstance(val, pd.Series):
                val = val.iloc[0]
            d[f'chroma_{i}'] = float(val)

    # Spectral
    spec_map = {
        'spec_centroid': ('spectral_centroid', 'mean'),
        'spec_rolloff': ('spectral_rolloff', 'mean'),
        'zero_crossing_rate': ('zcr', 'mean'),
    }
    for out_k, fma_k in spec_map.items():
        if fma_k in fma_row.index:
            val = fma_row[fma_k]
            if isinstance(val, pd.Series):
                val = val.iloc[0]
            d[out_k] = float(val)

    # Defaults to fill missing fields expected by model
    defaults = {
        'energy_score': 0.5,
        'danceability': 0.5,
        'chords_changes_rate': 0.1,
        'tuning_diatonic_strength': 0.5,
        'dyn_complex': 0.5,
        'onset_rate': 0.1,
        'lufs': -14.0,
    }
    for k, v in defaults.items():
        d.setdefault(k, v)

    return d


def main() -> None:
    p = argparse.ArgumentParser(description='Train ML bucket model from FMA metadata')
    p.add_argument('--fma-root', default='data/fma/fma_metadata', help='Path to FMA metadata directory')
    p.add_argument('--balanced', action='store_true', help='Undersample classes to balance the dataset')
    p.add_argument('--per-class', type=int, default=500, help='Samples per class when --balanced is used')
    p.add_argument('--limit', type=int, default=0, help='Optional global limit for quick runs')
    p.add_argument('--out', default='models/fma_trained_model_balanced.pkl', help='Output model path')
    args = p.parse_args()

    root = Path(args.fma_root)
    tracks = pd.read_csv(root / 'tracks.csv', header=[0, 1], index_col=0)
    feats = pd.read_csv(root / 'features.csv', header=[0, 1, 2], index_col=0)

    fma_dj = tracks[tracks['track', 'genre_top'].isin(DJ_GENRES)].copy()
    fma_dj['bucket'] = fma_dj['track', 'genre_top'].map(GENRE_TO_BUCKET)

    ids = fma_dj.index.intersection(feats.index)
    fma_dj = fma_dj.loc[ids]
    feats = feats.loc[ids]

    if args.balanced:
        all_ids: List[int] = []
        for bucket in sorted(fma_dj['bucket'].unique()):
            bucket_ids = fma_dj[fma_dj['bucket'] == bucket].index
            take = min(args.per_class, len(bucket_ids))
            sampled = pd.Index(np.random.default_rng(42).choice(bucket_ids, size=take, replace=False))
            all_ids.extend(sampled.tolist())
        ids = pd.Index(all_ids)
    
    if args.limit and args.limit > 0:
        ids = ids[:args.limit]

    labeled_tracks: List[Dict[str, Any]] = []
    for tid in ids:
        row = feats.loc[tid]
        label_val = fma_dj.loc[tid, 'bucket']
        if isinstance(label_val, pd.Series):
            label_val = label_val.iloc[0]
        label = str(label_val)
        labeled_tracks.append(build_track_dict(row, label))

    uniq_buckets = sorted({t['bucket'] for t in labeled_tracks})
    print(f"Prepared {len(labeled_tracks)} samples for training. Buckets: {uniq_buckets}")

    assigner = SimpleMLBucketAssigner()
    assigner.train(labeled_tracks)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    assigner.save_model(out_path)
    print(f"Model saved: {out_path}")


if __name__ == '__main__':
    main()
