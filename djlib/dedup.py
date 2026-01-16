"""
Library deduplication module.

Finds and handles duplicate tracks based on artist+title matching.
Considers audio quality, duration, and metadata when deciding which to keep.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass
import mutagen
from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.aiff import AIFF
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis
from mutagen.oggopus import OggOpus


@dataclass
class AudioInfo:
    """Audio file information for deduplication."""
    path: Path
    artist: str
    title: str
    format: str  # 'flac', 'mp3', 'aiff', 'wav', 'm4a', etc.
    bitrate: int  # kbps (0 for lossless)
    duration: float  # seconds
    lossless: bool
    # Metadata
    playcount: int
    rating: int  # 0-255 (Traktor style)
    tags: List[str]  # genre, mood, etc.
    
    @property
    def quality_score(self) -> int:
        """
        Calculate quality score for comparison.
        Higher is better.
        
        Scoring:
        - Lossless (FLAC/WAV/AIFF): 1000 + bitrate/10
        - Lossy 320+ kbps: 500 + bitrate
        - Lossy 256 kbps: 400 + bitrate
        - Lossy <256 kbps: bitrate
        """
        if self.lossless:
            return 1000 + (self.bitrate // 10)
        elif self.bitrate >= 320:
            return 500 + self.bitrate
        elif self.bitrate >= 256:
            return 400 + self.bitrate
        else:
            return self.bitrate
    
    @property
    def match_key(self) -> str:
        """Normalized key for matching duplicates."""
        return normalize_for_match(self.artist, self.title)


def normalize_for_match(artist: str, title: str) -> str:
    """
    Normalize artist+title for duplicate matching.
    
    Simple normalization:
    - lowercase
    - normalize whitespace
    - strip leading/trailing whitespace
    
    Does NOT strip remix/edit info - those are different tracks!
    """
    combined = f"{artist} - {title}".lower().strip()
    return " ".join(combined.split())


def get_audio_info(path: Path) -> Optional[AudioInfo]:
    """
    Extract audio information from file for deduplication.
    
    Returns None if file cannot be read.
    """
    try:
        audio = mutagen.File(path)
        if audio is None:
            return None
        
        # Determine format and lossless status
        ext = path.suffix.lower()
        format_name = ext.lstrip('.')
        lossless = ext in ('.flac', '.wav', '.aiff', '.aif')
        
        # Extract bitrate and duration
        bitrate = 0
        duration = 0.0
        
        if hasattr(audio, 'info'):
            info = audio.info
            duration = getattr(info, 'length', 0.0) or 0.0
            
            if hasattr(info, 'bitrate'):
                bitrate = int(info.bitrate / 1000) if info.bitrate > 1000 else int(info.bitrate)
            elif hasattr(info, 'bits_per_sample') and hasattr(info, 'sample_rate'):
                # For lossless, calculate bitrate
                channels = getattr(info, 'channels', 2)
                bitrate = int(info.bits_per_sample * info.sample_rate * channels / 1000)
        
        # Extract metadata (artist, title)
        artist = ""
        title = ""
        
        if isinstance(audio, MP3):
            from mutagen.id3 import ID3
            tags = audio.tags
            if tags:
                artist = str(tags.get('TPE1', [''])[0]) if tags.get('TPE1') else ''
                title = str(tags.get('TIT2', [''])[0]) if tags.get('TIT2') else ''
        elif isinstance(audio, FLAC):
            artist = audio.get('artist', [''])[0] if audio.get('artist') else ''
            title = audio.get('title', [''])[0] if audio.get('title') else ''
        elif isinstance(audio, AIFF):
            tags = audio.tags
            if tags:
                artist = str(tags.get('TPE1', [''])[0]) if tags.get('TPE1') else ''
                title = str(tags.get('TIT2', [''])[0]) if tags.get('TIT2') else ''
        elif isinstance(audio, MP4):
            artist = audio.get('\xa9ART', [''])[0] if audio.get('\xa9ART') else ''
            title = audio.get('\xa9nam', [''])[0] if audio.get('\xa9nam') else ''
        elif isinstance(audio, (OggVorbis, OggOpus)):
            artist = audio.get('artist', [''])[0] if audio.get('artist') else ''
            title = audio.get('title', [''])[0] if audio.get('title') else ''
        else:
            # Generic fallback
            artist = str(audio.get('artist', [''])[0]) if audio.get('artist') else ''
            title = str(audio.get('title', [''])[0]) if audio.get('title') else ''
        
        # Extract DJ-specific metadata (playcount, rating, custom tags)
        playcount = 0
        rating = 0
        tags_list: List[str] = []
        
        # Try to read from DJLIB tags or other common tags
        # This is simplified - expand based on your tag structure
        if isinstance(audio, (MP3, AIFF)):
            id3_tags = audio.tags
            if id3_tags:
                # POPM for playcount/rating
                for key in id3_tags:
                    if key.startswith('POPM'):
                        popm = id3_tags[key]
                        playcount = getattr(popm, 'count', 0) or 0
                        rating = getattr(popm, 'rating', 0) or 0
                        break
                # Genre as tag
                genre = id3_tags.get('TCON')
                if genre:
                    tags_list.append(str(genre))
        elif isinstance(audio, FLAC):
            genre = audio.get('genre', [])
            if genre:
                tags_list.extend(genre)
        
        return AudioInfo(
            path=path,
            artist=artist,
            title=title,
            format=format_name,
            bitrate=bitrate,
            duration=duration,
            lossless=lossless,
            playcount=playcount,
            rating=rating,
            tags=tags_list,
        )
        
    except Exception as e:
        print(f"⚠️  Cannot read {path.name}: {e}")
        return None


def find_duplicates_in_library(library_path: Path) -> Dict[str, List[AudioInfo]]:
    """
    Find duplicate tracks in library based on artist+title.
    
    Returns:
        Dict mapping match_key to list of AudioInfo (only keys with 2+ files)
    """
    from djlib.config import load_config
    
    # Collect all audio files
    audio_extensions = {'.mp3', '.flac', '.wav', '.aiff', '.aif', '.m4a', '.ogg', '.opus'}
    
    all_tracks: Dict[str, List[AudioInfo]] = {}
    
    for ext in audio_extensions:
        for path in library_path.rglob(f"*{ext}"):
            info = get_audio_info(path)
            if info and info.artist and info.title:
                key = info.match_key
                if key not in all_tracks:
                    all_tracks[key] = []
                all_tracks[key].append(info)
    
    # Filter to only duplicates (2+ files)
    duplicates = {k: v for k, v in all_tracks.items() if len(v) >= 2}
    
    return duplicates


def find_duplicates_against_library(
    source_files: List[Path],
    library_path: Path,
) -> List[Tuple[AudioInfo, AudioInfo]]:
    """
    Find files from source that already exist in library.
    
    Returns:
        List of tuples: (source_info, library_info)
    """
    # Build library index
    library_index: Dict[str, AudioInfo] = {}
    audio_extensions = {'.mp3', '.flac', '.wav', '.aiff', '.aif', '.m4a', '.ogg', '.opus'}
    
    for ext in audio_extensions:
        for path in library_path.rglob(f"*{ext}"):
            info = get_audio_info(path)
            if info and info.artist and info.title:
                library_index[info.match_key] = info
    
    # Check source files against library
    duplicates = []
    for src_path in source_files:
        src_info = get_audio_info(src_path)
        if src_info and src_info.artist and src_info.title:
            key = src_info.match_key
            if key in library_index:
                duplicates.append((src_info, library_index[key]))
    
    return duplicates


def format_quality(info: AudioInfo) -> str:
    """Format quality info for display."""
    if info.lossless:
        return f"{info.format.upper()} {info.bitrate}kbps (lossless)"
    else:
        return f"{info.format.upper()} {info.bitrate}kbps"


def format_duration(seconds: float) -> str:
    """Format duration as M:SS."""
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins}:{secs:02d}"


def merge_metadata(keep: AudioInfo, discard: AudioInfo) -> Dict[str, Any]:
    """
    Determine merged metadata from two tracks.
    
    Strategy:
    - playcount: sum
    - rating: max
    - tags: union
    
    Returns dict of metadata to apply to 'keep' track.
    """
    return {
        'playcount': keep.playcount + discard.playcount,
        'rating': max(keep.rating, discard.rating),
        'tags': list(set(keep.tags) | set(discard.tags)),
    }


def interactive_dedup(
    duplicates: Dict[str, List[AudioInfo]],
    dry_run: bool = True,
) -> Dict[str, int]:
    """
    Interactively handle duplicates.
    
    For each duplicate group, shows options:
    - Keep higher quality (with optional metadata merge)
    - Keep specific version
    - Skip (keep all)
    
    Returns:
        Dict with counts: {'kept': N, 'removed': N, 'skipped': N}
    """
    import shutil
    from djlib.config import load_config
    
    cfg = load_config()
    reject_path = Path(cfg.get('REJECT_ROOT', '')).expanduser()
    
    stats = {'kept': 0, 'removed': 0, 'skipped': 0}
    
    if not duplicates:
        print("ℹ️  No duplicates found")
        return stats
    
    print(f"\n🔍 Found {len(duplicates)} duplicate groups:\n")
    
    for i, (key, tracks) in enumerate(duplicates.items(), 1):
        # Sort by quality score (best first)
        tracks_sorted = sorted(tracks, key=lambda t: -t.quality_score)
        best = tracks_sorted[0]
        
        print(f"[{i}/{len(duplicates)}] {tracks[0].artist} - {tracks[0].title}")
        
        # Check duration difference
        durations = [t.duration for t in tracks]
        duration_diff = max(durations) - min(durations)
        
        for j, track in enumerate(tracks_sorted):
            quality = format_quality(track)
            duration = format_duration(track.duration)
            history = ""
            if track.playcount > 0 or track.rating > 0:
                stars = track.rating // 51
                history = f", plays={track.playcount}, {'★' * stars if stars else 'no stars'}"
            
            marker = "→ BEST" if j == 0 else ""
            print(f"    {chr(65+j)}: {quality}, {duration}{history} {marker}")
            print(f"       {track.path}")
        
        if duration_diff > 3:
            print(f"    ⚠️  Duration differs by {duration_diff:.0f}s - might be different edits!")
        
        print()
        print("    Options:")
        print("    [B] Keep BEST quality, remove others (merge metadata)")
        for j in range(len(tracks_sorted)):
            print(f"    [{chr(65+j)}] Keep {chr(65+j)} only, remove others")
        print("    [S] Skip (keep all)")
        
        choice = input("    Choice [B/A/B/.../S]: ").strip().upper()
        
        if choice == 'S' or choice == '':
            print("    → Skipped\n")
            stats['skipped'] += len(tracks)
            continue
        
        if choice == 'B':
            keep_track = tracks_sorted[0]
            remove_tracks = tracks_sorted[1:]
        elif choice in [chr(65+j) for j in range(len(tracks_sorted))]:
            idx = ord(choice) - 65
            keep_track = tracks_sorted[idx]
            remove_tracks = [t for j, t in enumerate(tracks_sorted) if j != idx]
        else:
            print("    → Invalid choice, skipping\n")
            stats['skipped'] += len(tracks)
            continue
        
        if dry_run:
            print(f"    → DRY-RUN: Would keep {keep_track.path.name}")
            for rt in remove_tracks:
                print(f"    → DRY-RUN: Would move to reject: {rt.path.name}")
            stats['kept'] += 1
            stats['removed'] += len(remove_tracks)
        else:
            # Merge metadata to keep_track
            merged = merge_metadata(keep_track, remove_tracks[0] if remove_tracks else keep_track)
            # TODO: Actually write merged metadata to keep_track
            
            # Move removed tracks to reject
            if reject_path:
                reject_path.mkdir(parents=True, exist_ok=True)
                for rt in remove_tracks:
                    dest = reject_path / rt.path.name
                    if dest.exists():
                        stem = dest.stem
                        ext = dest.suffix
                        n = 2
                        while (reject_path / f"{stem} ({n}){ext}").exists():
                            n += 1
                        dest = reject_path / f"{stem} ({n}){ext}"
                    shutil.move(str(rt.path), str(dest))
                    print(f"    → Moved to reject: {rt.path.name}")
            
            print(f"    → Kept: {keep_track.path.name}")
            stats['kept'] += 1
            stats['removed'] += len(remove_tracks)
        
        print()
    
    return stats
