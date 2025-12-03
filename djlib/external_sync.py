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
            
            if not dir_path or not file_name:
                continue
            
            # Reconstruct full path (normalize Traktor format)
            full_path_raw = str(Path(dir_path) / file_name)
            full_path = full_path_raw.replace('/:', '/').replace(':/', '/')
            while '//' in full_path:
                full_path = full_path.replace('//', '/')
            
            file_path = Path(full_path)
            traktor_id = entry.get("AUDIO_ID", "") or entry.get("PRIMARYKEY", "")
            
            if traktor_id:
                mapping[file_path] = traktor_id
        
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
        
        # Get track ID - Traktor uses AUDIO_ID
        traktor_id = entry.audio_id or ""
        
        # Generate our internal track_id
        file_path_obj = Path(full_path)
        internal_track_id = generate_track_id(file_path_obj, artist, title)
        
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
                        continue

                    try:
                        content = db.get_content(ID=rb_id_int)
                        if content is None:
                            raise RuntimeError(f"Rekordbox ID {rb_id_int} not found")

                        # update_content_path also refreshes ANLZ metadata; delay commit until we touch FileNameL
                        db.update_content_path(content, str(file_path), commit=False)

                        file_name_only = file_path.name
                        if getattr(content, "FileNameL", None) != file_name_only:
                            content.FileNameL = file_name_only

                        updated_count += 1
                    except Exception as e:
                        print(f"⚠️  Failed to update {file_path.name}: {e}")
            else:
                # Add new track
                try:
                    # add_content() automatically analyzes and imports the file
                    content = db.add_content(str(file_path))
                    if content:
                        added_count += 1
                except Exception as e:
                    print(f"⚠️  Failed to add {file_path.name}: {e}")
        
        # Commit changes
        db.commit()
        
        if added_count > 0:
            print(f"✅ Added {added_count} new tracks to Rekordbox")
        if updated_count > 0:
            print(f"🔄 Updated {updated_count} existing track paths in Rekordbox")
        
    finally:
        db.close()
    
    return (added_count, updated_count)



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
    serializer = XmlSerializer()
    serialized = serializer.render(nml_obj)
    serialized = '\n'.join(line.lstrip() for line in serialized.splitlines())
    with tempfile.NamedTemporaryFile(
        'w',
        dir=str(collection_path.parent),
        prefix=f".{collection_path.name}.tmp-",
        delete=False,
        encoding='utf-8',
    ) as tmp:
        tmp.write(serialized)
        temp_path = Path(tmp.name)
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
    _safe_save_traktor_collection(collection_nml_path, collection.nml)

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
    rekordbox_mapping = get_rekordbox_track_ids()
    traktor_mapping = get_traktor_track_ids(collection_nml_path)
    
    print(f"📖 Rekordbox: {len(rekordbox_mapping)} tracks")
    print(f"📖 Traktor: {len(traktor_mapping)} tracks")
    print()
    
    # Find NEW tracks (not in DJ software yet)
    new_for_rekordbox = []
    new_for_traktor = []
    
    for row in library_rows:
        file_path_str = row.get('final_path', '') or row.get('file_path', '')
        if not file_path_str:
            continue
        
        file_path = Path(file_path_str)
        if not file_path.exists():
            continue
        
        track_id = row.get('track_id', '')
        rekordbox_id = row.get('rekordbox_id', '')
        traktor_id = row.get('traktor_id', '')
        
        # Check if in Rekordbox
        if file_path not in rekordbox_mapping:
            new_for_rekordbox.append({
                'file_path': file_path,
                'track_id': track_id,
                'rekordbox_id': rekordbox_id,
            })
        
        # Check if in Traktor
        if file_path not in traktor_mapping:
            new_for_traktor.append({
                'file_path': file_path,
                'track_id': track_id,
                'traktor_id': traktor_id if traktor_id else track_id,
                'artist': row.get('artist', ''),
                'title': row.get('title', ''),
                'bpm': row.get('bpm', ''),
                'key': row.get('key_camelot', ''),
            })
    
    print(f"🆕 NEW tracks not in Rekordbox: {len(new_for_rekordbox)}")
    print(f"🆕 NEW tracks not in Traktor: {len(new_for_traktor)}")
    print()
    
    # Add to Rekordbox (programmatically using pyrekordbox)
    rekordbox_added = 0
    rekordbox_updated = 0
    if new_for_rekordbox:
        print(f"📝 Adding {len(new_for_rekordbox)} tracks to Rekordbox...")
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
            print(f"📝 Adding {len(new_for_traktor)} tracks to Traktor...")
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
