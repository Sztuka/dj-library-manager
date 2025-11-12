from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional, Tuple
import yaml
from pathlib import Path


class BucketAssigner(ABC):
    """Abstract base class for bucket assignment strategies."""

    @abstractmethod
    def predict(self, track: Dict[str, Any]) -> Tuple[str, float]:
        """Predict bucket for a single track.

        Args:
            track: Dictionary with track metadata

        Returns:
            Tuple of (predicted_bucket, confidence_score)
        """
        pass

    def predict_batch(self, tracks: List[Dict[str, Any]]) -> List[Tuple[str, float]]:
        """Predict buckets for multiple tracks.

        Args:
            tracks: List of track dictionaries

        Returns:
            List of (bucket, confidence) tuples
        """
        return [self.predict(track) for track in tracks]

    @abstractmethod
    def train(self, labeled_tracks: List[Dict[str, Any]]) -> None:
        """Train the assigner on labeled data.

        Args:
            labeled_tracks: List of tracks with 'bucket' field
        """
        pass

    def export_predictions_to_csv(self, tracks: List[Dict[str, Any]], path: Path) -> None:
        """Export predictions to CSV format.

        Args:
            tracks: List of track dictionaries
            path: Output CSV path
        """
        import csv

        predictions = self.predict_batch(tracks)

        with path.open('w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['track_id', 'predicted_bucket', 'confidence'])

            for track, (bucket, confidence) in zip(tracks, predictions):
                track_id = track.get('filename', track.get('track_id', 'unknown'))
                writer.writerow([track_id, bucket, f"{confidence:.3f}"])

    def learn_from_feedback(self, feedback_csv_path: Path) -> None:
        """Learn from user feedback CSV.

        Args:
            feedback_csv_path: Path to CSV with columns: track_id, correct_bucket
        """
        import csv

        feedback_tracks = []
        with feedback_csv_path.open('r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                feedback_tracks.append({
                    'track_id': row['track_id'],
                    'bucket': row['correct_bucket']
                })

        if feedback_tracks:
            self.train(feedback_tracks)


def load_rules_from_yaml(rules_path: Path) -> Dict[str, Any]:
    """Load bucket assignment rules from YAML file.

    Args:
        rules_path: Path to YAML rules file

    Returns:
        Dictionary with rules configuration
    """
    with rules_path.open('r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}