from __future__ import annotations
from typing import Dict, List, Any, Optional, Tuple
import pickle
from pathlib import Path
import numpy as np

from .base import BucketAssigner
from djlib.config import LOGS_DIR


class SimpleMLBucketAssigner(BucketAssigner):
    """ML-based bucket assigner using RandomForest and audio features."""

    def __init__(self, model_path: Optional[Path] = None):
        """Initialize with optional pre-trained model.

        Args:
            model_path: Path to pickled model file
        """
        self.model = None
        self.feature_names = []
        self.label_encoder = {}  # bucket -> int
        self.reverse_encoder = {}  # int -> bucket

        if model_path and model_path.exists():
            self.load_model(model_path)

    def _extract_features(self, track: Dict[str, Any]) -> np.ndarray:
        """Extract numerical features from track data for ML."""
        features = []

        # Audio features - basic rhythm/energy
        features.append(float(track.get('bpm_detected', 0) or 0))
        features.append(float(track.get('energy_score', 0) or 0))
        features.append(float(track.get('danceability', 0) or 0))
        features.append(float(track.get('zero_crossing_rate', 0) or 0))
        features.append(float(track.get('chords_changes_rate', 0) or 0))
        features.append(float(track.get('tuning_diatonic_strength', 0) or 0))

        # MFCC coefficients (first 13) - mean values
        for i in range(13):
            mfcc_key = f'mfcc_{i}'
            features.append(float(track.get(mfcc_key, 0) or 0))

        # MFCC per-coefficient std (if available)
        for i in range(13):
            mfcc_std_key = f'mfcc_std_{i}'
            features.append(float(track.get(mfcc_std_key, 0) or 0))

        # Additional MFCC aggregate statistics (if available)
        mfcc_agg_stats = ['kurtosis_mean', 'skew_mean']
        for stat in mfcc_agg_stats:
            mfcc_key = f'mfcc_{stat}'
            features.append(float(track.get(mfcc_key, 0) or 0))

        # Chroma features (12 bins) - mean values
        for i in range(12):
            chroma_key = f'chroma_{i}'
            features.append(float(track.get(chroma_key, 0) or 0))

        # Chroma per-bin std (if available)
        for i in range(12):
            chroma_std_key = f'chroma_std_{i}'
            features.append(float(track.get(chroma_std_key, 0) or 0))

        # Additional chroma aggregate statistics (if available)
        chroma_agg_stats = ['kurtosis_mean']
        for stat in chroma_agg_stats:
            chroma_key = f'chroma_{stat}'
            features.append(float(track.get(chroma_key, 0) or 0))

        # Spectral features - basic
        features.append(float(track.get('spec_centroid', 0) or 0))
        features.append(float(track.get('spec_rolloff', 0) or 0))
        features.append(float(track.get('dyn_complex', 0) or 0))
        features.append(float(track.get('onset_rate', 0) or 0))
        features.append(float(track.get('lufs', 0) or 0))

        # Additional spectral features (if available)
        spectral_features = [
            'spec_centroid_std', 'spec_rolloff_std', 'spec_bandwidth_mean', 'spec_bandwidth_std',
            'spec_contrast_mean', 'spec_contrast_std', 'tonnetz_mean', 'tonnetz_std',
            'spec_flux_mean', 'spec_flux_std', 'spec_flatness_mean', 'spec_flatness_std',
            'hfc_mean', 'hfc_std'
        ]
        for feat in spectral_features:
            features.append(float(track.get(feat, 0) or 0))

        return np.array(features)

    def _get_feature_names(self) -> List[str]:
        """Get list of feature names in order."""
        names = [
            'bpm_detected', 'energy_score', 'danceability', 'zero_crossing_rate',
            'chords_changes_rate', 'tuning_diatonic_strength'
        ]
        # MFCC - mean values
        names.extend([f'mfcc_{i}' for i in range(13)])
        # MFCC - std per coefficient
        names.extend([f'mfcc_std_{i}' for i in range(13)])
        # MFCC - additional aggregate statistics
        mfcc_agg_stats = ['kurtosis_mean', 'skew_mean']
        names.extend([f'mfcc_{stat}' for stat in mfcc_agg_stats])

        # Chroma - mean values
        names.extend([f'chroma_{i}' for i in range(12)])
        # Chroma - std per bin
        names.extend([f'chroma_std_{i}' for i in range(12)])
        # Chroma - additional aggregate statistics
        chroma_agg_stats = ['kurtosis_mean']
        names.extend([f'chroma_{stat}' for stat in chroma_agg_stats])

        # Spectral features - basic
        names.extend([
            'spec_centroid', 'spec_rolloff', 'dyn_complex',
            'onset_rate', 'lufs'
        ])

        # Additional spectral features
        names.extend([
            'spec_centroid_std', 'spec_rolloff_std', 'spec_bandwidth_mean', 'spec_bandwidth_std',
            'spec_contrast_mean', 'spec_contrast_std', 'tonnetz_mean', 'tonnetz_std',
            'spec_flux_mean', 'spec_flux_std', 'spec_flatness_mean', 'spec_flatness_std',
            'hfc_mean', 'hfc_std'
        ])

        return names

    def _encode_labels(self, buckets: List[str]) -> np.ndarray:
        """Encode bucket strings to integers."""
        unique_buckets = sorted(set(buckets))
        self.label_encoder = {bucket: i for i, bucket in enumerate(unique_buckets)}
        self.reverse_encoder = {i: bucket for bucket, i in self.label_encoder.items()}

        return np.array([self.label_encoder[bucket] for bucket in buckets])

    def _decode_labels(self, encoded: np.ndarray) -> List[str]:
        """Decode integer labels back to bucket strings."""
        return [self.reverse_encoder[int(label)] for label in encoded]

    def train(self, labeled_tracks: List[Dict[str, Any]]) -> None:
        """Train RandomForest model on labeled tracks."""
        try:
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.model_selection import train_test_split
            from sklearn.metrics import classification_report
        except ImportError:
            raise ImportError("scikit-learn required for ML bucket assignment")

        # Extract features and labels
        features = []
        labels = []

        for track in labeled_tracks:
            if 'bucket' not in track:
                continue

            feat = self._extract_features(track)
            features.append(feat)
            labels.append(track['bucket'])

        if not features or not labels:
            raise ValueError("No valid training data found")

        X = np.array(features)
        y = self._encode_labels(labels)

        # Split for validation
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )

        # Train model
        self.model = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            random_state=42,
            class_weight='balanced'
        )
        self.model.fit(X_train, y_train)

        self.feature_names = self._get_feature_names()

        # Calculate metrics
        y_pred = self.model.predict(X_test)
        from typing import cast
        report = cast(Dict[str, Any], classification_report(y_test, y_pred, output_dict=True))

        # Save metrics
        self._save_metrics(report, len(labeled_tracks))

        print(f"Trained ML model on {len(labeled_tracks)} tracks")
        print(".2f")

    def predict(self, track: Dict[str, Any]) -> Tuple[str, float]:
        """Predict bucket for a track."""
        if self.model is None:
            return "REVIEW QUEUE/UNDECIDED", 0.0

        features = self._extract_features(track).reshape(1, -1)

        # Get prediction probabilities
        probabilities = self.model.predict_proba(features)[0]
        predicted_class = int(np.argmax(probabilities))
        confidence = float(probabilities[predicted_class])

        # Decode bucket name
        bucket = self.reverse_encoder.get(predicted_class, "REVIEW QUEUE/UNDECIDED")

        return bucket, confidence

    def save_model(self, path: Path) -> None:
        """Save trained model to file."""
        if self.model is None:
            raise ValueError("No trained model to save")

        data = {
            'model': self.model,
            'feature_names': self.feature_names,
            'label_encoder': self.label_encoder,
            'reverse_encoder': self.reverse_encoder
        }

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('wb') as f:
            pickle.dump(data, f)

        print(f"Saved ML model to: {path}")

    def load_model(self, path: Path) -> None:
        """Load trained model from file."""
        with path.open('rb') as f:
            data = pickle.load(f)

        self.model = data['model']
        self.feature_names = data.get('feature_names', [])
        self.label_encoder = data.get('label_encoder', {})
        self.reverse_encoder = data.get('reverse_encoder', {})

        print(f"Loaded ML model from: {path}")

    def _save_metrics(self, report: Dict[str, Any], n_samples: int) -> None:
        """Save training metrics to file."""
        import json
        from datetime import datetime

        metrics = {
            'timestamp': datetime.now().isoformat(),
            'n_samples': n_samples,
            'accuracy': report.get('accuracy', 0),
            'macro_avg': report.get('macro avg', {}),
            'weighted_avg': report.get('weighted avg', {}),
            'classes': {}
        }

        # Per-class metrics
        for class_name, metrics_dict in report.items():
            if isinstance(metrics_dict, dict) and class_name not in ['accuracy', 'macro avg', 'weighted avg']:
                metrics['classes'][class_name] = metrics_dict

        metrics_path = LOGS_DIR / "ml_bucket_metrics.json"
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

        with metrics_path.open('w', encoding='utf-8') as f:
            json.dump(metrics, f, indent=2, ensure_ascii=False)

        print(f"Saved metrics to: {metrics_path}")
        # Also save a copy into the repository models/ for convenience
        try:
            models_dir = Path(__file__).resolve().parents[2] / "models"
            models_dir.mkdir(parents=True, exist_ok=True)
            with (models_dir / "ml_bucket_metrics.json").open('w', encoding='utf-8') as f:
                json.dump(metrics, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def get_feature_importance(self) -> Dict[str, float]:
        """Get feature importance scores."""
        if self.model is None:
            return {}

        importance = self.model.feature_importances_
        return dict(zip(self.feature_names, importance))