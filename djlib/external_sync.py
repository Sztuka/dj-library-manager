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
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from pyrekordbox import Rekordbox6Database

try:
    from pyrekordbox import Rekordbox6Database
    PYREKORDBOX_AVAILABLE = True
except ImportError:
    Rekordbox6Database = None  # type: ignore[assignment,misc]
    PYREKORDBOX_AVAILABLE = False

from djlib.djlib_tags import (
    generate_track_id,
    write_djlib_tags,
    read_djlib_tags,
    has_djlib_tags,
)


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
        title = getattr(content, 'TrackTitle', '')
        
        # Generate our internal track_id
        track_id = generate_track_id(file_path, artist, title)
        
        # Extract metadata
        track_data = {
            'external_source': 'rekordbox',
            'external_track_id': rekordbox_id,
            'track_id': track_id,  # OUR internal ID
            'old_full_path': full_path,
            'artist': artist,
            'title': title,
            'bpm': str(getattr(content, 'Tempo', '')),
            'key': getattr(content, 'KeyText', ''),
            'rating': str(getattr(content, 'Rating', '')),
            'color': str(getattr(content, 'ColorID', '')),
            'date_added': str(getattr(content, 'DateCreated', '')),
            'last_played': str(getattr(content, 'LastPlayed', '')),
            'play_count': str(getattr(content, 'PlayCount', '')),
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
    
    # Parse XML
    tree = ET.parse(collection_nml_path)
    root = tree.getroot()
    
    tracks = []
    snapshot_date = datetime.now(timezone.utc).isoformat()
    tracks_to_tag: List[Dict[str, Any]] = []
    
    # Find all ENTRY elements
    for entry in root.findall(".//ENTRY"):
        # Extract location
        location = entry.find("LOCATION")
        if location is None:
            continue
        
        dir_path = location.get("DIR", "")
        file_name = location.get("FILE", "")
        
        if not dir_path or not file_name:
            continue
        
        # Reconstruct full path
        # Traktor uses :/ as path separators, convert to /
        full_path_raw = str(Path(dir_path) / file_name)
        # Normalize Traktor path format: /:Users/:path → /Users/path
        full_path = full_path_raw.replace('/:', '/').replace(':/', '/')
        # Remove doubled slashes (Traktor sometimes adds extra /)
        while '//' in full_path:
            full_path = full_path.replace('//', '/')
        
        # Extract metadata from INFO tag
        info = entry.find("INFO")
        if info is not None:
            artist = info.get("ARTIST", "")
            title = info.get("TITLE", "")
        else:
            artist = ""
            title = ""
        
        # Extract tempo (BPM)
        tempo = entry.find("TEMPO")
        if tempo is not None:
            bpm = tempo.get("BPM", "")
        else:
            bpm = ""
        
        # Extract musical key
        musical_key = entry.find("MUSICAL_KEY")
        if musical_key is not None:
            key = musical_key.get("VALUE", "")
        else:
            key = ""
        
        # Count cue points (indicates track usage)
        cue_points = entry.findall(".//CUE_V2")
        cue_count = len(cue_points)
        
        # Get track ID - Traktor uses AUDIO_ID (not PRIMARYKEY)
        traktor_id = entry.get("AUDIO_ID", "") or entry.get("PRIMARYKEY", "")
        
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
            'key': key,
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
                    # Update existing track path
                    try:
                        db.update_content_path(int(rekordbox_id), str(file_path))
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
    if not collection_nml_path.exists():
        raise FileNotFoundError(f"Traktor collection.nml not found: {collection_nml_path}")
    
    # Backup original file
    if not dry_run:
        backup_path = collection_nml_path.with_suffix('.nml.backup')
        shutil.copy2(collection_nml_path, backup_path)
        print(f"📦 Backup created: {backup_path}")
    
    # Parse XML
    tree = ET.parse(collection_nml_path)
    root = tree.getroot()
    
    # Find COLLECTION element
    collection = root.find(".//COLLECTION")
    if collection is None:
        raise ValueError("Invalid Traktor collection.nml: no COLLECTION element found")
    
    # Build mapping of existing entries by AUDIO_ID
    existing_entries: Dict[str, ET.Element] = {}
    for entry in collection.findall("ENTRY"):
        audio_id = entry.get("AUDIO_ID", "")
        if audio_id:
            existing_entries[audio_id] = entry
    
    # Add new tracks and update existing
    added_count = 0
    updated_count = 0
    
    for track in tracks:
        file_path = Path(track['file_path'])
        traktor_id = track.get('traktor_id', '')
        
        # Skip if file doesn't exist
        if not file_path.exists():
            continue
        
        # Convert path to Traktor format (/:Users/:path)
        full_path = str(file_path.resolve())
        dir_path = str(file_path.parent).replace('/', '/:') + '/:'
        file_name = file_path.name
        
        # Check if track already exists
        if traktor_id and traktor_id in existing_entries:
            if update_existing:
                # Update existing entry's path
                entry = existing_entries[traktor_id]
                location = entry.find("LOCATION")
                if location is not None:
                    old_dir = location.get("DIR", "")
                    old_file = location.get("FILE", "")
                    if old_dir != dir_path or old_file != file_name:
                        location.set("DIR", dir_path)
                        location.set("FILE", file_name)
                        entry.set("MODIFIED_DATE", datetime.now().strftime("%Y/%m/%d"))
                        updated_count += 1
            continue  # Skip adding (already exists)
        
        # Create new ENTRY element
        entry = ET.Element("ENTRY")
        entry.set("MODIFIED_DATE", datetime.now().strftime("%Y/%m/%d"))
        entry.set("MODIFIED_TIME", "0")  # Traktor uses seconds since midnight
        entry.set("AUDIO_ID", traktor_id if traktor_id else generate_track_id(file_path, track.get('artist', ''), track.get('title', '')))
        entry.set("TITLE", track.get('title', ''))
        entry.set("ARTIST", track.get('artist', ''))
        
        # Add LOCATION sub-element
        location = ET.SubElement(entry, "LOCATION")
        location.set("DIR", dir_path)
        location.set("FILE", file_name)
        location.set("VOLUME", "Macintosh HD")  # macOS default
        location.set("VOLUMEID", "")
        
        # Add ALBUM, MODIFICATION_INFO, INFO, TEMPO, LOUDNESS, MUSICAL_KEY, etc.
        album = ET.SubElement(entry, "ALBUM")
        album.set("TITLE", "")
        
        modification_info = ET.SubElement(entry, "MODIFICATION_INFO")
        modification_info.set("AUTHOR_TYPE", "user")
        
        info = ET.SubElement(entry, "INFO")
        info.set("BITRATE", "320000")  # Default
        info.set("GENRE", "")
        info.set("COMMENT", "")
        info.set("PLAYTIME", "300")  # Default 5 minutes
        info.set("PLAYTIME_FLOAT", "300.000")
        info.set("IMPORT_DATE", datetime.now().strftime("%Y/%m/%d"))
        info.set("RELEASE_DATE", "")
        info.set("FLAGS", "12")
        
        # BPM
        if track.get('bpm'):
            tempo = ET.SubElement(entry, "TEMPO")
            tempo.set("BPM", str(track['bpm']))
            tempo.set("BPM_QUALITY", "100")  # 100 = user-confirmed
        
        # Key
        if track.get('key'):
            musical_key = ET.SubElement(entry, "MUSICAL_KEY")
            musical_key.set("VALUE", str(track['key']))
        
        # Add to collection
        collection.append(entry)
        added_count += 1
    
    # Write changes
    if not dry_run and (added_count > 0 or updated_count > 0):
        tree.write(collection_nml_path, encoding='utf-8', xml_declaration=True)
        if added_count > 0:
            print(f"✅ Added {added_count} new tracks to Traktor collection")
        if updated_count > 0:
            print(f"🔄 Updated {updated_count} existing track paths in Traktor")
    else:
        if added_count > 0:
            print(f"🔍 DRY-RUN: Would add {added_count} new tracks to Traktor collection")
        if updated_count > 0:
            print(f"🔍 DRY-RUN: Would update {updated_count} existing track paths")
    
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
