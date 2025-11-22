# NOTE: See docs/BUCKETING_LEGACY.md – bucketing/ML here is legacy and currently not active.
from __future__ import annotations
from typing import Dict, List, Any, Optional, Tuple
import re
from pathlib import Path

from .base import BucketAssigner, load_rules_from_yaml


class RulesBucketAssigner(BucketAssigner):
    """Bucket assigner using deterministic rules from YAML configuration."""

    def __init__(self, rules_path: Optional[Path] = None):
        """Initialize with rules from YAML file.

        Args:
            rules_path: Path to YAML rules file. If None, uses default rules.
        """
        self.rules_config = self._get_default_rules() if rules_path is None else load_rules_from_yaml(rules_path)
        self.rules = self.rules_config.get('rules', [])
        self.defaults = self.rules_config.get('defaults', {})
        self.resolution = self.rules_config.get('resolution', {})

    def _get_default_rules(self) -> Dict[str, Any]:
        """Get default rules configuration."""
        return {
            'version': 1,
            'defaults': {
                'target_bpm_range': [80, 180],
                'energy_thresholds': {
                    'low': 0.35,
                    'mid': 0.55,
                    'high': 0.70
                }
            },
            'rules': [
                {
                    'name': 'HOUSE_HIGH_ENERGY',
                    'when': {
                        'bpm_range': [120, 135],
                        'energy_min': 'high',
                        'genres_any': ['house', 'tech house', 'electro house']
                    },
                    'then': {
                        'bucket': 'READY TO PLAY/CLUB/HOUSE',
                        'confidence': 0.8
                    }
                },
                {
                    'name': 'TECHNO_HIGH_ENERGY',
                    'when': {
                        'bpm_range': [125, 145],
                        'energy_min': 'high',
                        'genres_any': ['techno', 'melodic techno', 'hard techno']
                    },
                    'then': {
                        'bucket': 'READY TO PLAY/CLUB/TECHNO',
                        'confidence': 0.8
                    }
                },
                {
                    'name': 'DNB_HIGH_ENERGY',
                    'when': {
                        'bpm_range': [160, 180],
                        'energy_min': 'mid',
                        'genres_any': ['drum and bass', 'dnb', 'jungle']
                    },
                    'then': {
                        'bucket': 'READY TO PLAY/CLUB/DNB',
                        'confidence': 0.8
                    }
                },
                {
                    'name': 'AFRO_HOUSE',
                    'when': {
                        'genres_any': ['afro house', 'afro-house', 'south african house']
                    },
                    'then': {
                        'bucket': 'READY TO PLAY/CLUB/AFRO HOUSE',
                        'confidence': 0.9
                    }
                },
                {
                    'name': 'CHILL_OPENING',
                    'when': {
                        'bpm_range': [70, 100],
                        'energy_max': 'low',
                        'genres_any': ['ambient', 'chillout', 'downtempo', 'lo-fi']
                    },
                    'then': {
                        'bucket': 'OPEN FORMAT/CHILL',
                        'confidence': 0.7
                    }
                },
                {
                    'name': 'HIPHOP_URBAN',
                    'when': {
                        'bpm_range': [80, 110],
                        'genres_any': ['hip hop', 'hip-hop', 'rap', 'r&b', 'rnb', 'trap']
                    },
                    'then': {
                        'bucket': 'OPEN FORMAT/HIP-HOP',
                        'confidence': 0.8
                    }
                },
                {
                    'name': 'LATIN_REGGAETON',
                    'when': {
                        'genres_any': ['reggaeton', 'latin', 'bachata', 'salsa', 'cumbia']
                    },
                    'then': {
                        'bucket': 'OPEN FORMAT/LATIN REGGAETON',
                        'confidence': 0.8
                    }
                },
                {
                    'name': 'CLASSICS_90S',
                    'when': {
                        'genres_any': ['90s', 'eurodance', 'happy hardcore', 'gabber']
                    },
                    'then': {
                        'bucket': 'OPEN FORMAT/90s',
                        'confidence': 0.7
                    }
                },
                # Fallback rules based on BPM/energy only (more lenient)
                {
                    'name': 'CLUB_HIGH_ENERGY',
                    'when': {
                        'bpm_range': [115, 145],
                        'energy_min': 'low'  # 0.35
                    },
                    'then': {
                        'bucket': 'READY TO PLAY/CLUB/OTHER',
                        'confidence': 0.4
                    }
                },
                {
                    'name': 'CLUB_MID_ENERGY',
                    'when': {
                        'bpm_range': [100, 135]
                    },
                    'then': {
                        'bucket': 'READY TO PLAY/CLUB/HOUSE',
                        'confidence': 0.3
                    }
                },
                {
                    'name': 'OPEN_FORMAT_SLOW',
                    'when': {
                        'bpm_range': [60, 105]
                    },
                    'then': {
                        'bucket': 'OPEN FORMAT/CHILL',
                        'confidence': 0.3
                    }
                }
            ],
            'resolution': {
                'tie_breaker': ['confidence', 'energy', 'bpm_proximity'],
                'fallback_bucket': 'REVIEW QUEUE/UNDECIDED'
            }
        }

    def _extract_genres(self, track: Dict[str, Any]) -> List[str]:
        """Extract all genre strings from track data."""
        genres = []

        # From various genre fields
        for field in ['genre_main', 'genre_sub1', 'genre_sub2', 'tag_genre']:
            genre = track.get(field, '').strip().lower()
            if genre:
                genres.append(genre)

        # Split comma-separated genres
        expanded = []
        for genre in genres:
            expanded.extend(g.strip() for g in genre.split(',') if g.strip())

        return expanded

    def _matches_condition(self, track: Dict[str, Any], condition: Dict[str, Any]) -> bool:
        """Check if track matches a rule condition."""

        # BPM range check
        if 'bpm_range' in condition:
            bpm_min, bpm_max = condition['bpm_range']
            bpm_detected = track.get('bpm_detected')
            if bpm_detected is not None:
                try:
                    bpm_val = float(bpm_detected)
                    if not (bpm_min <= bpm_val <= bpm_max):
                        return False
                except (ValueError, TypeError):
                    pass

        # Energy thresholds
        energy_thresholds = self.defaults.get('energy_thresholds', {})
        energy_score = track.get('energy_score')
        if energy_score is not None:
            try:
                energy_val = float(energy_score)
            except (ValueError, TypeError):
                energy_val = None

        if 'energy_min' in condition and energy_val is not None:
            threshold_name = condition['energy_min']
            threshold_val = energy_thresholds.get(threshold_name)
            if threshold_val is not None and energy_val < threshold_val:
                return False

        if 'energy_max' in condition and energy_val is not None:
            threshold_name = condition['energy_max']
            threshold_val = energy_thresholds.get(threshold_name)
            if threshold_val is not None and energy_val > threshold_val:
                return False

        # Genre matching
        if 'genres_any' in condition:
            track_genres = self._extract_genres(track)
            required_genres = [g.lower() for g in condition['genres_any']]
            if not any(any(rg in tg for rg in required_genres) for tg in track_genres):
                return False

        if 'genres_all' in condition:
            track_genres = self._extract_genres(track)
            required_genres = [g.lower() for g in condition['genres_all']]
            # Check if all required genres are present in track genres
            if not all(any(rg in tg for tg in track_genres) for rg in required_genres):
                return False

        # Key matching
        if 'key_mode_any' in condition:
            key = track.get('key_detected_camelot', '').upper()
            if key and key[-1] in condition['key_mode_any']:
                pass  # matches
            else:
                return False

        return True

    def predict(self, track: Dict[str, Any]) -> Tuple[str, float]:
        """Predict bucket using rules."""
        matching_rules = []

        for rule in self.rules:
            if self._matches_condition(track, rule['when']):
                matching_rules.append(rule)

        if not matching_rules:
            fallback = self.resolution.get('fallback_bucket', 'REVIEW QUEUE/UNDECIDED')
            return fallback, 0.0

        if len(matching_rules) == 1:
            rule = matching_rules[0]
            return rule['then']['bucket'], rule['then']['confidence']

        # Multiple matches - use tie breaker
        tie_breaker = self.resolution.get('tie_breaker', ['confidence'])

        best_rule = None
        best_score = -1

        for rule in matching_rules:
            score = 0
            confidence = rule['then']['confidence']

            if 'confidence' in tie_breaker:
                score += confidence * 100

            if 'energy' in tie_breaker:
                energy = track.get('energy_score')
                if energy is not None:
                    try:
                        score += float(energy) * 10
                    except (ValueError, TypeError):
                        pass

            if 'bpm_proximity' in tie_breaker:
                # Prefer rules where BPM is closer to center of range
                bpm_range = rule['when'].get('bpm_range')
                bpm_detected = track.get('bpm_detected')
                if bpm_range and bpm_detected:
                    try:
                        bpm_val = float(bpm_detected)
                        center = (bpm_range[0] + bpm_range[1]) / 2
                        proximity = 1.0 / (1.0 + abs(bpm_val - center))
                        score += proximity * 5
                    except (ValueError, TypeError):
                        pass

            if score > best_score:
                best_score = score
                best_rule = rule

        if best_rule:
            return best_rule['then']['bucket'], best_rule['then']['confidence']

        # Fallback
        fallback = self.resolution.get('fallback_bucket', 'REVIEW QUEUE/UNDECIDED')
        return fallback, 0.0

    def train(self, labeled_tracks: List[Dict[str, Any]]) -> None:
        """Rules-based assigner doesn't need training."""
        pass
