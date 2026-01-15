"""
External DJ Software Integration (Rekordbox & Traktor)

Phase 1: Import Snapshots (READ-ONLY)
Phase 2: Path Mapping (READ-ONLY)
Phase 3: Path Sync (WRITE - explicit opt-in)

This module provides safe, read-only snapshot import from DJ software databases
and prepares for future path synchronization after library moves.
"""

from __future__ import annotations

import csv
import shutil
import tempfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, TYPE_CHECKING

from xsdata.formats.dataclass.serializers import XmlSerializer

if TYPE_CHECKING:
    from pyrekordbox import Rekordbox6Database

try:
    from pyrekordbox import Rekordbox6Database
    from pyrekordbox.utils import get_rekordbox_pid
    PYREKORDBOX_AVAILABLE = True
except ImportError:
    Rekordbox6Database = None  # type: ignore[assignment,misc]
    PYREKORDBOX_AVAILABLE = False


try:
    from traktor_nml_utils import TraktorCollection
    from traktor_nml_utils.models.collection import (
        Entrytype,
        Infotype,
        Locationtype,
        ModificationInfotype,
        MusicalKeytype,
        Tempotype,
    )

    TRAKTOR_UTILS_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    TraktorCollection = None  # type: ignore[assignment]
    Entrytype = None  # type: ignore[assignment]
    Infotype = None  # type: ignore[assignment]
    Locationtype = None  # type: ignore[assignment]
    ModificationInfotype = None  # type: ignore[assignment]
    MusicalKeytype = None  # type: ignore[assignment]
    Tempotype = None  # type: ignore[assignment]
    TRAKTOR_UTILS_AVAILABLE = False

from djlib.djlib_tags import (
    generate_track_id,
    write_djlib_tags,
    read_djlib_tags,
    has_djlib_tags,
)


# ============ RATING CONVERSION HELPERS ============

def traktor_rating_to_stars(traktor_ranking: int) -> int:
    """
    Convert Traktor ranking (0-255) to star rating (0-5).
    
    Traktor scale: 0=unrated, 51=1★, 102=2★, 153=3★, 204=4★, 255=5★
    
    Args:
        traktor_ranking: Traktor INFO.RANKING value (0-255)
    
    Returns:
        Star rating 0-5
    """
    if traktor_ranking == 0:
        return 0
    # Round to nearest star: 0-25=0★, 26-76=1★, 77-127=2★, etc.
    return min(5, max(0, round(traktor_ranking / 51.0)))


def stars_to_traktor_rating(stars: int) -> int:
    """
    Convert star rating (0-5) to Traktor ranking (0-255).
    
    Args:
        stars: Star rating 0-5
    
    Returns:
        Traktor INFO.RANKING value (0-255)
    """
    if stars == 0:
        return 0
    return min(255, max(0, stars * 51))


def rekordbox_rating_to_stars(rekordbox_rating: int) -> int:
    """
    Convert Rekordbox rating to star rating.
    
    Rekordbox already uses 0-5 scale, so this is identity function.
    
    Args:
        rekordbox_rating: Rekordbox Rating field (0-5)
    
    Returns:
        Star rating 0-5
    """
    return min(5, max(0, rekordbox_rating))


def stars_to_rekordbox_rating(stars: int) -> int:
    """
    Convert star rating to Rekordbox rating.
    
    Rekordbox already uses 0-5 scale, so this is identity function.
    
    Args:
        stars: Star rating 0-5
    
    Returns:
        Rekordbox Rating value (0-5)
    """
    return min(5, max(0, stars))


# ============ HELPER: GET DJ SOFTWARE TRACK IDS ============

def get_rekordbox_track_ids() -> Dict[Path, str]:
    """
    Get mapping of file paths to Rekordbox IDs from current Rekordbox database.
    
    Returns:
        Dict[Path, str]: {file_path: rekordbox_id}
        Empty dict if Rekordbox not available or no database found.
    """
    if not PYREKORDBOX_AVAILABLE:
        return {}
    
    rekordbox_db_path = Path.home() / "Library" / "Pioneer" / "rekordbox" / "master.db"
    if not rekordbox_db_path.exists():
        return {}
    
    try:
        assert Rekordbox6Database is not None, "pyrekordbox not available"
        db = Rekordbox6Database(rekordbox_db_path)
        mapping: Dict[Path, str] = {}
        
        for content in db.get_content():
            full_path = getattr(content, 'FolderPath', '').strip()
            if not full_path:
                continue
            
            file_path = Path(full_path)
            rekordbox_id = str(getattr(content, 'ID', ''))
            
            if rekordbox_id:
                mapping[file_path] = rekordbox_id
        
        return mapping
    except Exception as e:
        print(f"⚠️  Warning: Could not read Rekordbox database: {e}")
        return {}


def get_rekordbox_by_track_id() -> Dict[str, tuple[str, Path]]:
    """
    Get mapping of our track_id to (rekordbox_id, file_path) by reading custom tags from files in Rekordbox.
    
    Returns:
        Dict[str, tuple[str, Path]]: {track_id: (rekordbox_id, file_path)}
        Empty dict if Rekordbox not available or no database found.
    """
    if not PYREKORDBOX_AVAILABLE:
        return {}
    
    rekordbox_db_path = Path.home() / "Library" / "Pioneer" / "rekordbox" / "master.db"
    if not rekordbox_db_path.exists():
        return {}
    
    try:
        assert Rekordbox6Database is not None, "pyrekordbox not available"
        db = Rekordbox6Database(rekordbox_db_path)
        mapping: Dict[str, tuple[str, Path]] = {}
        
        for content in db.get_content():
            full_path = getattr(content, 'FolderPath', '').strip()
            if not full_path:
                continue
            
            file_path = Path(full_path)
            rekordbox_id = str(getattr(content, 'ID', ''))
            if not rekordbox_id:
                continue
            
            # Try to read our custom track_id from file (may not exist if moved)
            # If file doesn't exist at path in DB, we'll still need rekordbox_id to update it
            try:
                if file_path.exists():
                    djlib_tags = read_djlib_tags(file_path)
                    track_id = djlib_tags.get('track_id', '')
                    if track_id:
                        mapping[track_id] = (rekordbox_id, file_path)
            except Exception:
                pass  # Skip files with unreadable tags
        
        return mapping
    except Exception as e:
        print(f"⚠️  Warning: Could not read Rekordbox database: {e}")
        return {}


def get_traktor_track_ids(collection_nml_path: Optional[Path] = None) -> Dict[Path, str]:
    """
    Get mapping of file paths to Traktor AUDIO_IDs from collection.nml.
    
    Args:
        collection_nml_path: Path to collection.nml. If None, tries default locations.
    
    Returns:
        Dict[Path, str]: {file_path: traktor_audio_id}
        Empty dict if collection.nml not found.
    """
    # Try default locations if not specified
    if collection_nml_path is None:
        docs = Path.home() / "Documents" / "Native Instruments"
        if docs.exists():
            # Try to find any Traktor version
            for traktor_dir in docs.glob("Traktor*"):
                nml = traktor_dir / "collection.nml"
                if nml.exists():
                    collection_nml_path = nml
                    break
    
    if collection_nml_path is None or not collection_nml_path.exists():
        return {}
    
    try:
        tree = ET.parse(collection_nml_path)
        root = tree.getroot()
        mapping: Dict[Path, str] = {}
        
        for entry in root.findall(".//ENTRY"):
            location = entry.find("LOCATION")
            if location is None:
                continue
            
            dir_path = location.get("DIR", "")
            file_name = location.get("FILE", "")
            volume = location.get("VOLUME", "")
            
            if not dir_path or not file_name:
                continue
            
            # Reconstruct full path from Traktor format
            # Traktor DIR format: /:<part1>/:part2/:part3/:
            # Volume info: VOLUME="Macintosh HD" or VOLUME="<external-drive>"
            parts = [p for p in dir_path.split('/:') if p]
            
            # macOS: /Volumes/<drive>/<rest> or /<root-path>
            if volume and volume != "Macintosh HD":
                full_path = Path("/Volumes") / volume / "/".join(parts) / file_name
            else:
                full_path = Path("/") / "/".join(parts) / file_name
            
            traktor_id = entry.get("AUDIO_ID", "") or entry.get("PRIMARYKEY", "")
            
            if traktor_id:
                mapping[full_path] = traktor_id
        
        return mapping
    except Exception as e:
        print(f"⚠️  Warning: Could not read Traktor collection: {e}")
        return {}


def get_traktor_by_track_id(collection_nml_path: Optional[Path] = None) -> Dict[str, tuple[str, Path]]:
    """
    Get mapping of our track_id to (traktor_id, file_path) by reading custom tags from files in Traktor.
    
    Args:
        collection_nml_path: Path to collection.nml. If None, tries default locations.
    
    Returns:
        Dict[str, tuple[str, Path]]: {track_id: (traktor_audio_id, file_path)}
        Empty dict if collection.nml not found.
    """
    # Try default locations if not specified
    if collection_nml_path is None:
        docs = Path.home() / "Documents" / "Native Instruments"
        if docs.exists():
            for traktor_dir in docs.glob("Traktor*"):
                nml = traktor_dir / "collection.nml"
                if nml.exists():
                    collection_nml_path = nml
                    break
    
    if collection_nml_path is None or not collection_nml_path.exists():
        return {}
    
    try:
        tree = ET.parse(collection_nml_path)
        root = tree.getroot()
        mapping: Dict[str, tuple[str, Path]] = {}
        
        for entry in root.findall(".//ENTRY"):
            location = entry.find("LOCATION")
            if location is None:
                continue
            
            dir_path = location.get("DIR", "")
            file_name = location.get("FILE", "")
            volume = location.get("VOLUME", "")
            
            if not dir_path or not file_name:
                continue
            
            # Reconstruct full path
            parts = [p for p in dir_path.split('/:') if p]
            if volume and volume != "Macintosh HD":
                full_path = Path("/Volumes") / volume / "/".join(parts) / file_name
            else:
                full_path = Path("/") / "/".join(parts) / file_name
            
            if not full_path.exists():
                continue
            
            traktor_id = entry.get("AUDIO_ID", "") or entry.get("PRIMARYKEY", "")
            if not traktor_id:
                continue
            
            # Read our custom track_id from file
            try:
                djlib_tags = read_djlib_tags(full_path)
                track_id = djlib_tags.get('track_id', '')
                if track_id:
                    mapping[track_id] = (traktor_id, full_path)
            except Exception:
                pass  # Skip files with unreadable tags
        
        return mapping
    except Exception as e:
        print(f"⚠️  Warning: Could not read Traktor collection: {e}")
        return {}


# ============ PHASE 1: IMPORT SNAPSHOTS (READ-ONLY) ============

def _tag_single_file(
    file_path: Path,
    track_id: str,
    rekordbox_id: Optional[str] = None,
    traktor_id: Optional[str] = None,
    original_path: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Tag a single file with DJLIB custom tags.
    
    Returns: (success: bool, status: str)
    """
    if not file_path.exists():
        return (False, "not_found")
    
    try:
        # Check if already tagged
        existing_tags = read_djlib_tags(file_path)
        if existing_tags.get('track_id'):
            # Already tagged, return existing track_id
            return (True, "skipped")
        
        # Write new tags
        write_djlib_tags(
            file_path,
            track_id=track_id,
            rekordbox_id=rekordbox_id,
            traktor_id=traktor_id,
            original_path=original_path,
        )
        return (True, "tagged")
    except Exception as e:
        return (False, f"error: {e}")


def import_rekordbox_snapshot(
    output_path: Path, 
    tag_files: bool = False,
    workers: int = 1
) -> int:
    """
    Create a CSV snapshot of Rekordbox collection for path mapping.
    
    This is READ-ONLY for Rekordbox database.
    Optionally writes DJLIB custom tags to audio files for persistent track IDs.
    
    Args:
        output_path: Where to save snapshot CSV
        tag_files: If True, write DJLIB_TRACK_ID to audio file tags
        workers: Number of parallel workers for tagging (1 = sequential, 4 = 4x faster)
    
    Returns: Number of tracks exported
    """
    if not PYREKORDBOX_AVAILABLE:
        raise RuntimeError(
            "pyrekordbox not available. Install with: pip install pyrekordbox"
        )
    
    # Default Rekordbox DB location (macOS)
    rekordbox_db_path = Path.home() / "Library" / "Pioneer" / "rekordbox" / "master.db"
    
    if not rekordbox_db_path.exists():
        raise FileNotFoundError(
            f"Rekordbox database not found at: {rekordbox_db_path}\n"
            f"Make sure Rekordbox 6 is installed and has been opened at least once."
        )
    
    print(f"📖 Reading Rekordbox database: {rekordbox_db_path}")
    
    # Open database (read-only)
    assert Rekordbox6Database is not None, "pyrekordbox not available"
    db = Rekordbox6Database(rekordbox_db_path)
    
    # Get all tracks - first pass to collect track data
    tracks = []
    snapshot_date = datetime.now(timezone.utc).isoformat()
    tracks_to_tag: List[Dict[str, Any]] = []
    
    for content in db.get_content():  # type: ignore
        # Extract file path
        # NOTE: FolderPath in Rekordbox DB is actually the full file path, not just folder
        full_path = getattr(content, 'FolderPath', '').strip()
        
        # Skip if no valid path
        if not full_path:
            continue
        
        file_path = Path(full_path)
        rekordbox_id = str(getattr(content, 'ID', ''))
        artist = getattr(content, 'ArtistName', '')
        title = getattr(content, 'Title', '')  # Fixed: was TrackTitle
        
        # Generate our internal track_id
        track_id = generate_track_id(file_path, artist, title)
        
        # Extract BPM (stored as int * 100 in Rekordbox)
        bpm_raw = getattr(content, 'BPM', None)
        bpm_str = str(bpm_raw / 100.0) if bpm_raw else ''
        
        # Extract metadata
        track_data = {
            'external_source': 'rekordbox',
            'external_track_id': rekordbox_id,
            'track_id': track_id,  # OUR internal ID
            'old_full_path': full_path,
            'artist': artist,
            'title': title,
            'bpm': bpm_str,  # Fixed: was Tempo, now BPM/100
            'key': getattr(content, 'KeyName', ''),  # Fixed: was KeyText
            'rating': str(getattr(content, 'Rating', '')),
            'color': str(getattr(content, 'ColorID', '')),
            'duration_seconds': str(getattr(content, 'Length', 0)),  # Track length in seconds
            'date_added': str(getattr(content, 'StockDate', '')),  # Fixed: was DateCreated (which is metadata date, not import date)
            'last_played': str(getattr(content, 'LastPlayed', '')),
            'play_count': str(getattr(content, 'DJPlayCount', '')),  # Fixed: was PlayCount
            'snapshot_date': snapshot_date,
        }
        
        tracks.append(track_data)
        
        # Collect files to tag
        if tag_files and file_path.exists():
            tracks_to_tag.append({
                'file_path': file_path,
                'track_id': track_id,
                'rekordbox_id': rekordbox_id,
                'original_path': full_path,
            })
    
    # Tag files in parallel (if requested)
    tagged_count = 0
    skipped_count = 0
    error_count = 0
    
    if tag_files and tracks_to_tag:
        print(f"📝 Tagging {len(tracks_to_tag)} files with DJLIB_TRACK_ID (workers={workers})...")
        
        if workers > 1:
            # Parallel tagging with ThreadPoolExecutor
            try:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {
                        executor.submit(
                            _tag_single_file,
                            t['file_path'],
                            t['track_id'],
                            t['rekordbox_id'],
                            None,  # traktor_id
                            t['original_path'],
                        ): t['file_path']
                        for t in tracks_to_tag
                    }
                    
                    for future in as_completed(futures):
                        try:
                            success, status = future.result()
                            if status == "tagged":
                                tagged_count += 1
                            elif status == "skipped":
                                skipped_count += 1
                            else:
                                error_count += 1
                        except Exception as e:
                            # Thread exception - log and continue
                            error_count += 1
                            print(f"⚠️  Thread error: {e}")
            except Exception as e:
                # ThreadPoolExecutor failed - fall back to sequential
                print(f"⚠️  Parallel tagging failed ({e}), falling back to sequential...")
                tagged_count = 0
                skipped_count = 0
                error_count = 0
                for t in tracks_to_tag:
                    success, status = _tag_single_file(
                        t['file_path'],
                        t['track_id'],
                        t['rekordbox_id'],
                        None,  # traktor_id
                        t['original_path'],
                    )
                    if status == "tagged":
                        tagged_count += 1
                    elif status == "skipped":
                        skipped_count += 1
                    else:
                        error_count += 1
        else:
            # Sequential tagging
            for t in tracks_to_tag:
                success, status = _tag_single_file(
                    t['file_path'],
                    t['track_id'],
                    t['rekordbox_id'],
                    None,  # traktor_id
                    t['original_path'],
                )
                if status == "tagged":
                    tagged_count += 1
                elif status == "skipped":
                    skipped_count += 1
                else:
                    error_count += 1
    
    # Write CSV
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with output_path.open('w', newline='', encoding='utf-8') as f:
        if tracks:
            writer = csv.DictWriter(f, fieldnames=tracks[0].keys())
            writer.writeheader()
            writer.writerows(tracks)
    
    print(f"✅ Exported {len(tracks)} tracks to: {output_path}")
    if tag_files:
        print(f"📝 Tagged {tagged_count} files with DJLIB_TRACK_ID")
        if skipped_count:
            print(f"⏭️  Skipped {skipped_count} files (already tagged)")
        if error_count:
            print(f"⚠️  Errors: {error_count} files could not be tagged")
    return len(tracks)


def import_traktor_snapshot(collection_nml_path: Path, output_path: Path, tag_files: bool = False, workers: int = 1) -> int:
    """
    Create a CSV snapshot of Traktor collection for path mapping.
    
    This is READ-ONLY for Traktor collection.nml.
    Optionally writes DJLIB custom tags to audio files for persistent track IDs.
    
    Args:
        collection_nml_path: Path to Traktor collection.nml
        output_path: Where to save snapshot CSV
        tag_files: If True, write DJLIB_TRACK_ID to audio file tags
        workers: Number of parallel workers for tagging (1 = sequential, 4 = 4x faster)
    
    Returns: Number of tracks exported
    """
    if not collection_nml_path.exists():
        raise FileNotFoundError(
            f"Traktor collection.nml not found at: {collection_nml_path}\n"
            f"Typical location: ~/Documents/Native Instruments/Traktor X.X.X/collection.nml"
        )
    
    print(f"📖 Reading Traktor collection: {collection_nml_path}")
    
    # Use traktor-nml-utils API (not raw XML parsing)
    collection = TraktorCollection(collection_nml_path)
    if not collection.nml.collection or not collection.nml.collection.entry:
        raise ValueError("Invalid Traktor collection.nml: no tracks found")
    
    tracks = []
    snapshot_date = datetime.now(timezone.utc).isoformat()
    tracks_to_tag: List[Dict[str, Any]] = []
    
    # Iterate through entries using traktor-nml-utils API
    for entry in collection.nml.collection.entry:
        # Extract location
        if not entry.location:
            continue
        
        location = entry.location
        dir_path = location.dir or ""
        file_name = location.file or ""
        
        if not file_name:
            continue
        
        # Reconstruct full path
        # Traktor uses :/ as path separators, convert to /
        full_path_raw = str(Path(dir_path) / file_name)
        # Normalize Traktor path format: /:Users/:path → /Users/path
        full_path = full_path_raw.replace('/:', '/').replace(':/', '/')
        # Remove doubled slashes (Traktor sometimes adds extra /)
        while '//' in full_path:
            full_path = full_path.replace('//', '/')
        
        # Extract metadata using traktor-nml-utils API (not raw XML)
        artist = entry.artist or ""
        title = entry.title or ""
        
        # Extract tempo (BPM)
        bpm = ""
        if entry.tempo:
            bpm = str(entry.tempo.bpm)
        
        # Extract musical key from INFO.KEY (text like "12B"), not MUSICAL_KEY.VALUE (numeric ID)
        key = ""
        if entry.info and entry.info.key:
            key = entry.info.key
        
        # Extract playback stats from INFO tag
        rating = ""
        play_count = ""
        last_played = ""
        if entry.info:
            if entry.info.ranking:
                # Convert Traktor ranking (0-255) to stars (0-5)
                stars = traktor_rating_to_stars(entry.info.ranking)
                rating = str(stars)
            if entry.info.playcount:
                play_count = str(entry.info.playcount)
            if entry.info.last_played:
                last_played = str(entry.info.last_played)
        
        # Count cue points (indicates track usage)
        cue_count = len(entry.cue_v2) if entry.cue_v2 else 0
        
        # Generate our internal track_id first (needed for fallback)
        file_path_obj = Path(full_path)
        internal_track_id = generate_track_id(file_path_obj, artist, title)
        
        # Get track ID - Traktor uses AUDIO_ID, fallback to our track_id if missing
        # Some tracks in Traktor don't have AUDIO_ID (not analyzed)
        traktor_id = entry.audio_id or internal_track_id
        
        track_data = {
            'external_source': 'traktor',
            'external_track_id': traktor_id,
            'track_id': internal_track_id,  # OUR internal ID
            'old_full_path': full_path,
            'artist': artist,
            'title': title,
            'bpm': bpm,
            'key': key,  # Fixed: now uses INFO.KEY (12B) not MUSICAL_KEY.VALUE (16)
            'rating': rating,  # NEW: Traktor ranking (0-255)
            'play_count': play_count,  # NEW: play count
            'last_played': last_played,  # NEW: last played date
            'cue_count': str(cue_count),
            'snapshot_date': snapshot_date,
        }
        
        tracks.append(track_data)
        
        # Collect files to tag
        if tag_files and file_path_obj.exists():
            tracks_to_tag.append({
                'file_path': file_path_obj,
                'track_id': internal_track_id,
                'traktor_id': traktor_id,
                'original_path': full_path,
            })
    
    # Tag files in parallel (if requested)
    tagged_count = 0
    skipped_count = 0
    error_count = 0
    
    if tag_files and tracks_to_tag:
        print(f"📝 Tagging {len(tracks_to_tag)} files with DJLIB_TRACK_ID (workers={workers})...")
        
        if workers > 1:
            # Parallel tagging with ThreadPoolExecutor
            try:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {
                        executor.submit(
                            _tag_single_file,
                            t['file_path'],
                            t['track_id'],
                            None,  # rekordbox_id
                            t['traktor_id'],
                            t['original_path'],
                        ): t['file_path']
                        for t in tracks_to_tag
                    }
                    
                    for future in as_completed(futures):
                        try:
                            success, status = future.result()
                            if status == "tagged":
                                tagged_count += 1
                            elif status == "skipped":
                                skipped_count += 1
                            else:
                                error_count += 1
                        except Exception as e:
                            # Thread exception - log and continue
                            error_count += 1
                            print(f"⚠️  Thread error: {e}")
            except Exception as e:
                # ThreadPoolExecutor failed - fall back to sequential
                print(f"⚠️  Parallel tagging failed ({e}), falling back to sequential...")
                tagged_count = 0
                skipped_count = 0
                error_count = 0
                for t in tracks_to_tag:
                    success, status = _tag_single_file(
                        t['file_path'],
                        t['track_id'],
                        None,  # rekordbox_id
                        t['traktor_id'],
                        t['original_path'],
                    )
                    if status == "tagged":
                        tagged_count += 1
                    elif status == "skipped":
                        skipped_count += 1
                    else:
                        error_count += 1
        else:
            # Sequential tagging
            for t in tracks_to_tag:
                success, status = _tag_single_file(
                    t['file_path'],
                    t['track_id'],
                    None,  # rekordbox_id
                    t['traktor_id'],
                    t['original_path'],
                )
                if status == "tagged":
                    tagged_count += 1
                elif status == "skipped":
                    skipped_count += 1
                else:
                    error_count += 1
    
    # Write CSV
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with output_path.open('w', newline='', encoding='utf-8') as f:
        if tracks:
            writer = csv.DictWriter(f, fieldnames=tracks[0].keys())
            writer.writeheader()
            writer.writerows(tracks)
    
    print(f"✅ Exported {len(tracks)} tracks to: {output_path}")
    if tag_files:
        print(f"📝 Tagged {tagged_count} files with DJLIB_TRACK_ID")
        if skipped_count:
            print(f"⏭️  Skipped {skipped_count} files (already tagged)")
        if error_count:
            print(f"⚠️  Errors: {error_count} files could not be tagged")
    return len(tracks)


# ============ PHASE 2: PATH MAPPING (READ-ONLY) ============

def load_snapshot(snapshot_path: Path) -> List[Dict[str, str]]:
    """Load a snapshot CSV file."""
    rows = []
    with snapshot_path.open('r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def find_in_snapshot(snapshot_rows: List[Dict[str, str]], 
                     old_path: str, 
                     source: str) -> Optional[str]:
    """
    Find track ID in snapshot by old file path.
    
    Args:
        snapshot_rows: List of snapshot records
        old_path: Original file path to search for
        source: 'rekordbox' or 'traktor'
    
    Returns:
        external_track_id if found, None otherwise
    """
    old_path_normalized = str(Path(old_path))
    
    for row in snapshot_rows:
        if row.get('external_source') != source:
            continue
        
        snapshot_path = row.get('old_full_path', '')
        snapshot_path_normalized = str(Path(snapshot_path))
        
        if snapshot_path_normalized == old_path_normalized:
            return row.get('external_track_id')
    
    return None


def create_path_map(move_log_path: Path,
                    rekordbox_snapshot_path: Optional[Path] = None,
                    traktor_snapshot_path: Optional[Path] = None,
                    output_path: Optional[Path] = None) -> Path:
    """
    Create a path mapping CSV from a move log and external snapshots.
    
    This is READ-ONLY - does not modify any databases.
    
    Args:
        move_log_path: Path to move log from apply command
        rekordbox_snapshot_path: Optional Rekordbox snapshot CSV
        traktor_snapshot_path: Optional Traktor snapshot CSV
        output_path: Where to save path map (auto-generated if None)
    
    Returns:
        Path to created path map CSV
    """
    # Load move log
    if not move_log_path.exists():
        raise FileNotFoundError(f"Move log not found: {move_log_path}")
    
    move_log = []
    with move_log_path.open('r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            move_log.append(row)
    
    # Load snapshots
    rekordbox_snapshot = []
    traktor_snapshot = []
    
    if rekordbox_snapshot_path and rekordbox_snapshot_path.exists():
        rekordbox_snapshot = load_snapshot(rekordbox_snapshot_path)
        print(f"📖 Loaded Rekordbox snapshot: {len(rekordbox_snapshot)} tracks")
    
    if traktor_snapshot_path and traktor_snapshot_path.exists():
        traktor_snapshot = load_snapshot(traktor_snapshot_path)
        print(f"📖 Loaded Traktor snapshot: {len(traktor_snapshot)} tracks")
    
    # Create path map
    path_map = []
    
    for move_entry in move_log:
        old_path = move_entry.get('src', '')
        new_path = move_entry.get('dest', '')
        track_id = move_entry.get('track_id', '')
        
        if not old_path or not new_path:
            continue
        
        # Find in snapshots
        rekordbox_id = None
        traktor_id = None
        
        if rekordbox_snapshot:
            rekordbox_id = find_in_snapshot(rekordbox_snapshot, old_path, 'rekordbox')
        
        if traktor_snapshot:
            traktor_id = find_in_snapshot(traktor_snapshot, old_path, 'traktor')
        
        path_map.append({
            'track_id': track_id,
            'old_path': old_path,
            'new_path': new_path,
            'rekordbox_id': rekordbox_id or '',
            'traktor_id': traktor_id or '',
        })
    
    # Write path map
    if output_path is None:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = Path('LOGS') / 'path_maps' / f'path_map_{timestamp}.csv'
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with output_path.open('w', newline='', encoding='utf-8') as f:
        if path_map:
            writer = csv.DictWriter(f, fieldnames=path_map[0].keys())
            writer.writeheader()
            writer.writerows(path_map)
    
    # Summary
    found_rekordbox = sum(1 for m in path_map if m['rekordbox_id'])
    found_traktor = sum(1 for m in path_map if m['traktor_id'])
    
    print(f"\n✅ Created path map: {output_path}")
    print(f"   Total moves: {len(path_map)}")
    print(f"   Found in Rekordbox: {found_rekordbox}")
    print(f"   Found in Traktor: {found_traktor}")
    
    return output_path


# ============ PHASE 3: PATH SYNC (WRITE - EXPLICIT OPT-IN) ============

def refresh_rekordbox_cover_art(file_path: Path) -> bool:
    """
    Extract cover art from MP3 and add to Rekordbox database.
    
    Rekordbox stores cover art as external JPEG files in /PIONEER/Artwork/{uuid}/artwork.jpg
    and references them via Content.ImagePath. This function:
    1. Extracts APIC frame from audio file
    2. Saves as JPEG in Rekordbox artwork directory
    3. Updates Content.ImagePath in database
    
    Args:
        file_path: Absolute path to the audio file
        
    Returns:
        True if successfully added cover art to Rekordbox, False otherwise
    """
    if not PYREKORDBOX_AVAILABLE:
        return False
    
    # Check if Rekordbox is running - must be closed for DB modifications
    pid = get_rekordbox_pid()
    if pid:
        print(f"⚠️  Rekordbox is running (PID {pid}). Please close Rekordbox before updating cover art.")
        return False
    
    rekordbox_db_path = Path.home() / "Library" / "Pioneer" / "rekordbox" / "master.db"
    if not rekordbox_db_path.exists():
        return False
    
    # Rekordbox artwork directory
    artwork_base = Path.home() / "Library" / "Pioneer" / "rekordbox" / "share" / "PIONEER" / "Artwork"
    
    try:
        # 1. Extract cover art from audio file
        from mutagen.id3 import ID3
        audio = ID3(str(file_path))
        
        # Find APIC frame
        apic_data = None
        for key in audio.keys():
            if key.startswith('APIC'):
                apic_data = audio[key].data
                break
        
        if not apic_data:
            # No cover art in file
            return False
        
        # 2. Convert/resize cover art to match Rekordbox format (500x500, optimized JPEG)
        from PIL import Image
        import io
        
        # Load image from APIC data
        img = Image.open(io.BytesIO(apic_data))
        
        # Resize to 500x500 (Rekordbox standard size)
        if img.size != (500, 500):
            # Use high-quality resampling
            img = img.resize((500, 500), Image.Resampling.LANCZOS)
        
        # Convert to RGB if needed (remove alpha channel)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Save as optimized JPEG
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=85, optimize=True)
        optimized_data = output.getvalue()
        
        # 3. Save to Rekordbox artwork directory with UUID structure
        import uuid
        artwork_uuid = str(uuid.uuid4())  # e.g., 'f49ed70d-2592-46c8-8514-5c6fa0345c34'
        
        # Rekordbox splits UUID: first 3 chars as folder, rest as subfolder
        # Format: /PIONEER/Artwork/{uuid[:3]}/{uuid[3:]}/artwork.jpg
        uuid_folder = artwork_uuid[:3]        # e.g., 'f49'
        uuid_subfolder = artwork_uuid[3:]     # e.g., 'ed70d-2592-46c8-8514-5c6fa0345c34'
        
        artwork_subdir = artwork_base / uuid_folder / uuid_subfolder
        artwork_subdir.mkdir(parents=True, exist_ok=True)
        artwork_file = artwork_subdir / "artwork.jpg"
        
        # Write optimized JPEG (not original APIC data)
        with artwork_file.open('wb') as f:
            f.write(optimized_data)
        
        # 3. Update Rekordbox database
        assert Rekordbox6Database is not None
        db = Rekordbox6Database(rekordbox_db_path)
        
        # Find track by path
        file_path_str = str(file_path)
        content = None
        for c in db.get_content():
            if getattr(c, 'FolderPath', '') == file_path_str:
                content = c
                break
        
        if not content:
            # Track not in Rekordbox yet
            db.close()
            return False
        
        # Set ImagePath using Rekordbox's UUID-based path format
        rekordbox_image_path = f"/PIONEER/Artwork/{uuid_folder}/{uuid_subfolder}/artwork.jpg"
        content.ImagePath = rekordbox_image_path
        
        db.commit()
        db.close()
        return True
        
    except Exception:
        return False

def add_tracks_to_rekordbox(
    tracks: List[Dict[str, Any]],
    dry_run: bool = True,
    update_existing: bool = True
) -> Tuple[int, int]:
    """
    Add new tracks to Rekordbox database and update paths for existing tracks.
    
    ⚠️ DANGER ZONE: This MODIFIES Rekordbox database!
    
    Args:
        tracks: List of track dicts with keys: file_path, artist, title, bpm, key, rekordbox_id
        dry_run: If True (default), don't write changes. If False, apply.
        update_existing: If True, update paths for tracks already in database
    
    Returns:
        Tuple[int, int]: (added_count, updated_count)
    """
    if not PYREKORDBOX_AVAILABLE:
        raise RuntimeError("pyrekordbox not available. Install with: pip install pyrekordbox")
    
    rekordbox_db_path = Path.home() / "Library" / "Pioneer" / "rekordbox" / "master.db"
    if not rekordbox_db_path.exists():
        raise FileNotFoundError(f"Rekordbox database not found: {rekordbox_db_path}")
    
    if dry_run:
        print(f"🔍 DRY-RUN: Would connect to Rekordbox DB: {rekordbox_db_path}")
        added_count = len([t for t in tracks if not t.get('rekordbox_id')])
        updated_count = len([t for t in tracks if t.get('rekordbox_id')])
        if added_count > 0:
            print(f"🔍 DRY-RUN: Would add {added_count} new tracks to Rekordbox")
        if updated_count > 0:
            print(f"🔍 DRY-RUN: Would update {updated_count} existing track paths")
        return (added_count, updated_count)
    
    # Backup database (important!)
    import shutil
    backup_path = rekordbox_db_path.with_suffix('.db.backup')
    shutil.copy2(rekordbox_db_path, backup_path)
    print(f"📦 Backup created: {backup_path}")
    
    # Open database (WRITE mode)
    db = Rekordbox6Database(rekordbox_db_path)  # type: ignore
    
    added_count = 0
    updated_count = 0
    failed_updates = []
    failed_adds = []
    
    try:
        for track in tracks:
            file_path = Path(track['file_path'])
            rekordbox_id = track.get('rekordbox_id', '')
            
            if not file_path.exists():
                continue
            
            # Check if track already exists
            if rekordbox_id:
                if update_existing:
                    # Update existing track path and make sure the GUI "File Name" column matches
                    try:
                        rb_id_int = int(rekordbox_id)
                    except (TypeError, ValueError):
                        print(f"⚠️  Invalid Rekordbox ID '{rekordbox_id}' for {file_path.name}")
                        failed_updates.append(file_path.name)
                        continue

                    try:
                        content = db.get_content(ID=rb_id_int)
                        if content is None:
                            raise RuntimeError(f"Rekordbox ID {rb_id_int} not found")

                        # Update path directly without refreshing ANLZ (avoids parsing errors)
                        old_path = getattr(content, 'FolderPath', '')
                        if old_path != str(file_path):
                            content.FolderPath = str(file_path)
                        
                        file_name_only = file_path.name
                        if getattr(content, "FileNameL", None) != file_name_only:
                            content.FileNameL = file_name_only
                        
                        # ✅ AUTO-REFRESH: Update tags in Rekordbox DB
                        try:
                            from djlib.tags import read_tags
                            file_tags = read_tags(file_path)
                            
                            # Update Artist (find or create)
                            if file_tags.get('artist'):
                                artist_name = file_tags['artist']
                                artist_obj = db.get_artist(Name=artist_name)
                                if artist_obj:
                                    artist_instance = artist_obj.first()
                                    if artist_instance:
                                        content.ArtistID = artist_instance.ID
                                    else:
                                        # Artist doesn't exist - create it
                                        new_artist = db.add_artist(name=artist_name)
                                        if new_artist:
                                            content.ArtistID = new_artist.ID
                            
                            # Update Title
                            if file_tags.get('title'):
                                content.Title = file_tags['title']
                            
                            # Update Album (clear if empty, or find/create if has value)
                            album_value = file_tags.get('album', '').strip()
                            if not album_value:
                                # Clear album - set to NULL/empty
                                content.AlbumID = None
                            else:
                                # Find or create album
                                album_obj = db.get_album(Name=album_value)
                                if album_obj:
                                    album_instance = album_obj.first()
                                    if album_instance:
                                        content.AlbumID = album_instance.ID
                                    else:
                                        # Album doesn't exist - create it
                                        new_album = db.add_album(name=album_value)
                                        if new_album:
                                            content.AlbumID = new_album.ID
                            
                            # Update Genre (find or create)
                            if file_tags.get('genre'):
                                genre_name = file_tags['genre']
                                genre_obj = db.get_genre(Name=genre_name)
                                if genre_obj:
                                    genre_instance = genre_obj.first()
                                    if genre_instance:
                                        content.GenreID = genre_instance.ID
                                    else:
                                        # Genre doesn't exist - create it
                                        new_genre = db.add_genre(name=genre_name)
                                        if new_genre:
                                            content.GenreID = new_genre.ID
                            
                            # Update Key (find existing)
                            if file_tags.get('key_camelot'):
                                key_name = file_tags['key_camelot']
                                key_obj = db.get_key(ScaleName=key_name)
                                if key_obj:
                                    key_instance = key_obj.first()
                                    if key_instance:
                                        content.KeyID = key_instance.ID
                            
                            # Update Year
                            if file_tags.get('year'):
                                try:
                                    year_int = int(file_tags['year'])
                                    content.ReleaseYear = year_int
                                except (ValueError, TypeError):
                                    pass
                            
                            # Update BPM (Rekordbox stores BPM * 100, e.g. 118 BPM = 11800)
                            if file_tags.get('bpm'):
                                try:
                                    bpm_float = float(file_tags['bpm'])
                                    content.BPM = int(bpm_float * 100)
                                except (ValueError, TypeError):
                                    pass
                            
                            # Clear ImagePath (cover art) - Rekordbox will auto-detect from file
                            content.ImagePath = ""
                                    
                        except Exception as tag_err:
                            print(f"   ⚠️  Could not refresh tags for {file_path.name}: {tag_err}")

                        updated_count += 1
                    except Exception as e:
                        print(f"⚠️  Failed to update {file_path.name}: {e}")
                        failed_updates.append(file_path.name)
                        # Skip this file but continue with others
                        continue
            else:
                # Add new track
                try:
                    # add_content() automatically analyzes and imports the file
                    content = db.add_content(str(file_path))
                    if content:
                        added_count += 1
                except Exception as e:
                    print(f"⚠️  Failed to add {file_path.name}: {e}")
                    failed_adds.append(file_path.name)
                    # Skip this file but continue with others
                    continue
        
        # Commit successful updates even if some failed
        db.commit()
        
        if added_count > 0:
            print(f"✅ Added {added_count} new tracks to Rekordbox")
        if updated_count > 0:
            print(f"🔄 Updated {updated_count} existing track paths in Rekordbox")
        
        if failed_updates:
            print(f"⚠️  Failed to update {len(failed_updates)} tracks in Rekordbox")
            for name in failed_updates:
                print(f"   - {name}")
        if failed_adds:
            print(f"⚠️  Failed to add {len(failed_adds)} tracks to Rekordbox")
            for name in failed_adds:
                print(f"   - {name}")
        
    finally:
        db.close()
    
    return (added_count, updated_count)


def sync_ratings_to_dj_software(
    library_csv_path: Path,
    dry_run: bool = True
) -> Tuple[int, int]:
    """
    Sync ratings from library.csv to Rekordbox and Traktor.
    
    Logic:
    - If track has rekordbox_id: update Rekordbox rating
    - If track has traktor_id: update Traktor rating
    - Converts star ratings (0-5) to native formats
    
    Args:
        library_csv_path: Path to library.csv with unified ratings
        dry_run: If True (default), don't write changes
    
    Returns:
        Tuple[int, int]: (rekordbox_updates, traktor_updates)
    """
    import pandas as pd
    
    if not library_csv_path.exists():
        raise FileNotFoundError(f"library.csv not found: {library_csv_path}")
    
    df = pd.read_csv(library_csv_path)
    
    # Filter tracks with ratings
    df['rating'] = pd.to_numeric(df['rating'], errors='coerce').fillna(0)
    df_with_rating = df[df['rating'] > 0].copy()
    
    rekordbox_updates = 0
    traktor_updates = 0
    
    # Sync to Rekordbox
    rb_tracks = df_with_rating[df_with_rating['rekordbox_id'].notna()].copy()
    if len(rb_tracks) > 0:
        rekordbox_updates = _sync_ratings_to_rekordbox(rb_tracks, dry_run)
    
    # Sync to Traktor
    tr_tracks = df_with_rating[df_with_rating['traktor_id'].notna()].copy()
    if len(tr_tracks) > 0:
        traktor_updates = _sync_ratings_to_traktor(tr_tracks, dry_run)
    
    return (rekordbox_updates, traktor_updates)


def _sync_ratings_to_rekordbox(df: Any, dry_run: bool) -> int:
    """Sync ratings to Rekordbox database."""
    if not PYREKORDBOX_AVAILABLE:
        print("⚠️  pyrekordbox not available - skipping Rekordbox rating sync")
        return 0
    
    rekordbox_db_path = Path.home() / "Library" / "Pioneer" / "rekordbox" / "master.db"
    if not rekordbox_db_path.exists():
        print(f"⚠️  Rekordbox database not found - skipping rating sync")
        return 0
    
    if dry_run:
        print(f"🔍 DRY-RUN: Would update {len(df)} Rekordbox ratings")
        return len(df)
    
    # Backup database
    import shutil
    backup_path = rekordbox_db_path.with_suffix('.db.backup-rating')
    shutil.copy2(rekordbox_db_path, backup_path)
    print(f"📦 Rekordbox backup: {backup_path.name}")
    
    db = Rekordbox6Database(rekordbox_db_path)  # type: ignore
    updated = 0
    
    try:
        for _, row in df.iterrows():
            try:
                rb_id = int(row['rekordbox_id'])
                stars = int(row['rating'])
                rb_rating = stars_to_rekordbox_rating(stars)
                
                content = db.get_content(ID=rb_id)
                if content is None:
                    continue
                
                # Update rating if changed
                current_rating = getattr(content, 'Rating', 0) or 0
                if current_rating != rb_rating:
                    content.Rating = rb_rating
                    updated += 1
                    
            except Exception as e:
                print(f"⚠️  Failed to update Rekordbox rating for ID {row.get('rekordbox_id')}: {e}")
        
        db.commit()
        print(f"✅ Updated {updated} Rekordbox ratings")
        
    finally:
        db.close()
    
    return updated


def _sync_ratings_to_traktor(df: Any, dry_run: bool) -> int:
    """Sync ratings to Traktor collection.nml."""
    if not TRAKTOR_UTILS_AVAILABLE:
        print("⚠️  traktor-nml-utils not available - skipping Traktor rating sync")
        return 0
    
    collection_path = Path.home() / "Documents" / "Native Instruments"
    # Find Traktor collection.nml (version may vary)
    nml_files = list(collection_path.glob("Traktor*/collection.nml"))
    if not nml_files:
        print(f"⚠️  Traktor collection.nml not found - skipping rating sync")
        return 0
    
    collection_nml_path = nml_files[0]
    
    if dry_run:
        print(f"🔍 DRY-RUN: Would update {len(df)} Traktor ratings")
        return len(df)
    
    # Backup collection.nml
    _backup_traktor_collection(collection_nml_path)
    
    collection = TraktorCollection(collection_nml_path)
    if not collection.nml.collection or not collection.nml.collection.entry:
        print("⚠️  Invalid Traktor collection")
        return 0
    
    # Build lookup: audio_id -> entry
    entry_map = {entry.audio_id: entry for entry in collection.nml.collection.entry if entry.audio_id}
    
    updated = 0
    for _, row in df.iterrows():
        try:
            traktor_id = row['traktor_id']
            stars = int(row['rating'])
            traktor_ranking = stars_to_traktor_rating(stars)
            
            entry = entry_map.get(traktor_id)
            if not entry:
                continue
            
            # Update ranking in INFO tag
            if not entry.info:
                entry.info = Infotype()
            
            current_ranking = entry.info.ranking or 0
            if current_ranking != traktor_ranking:
                entry.info.ranking = traktor_ranking
                updated += 1
                
        except Exception as e:
            print(f"⚠️  Failed to update Traktor rating for ID {row.get('traktor_id')}: {e}")
    
    if updated > 0:
        _safe_save_traktor_collection(collection_nml_path, collection.nml)
        print(f"✅ Updated {updated} Traktor ratings")
    
    return updated


TRAKTOR_BACKUP_TAG = "djlib-backup"


def _format_traktor_dir(directory: Path) -> str:
    """Translate a filesystem path to Traktor's DIR encoding."""
    resolved = directory.expanduser().resolve()
    parts = [part for part in resolved.parts if part not in ('/', '')]
    drive = resolved.drive.rstrip(':\\/') if resolved.drive else ''
    if drive and (not parts or parts[0] != drive):
        parts.insert(0, drive)
    if not parts:
        return '/:'
    formatted = '/:' + '/:'.join(parts)
    if not formatted.endswith('/:'):
        formatted += '/:'
    return formatted


def _guess_traktor_volume(file_path: Path) -> tuple[str, str]:
    """Best-effort guess of Traktor VOLUME/VOLUMEID attributes."""
    resolved = file_path.expanduser().resolve()
    parts = [part for part in resolved.parts if part not in ('/', '')]
    if len(parts) >= 2 and parts[0] == 'Volumes':
        return (parts[1], '')
    if resolved.drive:
        return (resolved.drive.rstrip(':\\/'), '')
    return ('Macintosh HD', '')


def _backup_traktor_collection(collection_path: Path) -> Path:
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_name = f"{collection_path.name}.{TRAKTOR_BACKUP_TAG}-{timestamp}"
    backup_path = collection_path.with_name(backup_name)
    shutil.copy2(collection_path, backup_path)
    print(f"📦 Backup created: {backup_path}")
    return backup_path


def _safe_save_traktor_collection(collection_path: Path, nml_obj: Any) -> None:
    """Safely save Traktor collection with validation."""
    serializer = XmlSerializer()
    
    try:
        serialized = serializer.render(nml_obj)
    except Exception as e:
        raise RuntimeError(f"Failed to serialize Traktor collection: {e}")
    
    # Remove leading whitespace from each line
    serialized = '\n'.join(line.lstrip() for line in serialized.splitlines())
    
    # Validate XML is well-formed before writing
    try:
        import xml.etree.ElementTree as ET
        ET.fromstring(serialized)
    except ET.ParseError as e:
        raise RuntimeError(f"Generated invalid XML: {e}")
    
    # Write to temp file first
    with tempfile.NamedTemporaryFile(
        'w',
        dir=str(collection_path.parent),
        prefix=f".{collection_path.name}.tmp-",
        delete=False,
        encoding='utf-8',
    ) as tmp:
        tmp.write(serialized)
        temp_path = Path(tmp.name)
    
    # Validate temp file is readable
    try:
        tree = ET.parse(str(temp_path))
        root = tree.getroot()
        if root.tag != 'NML':
            raise ValueError(f"Invalid root tag: {root.tag}")
    except Exception as e:
        temp_path.unlink()  # Clean up temp file
        raise RuntimeError(f"Generated invalid Traktor collection file: {e}")
    
    # Atomic replace
    temp_path.replace(collection_path)


def _apply_entry_update(
    entry: Entrytype,
    *,
    dir_attr: str,
    file_name: str,
    volume: str,
    volume_id: str,
    modified_date: str,
    modified_time: int,
) -> bool:
    if entry.location is None:
        entry.location = Locationtype(dir=dir_attr, file=file_name, volume=volume, volumeid=volume_id)
        entry.modified_date = modified_date
        entry.modified_time = modified_time
        return True
    location = entry.location
    changed = False
    if location.dir != dir_attr:
        location.dir = dir_attr
        changed = True
    if location.file != file_name:
        location.file = file_name
        changed = True
    if volume and location.volume != volume:
        location.volume = volume
        changed = True
    if location.volumeid != volume_id:
        location.volumeid = volume_id
        changed = True
    if changed:
        entry.modified_date = modified_date
        entry.modified_time = modified_time
    return changed


def _build_traktor_entry(
    track: dict[str, Any],
    *,
    file_path: Path,
    dir_attr: str,
    volume: str,
    volume_id: str,
    traktor_id: str,
    modified_date: str,
    modified_time: int,
) -> Entrytype:
    # Build INFO tag with metadata
    key_value = track.get('key') or track.get('key_camelot')
    info = Infotype(
        import_date=modified_date,
        comment=track.get('comment'),
        genre=track.get('genre'),
        key=key_value,  # Store key as text (12B, 5A, etc) in INFO.KEY
    )
    
    # Build TEMPO tag with BPM
    tempo = None
    bpm_value = track.get('bpm')
    if bpm_value is not None:
        try:
            bpm_float = float(bpm_value)
        except (TypeError, ValueError):
            bpm_float = None
        if bpm_float is not None:
            tempo = Tempotype(bpm=bpm_float, bpm_quality=100.0)
    
    # Note: We do NOT set musical_key.value_attribute (numeric ID)
    # Traktor will auto-generate it from info.key text when needed
    
    return Entrytype(
        location=Locationtype(dir=dir_attr, file=file_path.name, volume=volume, volumeid=volume_id),
        modification_info=ModificationInfotype(author_type='user'),
        info=info,
        tempo=tempo,
        musical_key=None,  # Let Traktor generate numeric ID from info.key
        modified_date=modified_date,
        modified_time=modified_time,
        audio_id=traktor_id,
        title=track.get('title', ''),
        artist=track.get('artist', ''),
    )


def add_tracks_to_traktor(
    collection_nml_path: Path,
    tracks: List[Dict[str, Any]],
    dry_run: bool = True,
    update_existing: bool = True
) -> Tuple[int, int]:
    """
    Add new tracks to Traktor collection.nml and update paths for existing tracks.

    ⚠️ DANGER ZONE: This MODIFIES Traktor collection.nml!

    Args:
        collection_nml_path: Path to collection.nml
        tracks: List of track dicts with keys: file_path, artist, title, bpm, key, traktor_id
        dry_run: If True (default), don't write changes. If False, apply.
        update_existing: If True, update paths for tracks already in collection

    Returns:
        Tuple[int, int]: (added_count, updated_count)
    """
    if not TRAKTOR_UTILS_AVAILABLE:  # pragma: no cover - safety guard
        raise RuntimeError(
            "traktor-nml-utils is required for Traktor sync. Install via 'pip install traktor-nml-utils'."
        )
    if not collection_nml_path.exists():
        raise FileNotFoundError(f"Traktor collection.nml not found: {collection_nml_path}")

    collection = TraktorCollection(collection_nml_path)
    collection_root = collection.nml.collection
    if collection_root is None:
        raise ValueError("Invalid Traktor collection.nml: no COLLECTION element found")

    now = datetime.now()
    modified_date = now.strftime("%Y/%m/%d")
    modified_time = now.hour * 3600 + now.minute * 60 + now.second

    existing_entries: Dict[str, Entrytype] = {
        entry.audio_id: entry
        for entry in collection_root.entry
        if entry.audio_id
    }

    added_count = 0
    updated_count = 0

    for track in tracks:
        file_path = Path(track['file_path']).expanduser()
        if not file_path.exists():
            continue

        traktor_id = track.get('traktor_id') or track.get('track_id')
        if not traktor_id:
            traktor_id = generate_track_id(file_path, track.get('artist', ''), track.get('title', ''))

        dir_attr = _format_traktor_dir(file_path.parent)
        volume, volume_id = _guess_traktor_volume(file_path)

        if traktor_id in existing_entries:
            if not update_existing:
                continue
            entry = existing_entries[traktor_id]
            if _apply_entry_update(
                entry,
                dir_attr=dir_attr,
                file_name=file_path.name,
                volume=volume,
                volume_id=volume_id,
                modified_date=modified_date,
                modified_time=modified_time,
            ):
                updated_count += 1
            continue

        entry = _build_traktor_entry(
            track,
            file_path=file_path,
            dir_attr=dir_attr,
            volume=volume,
            volume_id=volume_id,
            traktor_id=traktor_id,
            modified_date=modified_date,
            modified_time=modified_time,
        )
        collection_root.entry.append(entry)
        existing_entries[traktor_id] = entry
        added_count += 1

    collection_root.entries = len(collection_root.entry)

    if dry_run:
        if added_count:
            print(f"🔍 DRY-RUN: Would add {added_count} new tracks to Traktor collection")
        if updated_count:
            print(f"🔍 DRY-RUN: Would update {updated_count} existing track paths")
        return (added_count, updated_count)

    if added_count == 0 and updated_count == 0:
        print("ℹ️  No Traktor changes detected")
        return (0, 0)

    _backup_traktor_collection(collection_nml_path)
    
    try:
        # Use built-in save() method instead of manual serialization
        collection.save()
    except Exception as e:
        print(f"❌ Failed to save Traktor collection: {e}")
        print(f"   Collection backup preserved, no changes applied")
        return (0, 0)

    if added_count > 0:
        print(f"✅ Added {added_count} new tracks to Traktor collection")
    if updated_count > 0:
        print(f"🔄 Updated {updated_count} existing track paths in Traktor")

    return (added_count, updated_count)


def sync_rekordbox_paths(path_map_file: Path, write: bool = False) -> None:
    """
    Update Rekordbox database with new file paths.
    
    ⚠️ DANGER ZONE: This MODIFIES Rekordbox database!
    
    Args:
        path_map_file: Path to path map CSV
        write: If False (default), dry-run only. If True, apply changes.
    """
    raise NotImplementedError(
        "Phase 3 (sync_rekordbox_paths) not yet implemented.\n"
        "This feature requires careful testing and safety mechanisms.\n"
        "Use Phase 1 (import-rekordbox) and Phase 2 (create-path-map) for now."
    )


def sync_dj_libraries_after_export(
    library_csv_path: Path,
    collection_nml_path: Optional[Path] = None,
    dry_run: bool = True
) -> Dict[str, int]:
    """
    Sync DJ software libraries after exporting tracks to LIBRARY.
    
    This automatically:
    1. Reads library.csv (approved tracks)
    2. Checks which tracks are NEW (not in Rekordbox/Traktor)
    3. Adds new tracks to Rekordbox (via re-import) and Traktor
    
    ⚠️ MODIFIES: Traktor collection.nml (if write=True)
    📖 READ-ONLY: Rekordbox (detects new files via DB comparison)
    
    Args:
        library_csv_path: Path to library.csv (approved tracks)
        collection_nml_path: Path to Traktor collection.nml (auto-detect if None)
        dry_run: If True (default), don't write changes. If False, apply.
    
    Returns:
        Dict with counts: {'rekordbox_new': N, 'traktor_added': N}
    """
    from djlib.csvdb import load_records
    
    # Load library.csv (approved tracks)
    if not library_csv_path.exists():
        raise FileNotFoundError(f"Library CSV not found: {library_csv_path}")
    
    library_rows = load_records(library_csv_path)
    print(f"📋 Loaded {len(library_rows)} tracks from library.csv")
    
    # Get current Rekordbox and Traktor track IDs
    rekordbox_mapping = get_rekordbox_track_ids()  # {path: rb_id}
    traktor_mapping = get_traktor_track_ids(collection_nml_path)  # {path: tr_id}
    
    print(f"📖 Rekordbox: {len(rekordbox_mapping)} tracks")
    print(f"📖 Traktor: {len(traktor_mapping)} tracks")
    print()
    
    # Find NEW tracks (not in DJ software yet) vs tracks needing path UPDATES
    new_for_rekordbox = []
    new_for_traktor = []
    
    for row in library_rows:
        file_path_str = row.get('final_path', '') or row.get('file_path', '')
        if not file_path_str:
            continue
        
        file_path = Path(file_path_str)
        if not file_path.exists():
            print(f"   ⚠️  Skipping {file_path} - does not exist")
            continue
        
        track_id = row.get('track_id', '')
        original_path_str = row.get('original_path', '')
        original_path = Path(original_path_str) if original_path_str else None
        
        print(f"\n   Checking: {file_path.name}")
        print(f"      original_path: {original_path}")
        print(f"      final_path: {file_path}")
        
        # Always read DJ software IDs from audio file tags (source of truth)
        rekordbox_id = ''
        traktor_id = ''
        try:
            djlib_tags = read_djlib_tags(file_path)
            rekordbox_id = djlib_tags.get('rekordbox_id', '')
            traktor_id = djlib_tags.get('traktor_id', '')
        except Exception:
            pass
        
        # Check if track exists in Rekordbox
        # All tracks in library.csv should be synced (tags may have changed even if path didn't)
        in_rekordbox = False
        found_rekordbox_id = rekordbox_id
        
        # Strategy 1: Check by original path (where file was before export)
        if original_path and original_path in rekordbox_mapping:
            in_rekordbox = True
            found_rekordbox_id = rekordbox_mapping[original_path]
        # Strategy 2: Check by rekordbox_id from tags
        elif rekordbox_id:
            for path, rb_id in rekordbox_mapping.items():
                if rb_id == rekordbox_id:
                    in_rekordbox = True
                    found_rekordbox_id = rekordbox_id
                    break
        # Strategy 3: Check by current path
        elif file_path in rekordbox_mapping:
            # File already at this path in DB
            in_rekordbox = True
            found_rekordbox_id = rekordbox_mapping[file_path]
        
        # Add to sync list ONCE (either existing or new)
        new_for_rekordbox.append({
            'file_path': file_path,
            'track_id': track_id,
            'rekordbox_id': found_rekordbox_id if found_rekordbox_id else '',
        })
        
        # Check if track exists in Traktor
        # All tracks in library.csv should be synced (tags may have changed even if path didn't)
        in_traktor = False
        found_traktor_id = traktor_id if traktor_id else track_id
        
        # Strategy 1: Check by original path (where file was before export)
        if original_path and original_path in traktor_mapping:
            in_traktor = True
            found_traktor_id = traktor_mapping[original_path]
        # Strategy 2: Check by traktor_id from tags
        elif traktor_id:
            for path, tr_id in traktor_mapping.items():
                if tr_id == traktor_id:
                    in_traktor = True
                    found_traktor_id = traktor_id
                    break
        # Strategy 3: Check by current path
        elif file_path in traktor_mapping:
            # File already at this path in DB
            in_traktor = True
            found_traktor_id = traktor_mapping[file_path]
        
        # Add to sync list ONCE (either existing or new)
        new_for_traktor.append({
            'file_path': file_path,
            'track_id': track_id,
            'traktor_id': found_traktor_id if found_traktor_id else track_id,
            'artist': row.get('artist', ''),
            'title': row.get('title', ''),
            'bpm': row.get('bpm', ''),
            'key': row.get('key_camelot', ''),
        })
    
    print(f"🆕 Tracks to add/update in Rekordbox: {len(new_for_rekordbox)}")
    print(f"🆕 Tracks to add/update in Traktor: {len(new_for_traktor)}")
    print()
    
    # Add to Rekordbox (programmatically using pyrekordbox)
    rekordbox_added = 0
    rekordbox_updated = 0
    if new_for_rekordbox:
        print(f"📝 Syncing {len(new_for_rekordbox)} tracks to Rekordbox...")
        try:
            rekordbox_added, rekordbox_updated = add_tracks_to_rekordbox(
                new_for_rekordbox,
                dry_run=dry_run
            )
        except Exception as e:
            print(f"⚠️  Rekordbox sync failed: {e}")
            print("   Falling back to manual import instructions...")
            if not dry_run:
                print("\n📖 REKORDBOX: Manual Action Required")
                print("   1. Open Rekordbox")
                print("   2. Go to File → Import → Import Folder")
                print("   3. Select your LIBRARY folder")
    
    # Add to Traktor (programmatically using XML manipulation)
    traktor_added = 0
    traktor_updated = 0
    if new_for_traktor:
        if collection_nml_path is None:
            # Auto-detect Traktor collection.nml
            docs = Path.home() / "Documents" / "Native Instruments"
            if docs.exists():
                for traktor_dir in docs.glob("Traktor*"):
                    nml = traktor_dir / "collection.nml"
                    if nml.exists():
                        collection_nml_path = nml
                        break
        
        if collection_nml_path and collection_nml_path.exists():
            print(f"📝 Syncing {len(new_for_traktor)} tracks to Traktor...")
            traktor_added, traktor_updated = add_tracks_to_traktor(
                collection_nml_path,
                new_for_traktor,
                dry_run=dry_run
            )
        else:
            print("⚠️  Traktor collection.nml not found - skipping Traktor sync")
    
    return {
        'rekordbox_added': rekordbox_added,
        'rekordbox_updated': rekordbox_updated,
        'traktor_added': traktor_added,
        'traktor_updated': traktor_updated,
    }


def sync_traktor_paths(collection_nml_path: Path, 
                       path_map_file: Path, 
                       write: bool = False) -> None:
    """
    Update Traktor collection.nml with new file paths.
    
    ⚠️ DANGER ZONE: This MODIFIES Traktor collection.nml!
    
    Args:
        collection_nml_path: Path to collection.nml
        path_map_file: Path to path map CSV
        write: If False (default), dry-run only. If True, apply changes.
    """
    raise NotImplementedError(
        "Phase 3 (sync_traktor_paths) not yet implemented.\n"
        "This feature requires careful testing and safety mechanisms.\n"
        "Use Phase 1 (import-traktor) and Phase 2 (create-path-map) for now."
    )
