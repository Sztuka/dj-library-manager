"""
MusicBrainz Canonical Data lookup module.

Uses local SQLite database built from MusicBrainz Canonical Data dumps.
Provides fast indexed lookups for canonical recording + release pairs.
"""
from __future__ import annotations
from typing import Optional, Tuple
from pathlib import Path
import sqlite3
import csv
import tarfile
import logging
import re

logger = logging.getLogger(__name__)


class CanonicalMBDatabase:
    """
    Interface to MusicBrainz Canonical Data SQLite database.
    
    The canonical data contains curated recording→release mappings,
    focusing on first/original studio releases and filtering out
    live albums, bootlegs, and compilations.
    """
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
    
    def __enter__(self):
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def connect(self):
        """Open connection to SQLite database."""
        if self.conn is None:
            self.conn = sqlite3.connect(str(self.db_path))
            self.conn.row_factory = sqlite3.Row
    
    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
    
    def lookup(self, artist: str, track: str) -> Optional[Tuple[str, str, str, str]]:
        """
        Look up canonical release for artist + track.
        
        Args:
            artist: Artist name
            track: Track/recording name
        
        Returns:
            Tuple of (recording_mbid, release_mbid, release_name, artist_credit_name)
            or None if not found
        """
        if not self.conn:
            self.connect()
        
        # Normalize for lookup (remove special chars, lowercase)
        lookup_key = self._normalize_lookup(artist, track)
        
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT recording_mbid, release_mbid, release_name, artist_credit_name
            FROM canonical_recordings
            WHERE combined_lookup = ?
            LIMIT 1
        """, (lookup_key,))
        
        row = cursor.fetchone()
        if row:
            return (row['recording_mbid'], row['release_mbid'], 
                   row['release_name'], row['artist_credit_name'])
        return None
    
    def fuzzy_lookup(self, artist: str, track: str, threshold: int = 85) -> Optional[Tuple[str, str, str, str]]:
        """
        Fuzzy lookup using LIKE pattern matching.
        
        Args:
            artist: Artist name
            track: Track/recording name
            threshold: Minimum similarity score (not used in basic LIKE, for future)
        
        Returns:
            Best match tuple or None
        """
        if not self.conn:
            self.connect()
        
        lookup_key = self._normalize_lookup(artist, track)
        
        # Try exact match first
        result = self.lookup(artist, track)
        if result:
            return result
        
        # Fuzzy with LIKE
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT recording_mbid, release_mbid, release_name, artist_credit_name
            FROM canonical_recordings
            WHERE combined_lookup LIKE ?
            LIMIT 1
        """, (f"%{lookup_key}%",))
        
        row = cursor.fetchone()
        if row:
            return (row['recording_mbid'], row['release_mbid'], 
                   row['release_name'], row['artist_credit_name'])
        return None
    
    @staticmethod
    def _normalize_lookup(artist: str, track: str) -> str:
        """
        Normalize artist+track for combined_lookup matching.
        
        Removes special characters, converts to lowercase, joins without spaces.
        Matches the format in canonical CSV.
        """
        text = f"{artist} {track}".lower()
        # Remove everything except alphanumeric and spaces
        text = re.sub(r'[^a-z0-9\s]', '', text)
        # Remove extra spaces and join
        text = ''.join(text.split())
        return text


def import_canonical_dump(tar_path: Path, db_path: Path, csv_name: str = "canonical_musicbrainz_data.csv"):
    """
    Import MusicBrainz Canonical Data dump into SQLite database.
    
    Args:
        tar_path: Path to .tar or .tar.zst dump file (will try to decompress .zst first)
        db_path: Output SQLite database path
        csv_name: Name of CSV file inside tar archive
    
    Raises:
        FileNotFoundError: If tar_path doesn't exist
        ValueError: If CSV not found in archive
    """
    if not tar_path.exists():
        raise FileNotFoundError(f"Dump file not found: {tar_path}")
    
    # If .zst file, decompress first
    if tar_path.suffix == ".zst":
        logger.info(f"Decompressing {tar_path.name} with zstd...")
        import subprocess
        decompressed = tar_path.with_suffix("")  # Remove .zst extension
        
        if not decompressed.exists():
            subprocess.run(["zstd", "-d", str(tar_path)], check=True)
        
        tar_path = decompressed
        logger.info(f"Using decompressed: {tar_path.name}")
    
    logger.info(f"Importing canonical data from {tar_path}")
    logger.info("This may take several minutes...")
    print(f"📊 Opening tar archive: {tar_path.name}")
    print(f"   Size: {tar_path.stat().st_size / (1024**3):.1f} GB")
    
    # Create/recreate database
    if db_path.exists():
        db_path.unlink()
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    # Create table
    cursor.execute("""
        CREATE TABLE canonical_recordings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_credit_name TEXT NOT NULL,
            release_name TEXT NOT NULL,
            release_mbid TEXT NOT NULL,
            recording_name TEXT NOT NULL,
            recording_mbid TEXT NOT NULL,
            combined_lookup TEXT NOT NULL
        )
    """)
    
    # Create index on combined_lookup for fast queries
    cursor.execute("""
        CREATE INDEX idx_combined_lookup ON canonical_recordings(combined_lookup)
    """)
    
    # Open tar and find CSV
    logger.info("Opening archive...")
    with tarfile.open(tar_path, 'r:*') as tar:
        # Find CSV file in archive
        csv_member = None
        for member in tar.getmembers():
            if member.name.endswith('.csv') or csv_name in member.name:
                csv_member = member
                break
        
        if not csv_member:
            raise ValueError(f"CSV file '{csv_name}' not found in archive")
        
        logger.info(f"Found CSV: {csv_member.name}")
        logger.info("Importing rows...")
        
        # Extract and read CSV
        csv_file = tar.extractfile(csv_member)
        if not csv_file:
            raise ValueError(f"Could not extract {csv_member.name}")
        
        # Read CSV and insert into database
        # Assuming CSV has header: artist_credit_name,release_name,release_mbid,recording_name,recording_mbid,combined_lookup
        csv_text = csv_file.read().decode('utf-8')
        reader = csv.DictReader(csv_text.splitlines())
        
        batch = []
        batch_size = 10000
        total = 0
        skipped = 0
        
        for row in reader:
            # Validate required fields (handle None values)
            artist = (row.get('artist_credit_name') or '').strip()
            release = (row.get('release_name') or '').strip()
            release_mbid = (row.get('release_mbid') or '').strip()
            recording = (row.get('recording_name') or '').strip()
            recording_mbid = (row.get('recording_mbid') or '').strip()
            lookup = (row.get('combined_lookup') or '').strip()
            
            # Skip rows with missing essential fields
            if not all([artist, release, release_mbid, recording, recording_mbid, lookup]):
                skipped += 1
                continue
            
            batch.append((artist, release, release_mbid, recording, recording_mbid, lookup))
            
            if len(batch) >= batch_size:
                cursor.executemany("""
                    INSERT INTO canonical_recordings 
                    (artist_credit_name, release_name, release_mbid, recording_name, recording_mbid, combined_lookup)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, batch)
                conn.commit()
                total += len(batch)
                print(f"✓ Imported {total:,} rows...")
                batch = []
        
        # Insert remaining rows
        if batch:
            cursor.executemany("""
                INSERT INTO canonical_recordings 
                (artist_credit_name, release_name, release_mbid, recording_name, recording_mbid, combined_lookup)
                VALUES (?, ?, ?, ?, ?, ?)
            """, batch)
            conn.commit()
            total += len(batch)
    
    conn.close()
    
    print(f"\n✅ Import complete!")
    print(f"   Total rows: {total:,}")
    if skipped > 0:
        print(f"   Skipped: {skipped:,} rows with missing fields")
    print(f"   Database: {db_path}")
    print(f"   Size: {db_path.stat().st_size / (1024**3):.2f} GB")
    
    logger.info(f"Import complete! Total rows: {total}")
    if skipped > 0:
        logger.info(f"Skipped {skipped} rows with missing fields")
    logger.info(f"Database: {db_path} ({db_path.stat().st_size / (1024**3):.2f} GB)")


def get_canonical_db_path() -> Path:
    """Get path to canonical MusicBrainz SQLite database."""
    # Database in project data/ folder
    return Path(__file__).parent.parent.parent / "data" / "musicbrainz_canonical.db"


def ensure_canonical_database() -> bool:
    """
    Ensure canonical database exists. If not, try to build it from dump.
    
    Returns:
        True if database is ready, False if dump not found
    """
    db_path = get_canonical_db_path()
    
    if db_path.exists():
        return True
    
    # Look for dump file
    data_dir = db_path.parent
    dump_files = list(data_dir.glob("musicbrainz-canonical-dump-*.tar.zst"))
    
    if not dump_files:
        logger.warning("No canonical dump found. Download from:")
        logger.warning("https://data.metabrainz.org/pub/musicbrainz/canonical_data/")
        return False
    
    # Use most recent dump
    dump_file = sorted(dump_files)[-1]
    logger.info(f"Building database from {dump_file.name}")
    
    try:
        import_canonical_dump(dump_file, db_path)
        return True
    except Exception as e:
        logger.error(f"Failed to import dump: {e}")
        return False


def lookup_canonical_release(artist: str, track: str, fetch_year: bool = True) -> Optional[dict]:
    """
    Look up canonical release for artist + track.
    
    Convenience function that handles database connection automatically.
    Optionally fetches release year from MusicBrainz API.
    
    Args:
        artist: Artist name
        track: Track/recording name
        fetch_year: Whether to fetch release year from MB API (default: True)
    
    Returns:
        Dict with keys: recording_mbid, release_mbid, album_title, artist_name, release_year (if fetch_year=True)
        or None if not found or database not available
    """
    if not ensure_canonical_database():
        return None
    
    db_path = get_canonical_db_path()
    
    try:
        with CanonicalMBDatabase(db_path) as db:
            result = db.lookup(artist, track)
            if result:
                recording_mbid, release_mbid, release_name, artist_name = result
                data = {
                    'recording_mbid': recording_mbid,
                    'release_mbid': release_mbid,
                    'album_title': release_name,
                    'artist_name': artist_name
                }
                
                # Fetch release year from MusicBrainz API if requested
                if fetch_year and release_mbid:
                    try:
                        from djlib.metadata.mb_client import get_release_year
                        year = get_release_year(release_mbid)
                        if year:
                            data['release_year'] = year
                    except Exception as e:
                        logger.debug(f"Could not fetch year for release {release_mbid}: {e}")
                
                return data
    except Exception as e:
        logger.error(f"Canonical lookup failed: {e}")
    
    return None
