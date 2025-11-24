"""
Check if a track has been analyzed in Rekordbox (BPM & Key detection).

Provides two methods:
1. was_analyzed_from_tags() - reads TBPM/TKEY from ID3 tags (fast, works offline)
2. was_analyzed_from_db() - reads from Rekordbox database (more authoritative)

The was_analyzed() wrapper prioritizes tags (most reliable), uses DB as fallback.

IMPORTANT BEHAVIOR:
------------------
1. If file has TBPM+TKEY tags → returns True (even if edited outside Rekordbox)
   - This handles case: analyze in Rekordbox → edit BPM in Traktor → still "analyzed"
   - Tags are the source of truth that travels with the file

2. If no tags but found in DB → returns True
   - This handles case: analyzed in Rekordbox but tags not saved yet
   - WARNING: DB tracks by file path, so moving file breaks this!

3. If file moved (e.g., UNSORTED → Library) → DB won't find it anymore
   - This is OK! Tags should be present after Rekordbox analysis
   - If tags missing after move → file needs re-analysis in Rekordbox

RECOMMENDED WORKFLOW:
--------------------
1. Import tracks to Rekordbox
2. Analyze in Rekordbox (sets BPM/Key in DB + writes to tags)
3. Move files with this app → tags travel with file, DB entry becomes stale
4. Scan checks tags (not DB path) → works correctly after move
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Any
import unicodedata  # For Unicode normalization (macOS NFD vs NFC)

from mutagen._file import File as MutagenFile

try:
    from pyrekordbox import Rekordbox6Database
    PYREKORDBOX_AVAILABLE = True
except ImportError:
    Rekordbox6Database = None  # type: ignore
    PYREKORDBOX_AVAILABLE = False


# Default Rekordbox DB paths on macOS
DEFAULT_REKORDBOX_DB_PATHS = [
    Path.home() / "Library" / "Pioneer" / "rekordbox" / "master.db",
    Path.home() / "Library" / "Pioneer" / "rekordbox" / "datafile.edb",
]

_db_cache: Optional[Any] = None
_db_path_cache: Optional[Path] = None


def _get_rekordbox_db() -> Optional[Any]:
    """Get cached Rekordbox database connection, or None if unavailable."""
    global _db_cache, _db_path_cache
    
    if not PYREKORDBOX_AVAILABLE:
        return None
    
    if _db_cache is not None:
        return _db_cache
    
    # Try to find and open the database
    for db_path in DEFAULT_REKORDBOX_DB_PATHS:
        if db_path.exists():
            try:
                _db_cache = Rekordbox6Database(str(db_path))  # type: ignore
                _db_path_cache = db_path
                return _db_cache
            except Exception:
                continue
    
    return None


def was_analyzed_from_tags(path: Path) -> bool:
    """
    Return True if file looks like it has been analyzed by Rekordbox,
    based purely on ID3 tags (TBPM + TKEY present and non-empty).

    This is the MVP heuristic.
    
    Args:
        path: Path to audio file
        
    Returns:
        True if both TBPM and TKEY tags exist and are non-empty
    """
    try:
        audio = MutagenFile(path)
        if not audio or not getattr(audio, "tags", None):
            return False

        tags = audio.tags

        bpm = tags.get("TBPM")
        key = tags.get("TKEY")

        def _non_empty(val) -> bool:
            if val is None:
                return False
            # val can be a frame object, list, or str; just stringify and trim
            s = str(val).strip()
            return bool(s) and s != "0"

        return _non_empty(bpm) and _non_empty(key)
    except Exception:
        # If we can't read the file, assume not analyzed
        return False


def was_analyzed_from_db(path: Path, db_path: Optional[Path] = None) -> Optional[bool]:
    """
    Check if track was analyzed IN REKORDBOX by querying the database.
    
    This is the AUTHORITATIVE check - confirms file was analyzed
    specifically in Rekordbox, not in Traktor/Serato/other tools.
    
    Args:
        path: Path to audio file
        db_path: Optional path to Rekordbox database (uses default if None)
        
    Returns:
        True if track is in Rekordbox DB with BPM+Key analysis
        False if track is in DB but missing analysis data
        None if DB is unavailable or track not found (e.g., after file move)
    """
    db = _get_rekordbox_db()
    if db is None:
        return None
    
    try:
        # Normalize path for comparison (macOS uses NFD, Rekordbox stores NFC)
        abs_path = str(path.resolve())
        abs_path_nfc = unicodedata.normalize('NFC', abs_path)
        filename = path.name
        filename_nfc = unicodedata.normalize('NFC', filename)
        
        # Query tracks from database
        query = db.get_content()
        if query is None:
            return None
        
        # Find track by file path (less reliable after moves)
        # Rekordbox uses FolderPath for full path
        for track in query.all():
            # Try exact path match first
            folder_path = getattr(track, 'FolderPath', None)
            if folder_path:
                folder_path_nfc = unicodedata.normalize('NFC', str(folder_path))
                
                if abs_path_nfc == folder_path_nfc:
                    # Check if analyzed: Analysed flag and BPM/Key present
                    analysed_flag = getattr(track, 'Analysed', 0)
                    bpm_raw = getattr(track, 'BPM', None)  # BPM * 100 in DB
                    key_id = getattr(track, 'KeyID', None)
                    
                    has_analysis = analysed_flag > 0
                    has_bpm = bpm_raw is not None and bpm_raw > 0
                    has_key = key_id is not None and str(key_id) != '0'
                    
                    return has_analysis and has_bpm and has_key
            
            # Fallback: match by filename only (less precise)
            if folder_path:
                folder_path_nfc = unicodedata.normalize('NFC', str(folder_path))
                
                if filename_nfc in folder_path_nfc:
                    analysed_flag = getattr(track, 'Analysed', 0)
                    bpm_raw = getattr(track, 'BPM', None)
                    key_id = getattr(track, 'KeyID', None)
                    
                    has_analysis = analysed_flag > 0
                    has_bpm = bpm_raw is not None and bpm_raw > 0
                    has_key = key_id is not None and str(key_id) != '0'
                    
                    return has_analysis and has_bpm and has_key
        
        return None
        
    except Exception:
        return None


def was_analyzed(path: Path, *, use_db: bool = True, strict: bool = False) -> bool:
    """
    High-level analysis check, used by the scan/unsorted generation step.
    
    Strategy:
    1. Check Rekordbox DB first (most authoritative):
       - Confirms file was analyzed IN REKORDBOX specifically
       - Not Traktor/Serato/other tools
       - Only works if file path unchanged since analysis
    2. If not in DB, check tags as fallback (less authoritative):
       - Could be from any DJ software
       - Works after file moves
       - Better than blocking workflow entirely
    
    This prioritizes Rekordbox-specific analysis while maintaining
    workflow flexibility for edge cases.

    Args:
        path: Path to audio file
        use_db: Whether to attempt DB lookup (default True)
        strict: If True, ONLY accept DB results (reject tag-only files)
                Use strict=True to enforce Rekordbox analysis only
        
    Returns:
        True if file was analyzed in Rekordbox (or has tags if not strict)
    """
    # Try Rekordbox DB first - most authoritative
    if use_db:
        db_result = was_analyzed_from_db(path)
        if db_result is not None:
            return db_result
    
    # If strict mode, reject tag-only files (require DB confirmation)
    if strict:
        return False
    
    # Fall back to tags (could be from other DJ software)
    return was_analyzed_from_tags(path)


def debug_print_db_status() -> None:
    """Print information about Rekordbox database availability and status."""
    if not PYREKORDBOX_AVAILABLE:
        print("⚠️  pyrekordbox not installed - DB queries disabled")
        print("   Install: pip install pyrekordbox")
        return
    
    db = _get_rekordbox_db()
    if db is None:
        print("⚠️  Rekordbox database not found")
        print("   Searched paths:")
        for path in DEFAULT_REKORDBOX_DB_PATHS:
            print(f"   - {path}")
        return
    
    print(f"✅ Rekordbox database found: {_db_path_cache}")
    try:
        query = db.get_content()
        if query is not None:
            tracks = query.all()
            print(f"   Tracks in database: {len(tracks)}")
    except Exception as e:
        print(f"   Warning: Could not read tracks: {e}")
