"""Regression tests for enrich-online results against baseline SoT.

These are NOT unit tests — they compare a static baseline snapshot
(data/unsorted_baseline_sot.xlsx, Feb 2026) against live data/unsorted.csv
which grows with every enrichment session. They will always fail in CI.

Run manually to spot regressions after a bulk enrich:
    pytest tests/test_enrich_regression.py -v
"""

import pytest
import pandas as pd
from pathlib import Path

pytestmark = pytest.mark.skip(reason="regression — compare live data manually, not in CI")

# Column that identifies a track uniquely
ID_COLUMN = "file_path"

# All important enrich columns to validate
ENRICH_COLUMNS = {
    # Final resolved values
    "genre": "Final resolved genre",
    "year": "Final resolved year",
    
    # Suggestions from enrich
    "artist_suggest": "Artist suggestion",
    "title_suggest": "Title suggestion", 
    "version_suggest": "Version/remix suggestion",
    "genre_suggest": "Genre suggestion (combined)",
    "year_suggest": "Year suggestion",
    "album_suggest": "Album suggestion",
    
    # Source-specific genre results (critical for optimization testing)
    "genres_beatport": "Beatport genre results",
    "genres_musicbrainz": "MusicBrainz genre results",
    "genres_lastfm": "Last.fm genre results",
    "genres_soundcloud": "SoundCloud genre results",
    
    # Metadata source tracking
    "meta_source": "Which source provided metadata",
    
    # Release information
    "original_release_date": "Original release date",
    "original_release_year": "Original release year",
}

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_FILE = REPO_ROOT / "data" / "unsorted_baseline_sot.xlsx"
CURRENT_FILE = REPO_ROOT / "data" / "unsorted.csv"


def _normalize_value(val) -> str:
    """Normalize a value for comparison."""
    if pd.isna(val):
        return ""
    if isinstance(val, (int, float)):
        # Handle years and numeric values
        if val == int(val):
            return str(int(val))
        return str(val)
    return str(val).strip().lower()


@pytest.fixture(scope="module")
def baseline_df() -> pd.DataFrame:
    """Load the baseline SoT (cached for module)."""
    if not BASELINE_FILE.exists():
        pytest.skip(f"Baseline file not found: {BASELINE_FILE}")
    return pd.read_excel(BASELINE_FILE)


@pytest.fixture(scope="module")
def current_df() -> pd.DataFrame:
    """Load current enrich results (cached for module)."""
    if not CURRENT_FILE.exists():
        pytest.skip(f"Current file not found: {CURRENT_FILE}")
    return pd.read_csv(CURRENT_FILE)


@pytest.fixture(scope="module")
def merged_df(baseline_df: pd.DataFrame, current_df: pd.DataFrame) -> pd.DataFrame:
    """Merge baseline and current DataFrames once for all tests."""
    return baseline_df.merge(
        current_df,
        on=ID_COLUMN,
        suffixes=("_baseline", "_current"),
        how="outer",
    )


def test_baseline_exists():
    """Verify baseline SoT file exists."""
    assert BASELINE_FILE.exists(), f"Baseline SoT not found: {BASELINE_FILE}"


def test_same_track_count(baseline_df: pd.DataFrame, current_df: pd.DataFrame):
    """Verify same number of tracks are processed."""
    assert len(current_df) == len(baseline_df), (
        f"Track count mismatch: baseline={len(baseline_df)}, current={len(current_df)}"
    )


def _compare_column(merged_df: pd.DataFrame, column: str) -> list[dict]:
    """Compare a single column between baseline and current, return mismatches."""
    mismatches = []
    baseline_col = f"{column}_baseline"
    current_col = f"{column}_current"
    
    # Skip if columns don't exist
    if baseline_col not in merged_df.columns or current_col not in merged_df.columns:
        return []
    
    for _, row in merged_df.iterrows():
        file_path = row[ID_COLUMN]
        
        baseline_val = row.get(baseline_col)
        current_val = row.get(current_col)
        
        baseline_norm = _normalize_value(baseline_val)
        current_norm = _normalize_value(current_val)
        
        if baseline_norm != current_norm:
            filename = Path(file_path).name if pd.notna(file_path) else "?"
            mismatches.append({
                "file": filename,
                "baseline": baseline_norm or "(empty)",
                "current": current_norm or "(empty)",
            })
    
    return mismatches


# Critical columns that get individual tests for clearer CI output
CRITICAL_COLUMNS = [
    ("genre", "Genre"),
    ("year", "Year"),
    ("genres_beatport", "Beatport"),
    ("genres_musicbrainz", "MusicBrainz"),
    ("genres_lastfm", "Last.fm"),
    ("genres_soundcloud", "SoundCloud"),
    ("genre_suggest", "genre_suggest"),
    ("meta_source", "meta_source"),
]


@pytest.mark.parametrize("column,label", CRITICAL_COLUMNS)
def test_enrich_column_match(merged_df: pd.DataFrame, column: str, label: str):
    """Verify enrich column matches baseline (parametrized)."""
    mismatches = _compare_column(merged_df, column)
    if mismatches:
        msg = f"{label} mismatches:\n"
        for m in mismatches:
            msg += f"  {m['file']}: '{m['baseline']}' → '{m['current']}'\n"
        pytest.fail(msg)


def compare_enrich_results() -> dict:
    """Compare current results with baseline and return comprehensive summary.
    
    This function can be called directly for debugging:
        python tests/test_enrich_regression.py
    """
    if not BASELINE_FILE.exists():
        return {"error": f"Baseline not found: {BASELINE_FILE}"}
    if not CURRENT_FILE.exists():
        return {"error": f"Current file not found: {CURRENT_FILE}"}
    
    baseline = pd.read_excel(BASELINE_FILE)
    current = pd.read_excel(CURRENT_FILE)
    
    merged = baseline.merge(
        current,
        on=ID_COLUMN,
        suffixes=("_baseline", "_current"),
        how="outer",
    )
    
    # Compare all enrich columns
    all_mismatches = {}
    for column in ENRICH_COLUMNS:
        baseline_col = f"{column}_baseline"
        current_col = f"{column}_current"
        
        # Skip if column doesn't exist in both
        if baseline_col not in merged.columns or current_col not in merged.columns:
            continue
        
        mismatches = []
        for _, row in merged.iterrows():
            file_path = row[ID_COLUMN]
            filename = Path(file_path).name if pd.notna(file_path) else "?"
            
            baseline_val = _normalize_value(row.get(baseline_col))
            current_val = _normalize_value(row.get(current_col))
            
            if baseline_val != current_val:
                mismatches.append({
                    "file": filename,
                    "baseline": baseline_val or "(empty)",
                    "current": current_val or "(empty)",
                })
        
        if mismatches:
            all_mismatches[column] = mismatches
    
    return {
        "baseline_count": len(baseline),
        "current_count": len(current),
        "mismatches": all_mismatches,
        "all_match": len(all_mismatches) == 0,
    }


if __name__ == "__main__":
    # Quick comparison when run directly
    result = compare_enrich_results()
    print(f"Baseline tracks: {result.get('baseline_count', '?')}")
    print(f"Current tracks:  {result.get('current_count', '?')}")
    print()
    
    if result.get("all_match"):
        print("✓ All enrich results match baseline")
    else:
        for column, mismatches in result.get("mismatches", {}).items():
            desc = ENRICH_COLUMNS.get(column, column)
            print(f"\n{column} ({desc}):")
            for m in mismatches:
                print(f"  {m['file']}: '{m['baseline']}' → '{m['current']}'")
