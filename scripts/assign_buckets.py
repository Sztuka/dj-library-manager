#!/usr/bin/env python3
"""
DJ Library Manager - Bucket Assignment Script

Assigns tracks to buckets using rules-based or ML-based approaches.
"""

from __future__ import annotations
import argparse
import csv
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

# Use project modules
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from djlib.config import LIB_ROOT
try:
    from djlib.bucketing.rules import RulesBucketAssigner
    rules_available = True
except ImportError:
    rules_available = False

LEGACY_ML_MSG = (
    "Legacy ML bucket assigner (FMA-based) has been removed. "
    "Future Essentia models will replace it once implemented."
)


def load_tracks_from_csv(csv_path: Path) -> List[Dict[str, Any]]:
    """Load tracks from CSV file."""
    tracks = []
    with csv_path.open('r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            tracks.append(row)
    return tracks


def assign_buckets_with_rules(tracks: List[Dict[str, Any]], rules_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Assign buckets using rules-based approach."""
    if not rules_available:
        print("ERROR: RulesBucketAssigner not available")
        return tracks

    assigner = RulesBucketAssigner(rules_path)

    for track in tracks:
        bucket, confidence = assigner.predict(track)
        track['bucket_suggest'] = bucket
        track['bucket_confidence'] = f"{confidence:.2f}"

    return tracks


def assign_buckets_with_ml(
    tracks: List[Dict[str, Any]],
    model_path: Path = Path('models/bucket_model.pkl'),
    feedback_csv: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """Legacy ML path retained for compatibility, but not active."""
    raise RuntimeError(LEGACY_ML_MSG)


def main() -> int:
    parser = argparse.ArgumentParser(description="Assign tracks to buckets")
    parser.add_argument(
        '--method',
        choices=['rules', 'ml'],
        default='rules',
        help='Assignment method (default: rules)'
    )
    parser.add_argument(
        '--input-csv',
        type=Path,
        help='Input CSV file (default: preview_inbox.csv)'
    )
    parser.add_argument(
        '--output-csv',
        type=Path,
        help='Output CSV file (default: bucket_predictions.csv)'
    )
    parser.add_argument(
        '--rules-file',
        type=Path,
        help='Custom rules YAML file'
    )
    parser.add_argument(
        '--model-path',
        type=Path,
        help='Path to ML model file (default: models/bucket_model.pkl)'
    )
    parser.add_argument(
        '--feedback-csv',
        type=Path,
        help='Feedback CSV for ML retraining'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug output'
    )

    args = parser.parse_args()

    # Default paths
    lib_root = Path(LIB_ROOT)
    input_csv = args.input_csv or lib_root / "preview_inbox.csv"
    output_csv = args.output_csv or lib_root / "bucket_predictions.csv"

    if not input_csv.exists():
        print(f"ERROR: Input CSV not found: {input_csv}")
        return 1

    # Load tracks
    print(f"Loading tracks from: {input_csv}")
    tracks = load_tracks_from_csv(input_csv)
    print(f"Loaded {len(tracks)} tracks")

    # Assign buckets
    if args.method == 'rules':
        if not rules_available:
            print("ERROR: Rules method requires djlib.bucketing.rules")
            return 1

        print("Assigning buckets using rules...")
        tracks = assign_buckets_with_rules(tracks, args.rules_file)

    elif args.method == 'ml':
        print(f"ERROR: {LEGACY_ML_MSG}")
        return 1

    # Handle feedback for ML retraining
    if args.feedback_csv and args.feedback_csv.exists():
        print(f"Processing feedback from: {args.feedback_csv}")
        # TODO: Implement feedback processing for ML
        print("Feedback processing not implemented yet")

    # Save results
    print(f"Saving predictions to: {output_csv}")
    with output_csv.open('w', encoding='utf-8', newline='') as f:
        if tracks:
            fieldnames = ['filename', 'bucket_suggest', 'bucket_confidence'] + list(tracks[0].keys())
            # Remove duplicates
            fieldnames = list(dict.fromkeys(fieldnames))

            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(tracks)

    print(f"Processed {len(tracks)} tracks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
