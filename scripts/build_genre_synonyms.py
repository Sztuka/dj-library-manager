#!/usr/bin/env python3
"""Build canonical genre synonyms from legacy CSV exports.

This is a one-shot helper that mines OLD_LIBRARY_GENRES_LIST.csv and produces
synonym suggestions for the canonical genres defined in genres.yml. It does
not write any files – it prints a summary plus a YAML block you can paste into
genres.yml. The mapping rules are intentionally simple, deterministic, and
documented below so they can be tweaked later.
"""

from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import yaml

from djlib.genre_canonical import CanonicalGenreResolver, GENRES_FILE

# ---- Config / constants ----------------------------------------------------

DEFAULT_GENRES_CSV = Path("data/OLD_LIBRARY_GENRES_LIST.csv")

# Values that should never become synonyms (noise, charts, placeholders, URLs)
IGNORED_GENRE_TOKENS = {
    "other",
    "unknown",
    "misc",
    "various",
    "genre",
    "no genre",
    "top 40",
    "top 40 dossier",
    "top40",
    "top 100",
    "top 200",
    "top 250",
    "top200",
    "top250",
    "<onbekend>",
    "rmx",
    "dj mix",
    "remix service",
    "music",
    "f",
    "beat",
    "beats",
    "fusion",
    "moscow steak house",
    "not rap",
}

IGNORED_SUBSTRINGS = ["http", "www", ".com", "skachat", "darkside"]

# Skip extremely long tag clouds; we only want compact genre labels as synonyms
MAX_PARTS_FOR_SYNONYM = 10

# Preserve certain multi-word patterns before splitting (e.g., don't break R&B)
SPECIAL_REPLACEMENTS = [
    (re.compile(r"rhythm\s*&\s*blues", re.IGNORECASE), "rnb"),
    (re.compile(r"r\s*&\s*b", re.IGNORECASE), "rnb"),
]

# Canonical priority: when a raw string maps to multiple candidates, choose the
# most specific dance subgenre first, then work downwards to broader labels.
CANONICAL_PRIORITY: List[str] = [
    "MELODIC_TECHNO",
    "TECH_HOUSE",
    "DEEP_HOUSE",
    "HOUSE",
    "HARD_TECHNO",
    "TECHNO",
    "HARDCORE",
    "TRANCE",
    "DNB",
    "AFRO_HOUSE",
    "AFROBEATS",
    "DANCEHALL",  # prefer Dancehall over Latin when both appear
    "LATIN",
    "REGGAE",
    "HIP_HOP",
    "RNB",
    "EURO_DANCE",
    "POP",
    "NU_DISCO",
    "DISCO",
    "FUNK",
    "SOUL",
    "BLUES",
    "ROCK_N_ROLL",
    "ROCK",
    "ALTERNATIVE_ROCK",
    "INDIE_ROCK",
    "NEW_WAVE",
    "ELECTRO_SWING",
]

# Manual keyword → canonical genre hints to supplement genres.yml synonyms
MANUAL_MAP = {
    "melodic house & techno": "MELODIC_TECHNO",
    "melodic house and techno": "MELODIC_TECHNO",
    "melodic house techno": "MELODIC_TECHNO",
    "melodic house": "MELODIC_TECHNO",
    "tech house": "TECH_HOUSE",
    "electro house": "TECH_HOUSE",
    "deep tech house": "TECH_HOUSE",
    "deep house": "DEEP_HOUSE",
    "funky/club house": "HOUSE",
    "funky house": "HOUSE",
    "club house": "HOUSE",
    "progressive house": "HOUSE",
    "dance house": "HOUSE",
    "house": "HOUSE",
    "hard techno": "HARD_TECHNO",
    "techno": "TECHNO",
    "hardcore": "HARDCORE",
    "gabber": "HARDCORE",
    "happy hardcore": "HARDCORE",
    "trance": "TRANCE",
    "uplifting trance": "TRANCE",
    "progressive trance": "TRANCE",
    "drum and bass": "DNB",
    "drum & bass": "DNB",
    "dnb": "DNB",
    "jungle": "DNB",
    "afro house": "AFRO_HOUSE",
    "afro-house": "AFRO_HOUSE",
    "afro house tech": "AFRO_HOUSE",
    "afro tech": "AFRO_HOUSE",
    "organic house": "AFRO_HOUSE",
    "afrobeats": "AFROBEATS",
    "afrobeat": "AFROBEATS",
    "afro beats": "AFROBEATS",
    "afro pop": "AFROBEATS",
    "afropop": "AFROBEATS",
    "naija pop": "AFROBEATS",
    "hip hop": "HIP_HOP",
    "hip-hop": "HIP_HOP",
    "hiphop": "HIP_HOP",
    "rap": "HIP_HOP",
    "rap/hip hop": "HIP_HOP",
    "hip hop/rap": "HIP_HOP",
    "rap & hip-hop": "HIP_HOP",
    "trap": "HIP_HOP",
    "drill": "HIP_HOP",
    "r&b": "RNB",
    "r n b": "RNB",
    "rnb": "RNB",
    "r'n'b": "RNB",
    "rhythm and blues": "RNB",
    "latin": "LATIN",
    "latin pop": "LATIN",
    "latin urban": "LATIN",
    "latinamerikanische musik": "LATIN",
    "lateinamerikanische musik": "LATIN",
    "reggaeton": "LATIN",
    "bachata": "LATIN",
    "salsa": "LATIN",
    "salsa cubana": "LATIN",
    "moombahton": "LATIN",
    "dembow": "LATIN",
    "dancehall": "DANCEHALL",
    "ragga": "DANCEHALL",
    "reggae": "REGGAE",
    "pop": "POP",
    "dance pop": "POP",
    "mainstream pop": "POP",
    "dance": "EURO_DANCE",
    "dance / pop": "EURO_DANCE",
    "dance-pop": "EURO_DANCE",
    "eurodance": "EURO_DANCE",
    "euro dance": "EURO_DANCE",
    "electro": "EURO_DANCE",
    "electronic": "EURO_DANCE",
    "electronica": "EURO_DANCE",
    "muzyka elektroniczna": "EURO_DANCE",
    "disco": "DISCO",
    "nu disco": "NU_DISCO",
    "electro swing": "ELECTRO_SWING",
    "funk": "FUNK",
    "funky": "FUNK",
    "soul": "SOUL",
    "motown": "SOUL",
    "blues": "BLUES",
    "swing": "SWING",
    "rock": "ROCK",
    "hard rock": "ROCK",
    "classic rock": "ROCK",
    "punk rock": "ROCK",
    "punk": "ROCK",
    "pop rock": "ROCK",
    "rock & roll": "ROCK_N_ROLL",
    "rock and roll": "ROCK_N_ROLL",
    "rock'n'roll": "ROCK_N_ROLL",
    "rocknroll": "ROCK_N_ROLL",
    "indie": "INDIE_ROCK",
    "indie rock": "INDIE_ROCK",
    "indie pop": "INDIE_ROCK",
    "indie alternative": "INDIE_ROCK",
    "alternative": "ALTERNATIVE_ROCK",
    "alternative rock": "ALTERNATIVE_ROCK",
    "alt rock": "ALTERNATIVE_ROCK",
    "new wave": "NEW_WAVE",
    "post punk": "NEW_WAVE",
    "80s": "NEW_WAVE",
    "synthpop": "NEW_WAVE",
}

MULTI_SPLIT_PATTERN = re.compile(r"[,/;|]+|\s*&\s+|\s*\+\s+")


# ---- Helpers ---------------------------------------------------------------

def normalize_token(text: str) -> str:
    """Normalize a token for matching: lowercase, ASCII-ish, single spaces."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\x00", " ")
    text = text.replace("&", " and ")
    text = text.strip(" \t\n\r\"'[](){}*")
    text = re.sub(r"[\t\n\r]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def split_raw_genre(raw: str) -> List[str]:
    """Split a raw genre string into subparts using common delimiters."""
    cleaned = raw
    for pattern, repl in SPECIAL_REPLACEMENTS:
        cleaned = pattern.sub(repl, cleaned)
    parts = [p.strip() for p in MULTI_SPLIT_PATTERN.split(cleaned) if p.strip()]
    return parts or [cleaned.strip()]


def is_garbage(raw: str) -> bool:
    lowered = normalize_token(raw)
    if not lowered:
        return True
    if len(lowered) < 2:
        return True
    if not re.search(r"[a-z]", lowered):
        return True
    if lowered in IGNORED_GENRE_TOKENS:
        return True
    for sub in IGNORED_SUBSTRINGS:
        if sub in lowered:
            return True
    return False


def pick_priority(candidates: Sequence[str]) -> Optional[str]:
    if not candidates:
        return None
    for key in CANONICAL_PRIORITY:
        if key in candidates:
            return key
    return sorted(candidates)[0]


def map_part_to_canonical(part: str, resolver: CanonicalGenreResolver) -> Optional[str]:
    """Map a single sub-genre part to a canonical id."""
    norm = normalize_token(part)
    if is_garbage(norm):
        return None
    if norm in MANUAL_MAP:
        return MANUAL_MAP[norm]

    resolved = resolver.resolve(part)
    if resolved:
        return resolved[0]

    # Heuristic: try resolver on normalized form (without punctuation noise)
    resolved_norm = resolver.resolve(norm)
    if resolved_norm:
        return resolved_norm[0]

    return None


def build_synonyms(
    csv_path: Path, resolver: CanonicalGenreResolver
) -> Tuple[Dict[str, Set[str]], Counter[str], Counter[str]]:
    """
    Build a mapping: canonical genre -> set of raw synonyms.

    Returns:
        synonyms_map: canonical id -> set of raw strings
        unmapped: counter of raw strings that could not be mapped
        ignored: counter of raw strings treated as garbage
    """

    synonyms_map: Dict[str, Set[str]] = defaultdict(set)
    unmapped: Counter[str] = Counter()
    ignored: Counter[str] = Counter()

    with csv_path.open(newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader((line.replace("\x00", "") for line in f))
        for row in reader:
            raw = (row.get("genre") or "").strip()
            if not raw:
                continue
            if is_garbage(raw):
                ignored[raw] += 1
                continue

            display_raw = re.sub(r"\s+", " ", raw).strip()

            parts = split_raw_genre(raw)
            if len(parts) > MAX_PARTS_FOR_SYNONYM:
                ignored[raw] += 1
                continue
            candidates = {c for part in parts if (c := map_part_to_canonical(part, resolver))}
            if not candidates:
                # As a fallback, try mapping the whole string
                whole = map_part_to_canonical(raw, resolver)
                if whole:
                    candidates.add(whole)

            if not candidates:
                unmapped[raw] += 1
                continue

            chosen = pick_priority(sorted(candidates))
            if chosen:
                synonyms_map[chosen].add(display_raw)
            else:
                unmapped[raw] += 1

    return synonyms_map, unmapped, ignored


def merge_with_existing(
    synonyms_map: Dict[str, Set[str]], genres_file: Path
) -> Dict[str, List[str]]:
    """Union new synonyms with those already present in genres.yml."""
    with genres_file.open() as f:
        genres = yaml.safe_load(f) or {}

    merged: Dict[str, List[str]] = {}
    for key, definition in genres.items():
        existing = definition.get("synonyms", []) or []
        new_values = synonyms_map.get(key, set())
        merged_list = sorted({*existing, *new_values}, key=lambda s: s.lower())
        merged[key] = merged_list
    return merged


def print_summary(synonyms_map: Dict[str, Set[str]], unmapped: Counter[str], ignored: Counter[str]) -> None:
    print("=== Mapped synonyms per canonical genre ===")
    for key in CANONICAL_PRIORITY:
        syns = sorted(synonyms_map.get(key, []), key=lambda s: s.lower())
        if not syns:
            continue
        preview = ", ".join(syns[:5])
        print(f"{key:<16} total={len(syns):>3} preview: {preview}")

    print("\n=== Ignored (garbage) samples ===")
    for item, count in ignored.most_common(10):
        print(f"{item!r}: {count}")

    print("\n=== Unmapped samples ===")
    for item, count in unmapped.most_common(10):
        print(f"{item!r}: {count}")


def emit_yaml_block(merged_synonyms: Dict[str, List[str]], genres_file: Path) -> None:
    with genres_file.open() as f:
        genres = yaml.safe_load(f) or {}

    print("\n=== YAML block (paste into genres.yml) ===")
    for key in genres:
        definition = genres[key] or {}
        label = definition.get("label", key.title())
        synonyms = merged_synonyms.get(key, [])
        print(f"{key}:")
        print(f"  label: \"{label}\"")
        print("  synonyms:")
        for syn in synonyms:
            print(f"    - \"{syn}\"")
        print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build genre synonyms from legacy exports.")
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_GENRES_CSV,
        help="Path to OLD_LIBRARY_GENRES_LIST.csv",
    )
    parser.add_argument(
        "--genres-yml",
        type=Path,
        default=GENRES_FILE,
        help="Path to genres.yml (used to merge existing synonyms)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    resolver = CanonicalGenreResolver(args.genres_yml)

    synonyms_map, unmapped, ignored = build_synonyms(args.csv, resolver)
    merged = merge_with_existing(synonyms_map, args.genres_yml)

    print_summary(synonyms_map, unmapped, ignored)
    emit_yaml_block(merged, args.genres_yml)


if __name__ == "__main__":
    main()
