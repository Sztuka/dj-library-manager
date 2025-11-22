"""
Configs and placeholders for future Essentia-based ML models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = REPO_ROOT / "models"
DEFAULT_DATASET = REPO_ROOT / "data" / "training_dataset_full.csv"


@dataclass
class GenreModelConfig:
    """Placeholder describing how the future genre classifier will be stored."""

    dataset_path: Path = DEFAULT_DATASET
    model_path: Path = MODELS_DIR / "genre_model.pkl"
    feature_columns: Tuple[str, ...] = field(default_factory=lambda: ())
    label_column: str = "genre_label"
    export_notes: str = (
        "Uses Essentia features from audio_analysis joined with current genre labels."
    )


@dataclass
class BucketModelConfig:
    """Placeholder describing how the future bucket predictor will be stored."""

    dataset_path: Path = DEFAULT_DATASET
    model_path: Path = MODELS_DIR / "bucket_model.pkl"
    feature_columns: Tuple[str, ...] = field(default_factory=lambda: ())
    label_column: str = "bucket_label"
    export_notes: str = (
        "Maps Essentia features to DJ-specific target_subfolder buckets."
    )


# TODO: train_genre_model(config: GenreModelConfig) -> Path
#       Will load DEFAULT_DATASET, train a classifier and save to config.model_path.
#
# TODO: train_bucket_model(config: BucketModelConfig) -> Path
#       Will load DEFAULT_DATASET, train a bucket model and save to config.model_path.
