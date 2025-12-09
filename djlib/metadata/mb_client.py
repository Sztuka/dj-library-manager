from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Tuple
import os
import time

import musicbrainzngs
from tenacity import retry, wait_exponential_jitter, stop_after_attempt, retry_if_exception_type

# Configure MusicBrainz client
APP_NAME = os.getenv("MB_APP_NAME", "DJLibraryManager")
APP_VER = os.getenv("MB_APP_VERSION", "0.1")
APP_CONTACT = os.getenv("MB_CONTACT", "https://github.com/Sztuka/dj-library-manager")

# MB Terms require a descriptive UA
musicbrainzngs.set_useragent(APP_NAME, APP_VER, APP_CONTACT)
# Global 1 request/second. Library provides internal throttling; we add a guard as well.
musicbrainzngs.set_rate_limit(limit_or_interval=1.0, new_requests=1)

_LAST_CALL_TS: float = 0.0

def _throttle_mb(min_interval: float = 1.05) -> None:
    global _LAST_CALL_TS
    now = time.time()
    wait = _LAST_CALL_TS + min_interval - now
    if wait > 0:
        time.sleep(wait)
    _LAST_CALL_TS = time.time()

@dataclass
class RecordingMatch:
    recording_id: str
    title: str
    artist_credit: str
    release_group_id: Optional[str]
    artist_id: Optional[str]
    score: int
    length_ms: Optional[int]


@dataclass
class FirstReleaseAlbum:
    """Canonical first release album for a recording from MusicBrainz."""
    recording_mbid: str
    release_mbid: str           # chosen release
    release_group_mbid: str     # associated release-group
    album_title: str
    original_release_date: str  # e.g. "1975-12-01" or "1969-10-22" or "1999"
    original_release_year: int  # first 4 digits
    release_category: str       # "studio" or "live"
    source: str = "musicbrainz_first_release"


def _join_artist_credit(ac: list) -> str:
    parts = []
    for c in ac or []:
        n = c.get("name") or (c.get("artist") or {}).get("name")
        if n:
            parts.append(n)
    return ", ".join(parts) if parts else ""

@retry(wait=wait_exponential_jitter(initial=1, max=10), stop=stop_after_attempt(5), reraise=True)
def _search_recordings(q: str, limit: int = 5) -> dict:
    _throttle_mb()
    return musicbrainzngs.search_recordings(query=q, limit=limit)

@retry(wait=wait_exponential_jitter(initial=1, max=10), stop=stop_after_attempt(5), reraise=True)
def _search_release_groups(q: str, limit: int = 5) -> dict:
    _throttle_mb()
    return musicbrainzngs.search_release_groups(query=q, limit=limit)

@retry(wait=wait_exponential_jitter(initial=1, max=10), stop=stop_after_attempt(5), reraise=True)
def _get_recording_by_id(rid: str) -> dict:
    _throttle_mb()
    return musicbrainzngs.get_recording_by_id(rid, includes=["tags","artists","releases"])  # type: ignore[arg-type]

@retry(wait=wait_exponential_jitter(initial=1, max=10), stop=stop_after_attempt(5), reraise=True)
def _get_recording_by_id_with_releases(rid: str) -> dict:
    """Get recording with full release+release-group data for first release resolution."""
    _throttle_mb()
    return musicbrainzngs.get_recording_by_id(rid, includes=["releases"])  # type: ignore[arg-type]

@retry(wait=wait_exponential_jitter(initial=1, max=10), stop=stop_after_attempt(5), reraise=True)
def _get_release_by_id(release_mbid: str) -> dict:
    _throttle_mb()
    return musicbrainzngs.get_release_by_id(release_mbid, includes=["release-groups"])  # type: ignore[arg-type]

@retry(wait=wait_exponential_jitter(initial=1, max=10), stop=stop_after_attempt(5), reraise=True)
def _get_release_group_by_id(rgid: str) -> dict:
    _throttle_mb()
    return musicbrainzngs.get_release_group_by_id(rgid, includes=["tags", "releases"])  # type: ignore[arg-type]


def get_release_year(release_mbid: str) -> Optional[str]:
    """
    Get ORIGINAL release year from MusicBrainz release MBID.
    
    Uses release-group's first-release-date instead of specific release date
    to get the original/canonical year (not reissues).
    
    Args:
        release_mbid: MusicBrainz release MBID
    
    Returns:
        Original release year as string (e.g. "1975") or None if not found
    """
    try:
        release_data = _get_release_by_id(release_mbid)
        release = (release_data or {}).get("release", {})
        
        # Try to get first-release-date from release-group (original year)
        rg = release.get("release-group", {})
        rg_id = rg.get("id")
        
        if rg_id:
            try:
                rg_data = _get_release_group_by_id(rg_id)
                rg_full = (rg_data or {}).get("release-group", {})
                first_date = rg_full.get("first-release-date", "")
                if first_date and len(first_date) >= 4:
                    return first_date[:4]  # Extract year (YYYY)
            except Exception:
                pass
        
        # Fallback: use specific release date if first-release-date not available
        date = release.get("date", "")
        if date and len(date) >= 4:
            return date[:4]  # Extract year (YYYY)
    except Exception:
        pass
    return None


def get_release_group_type(rgid: str) -> Optional[str]:
    """Get primary-type of a release-group (Album, Single, EP, Live, Compilation, etc.)."""
    try:
        rg_data = _get_release_group_by_id(rgid)
        rg = (rg_data or {}).get("release-group", {})
        return rg.get("primary-type")
    except Exception:
        return None

@retry(wait=wait_exponential_jitter(initial=1, max=10), stop=stop_after_attempt(5), reraise=True)
def _get_artist_by_id(aid: str) -> dict:
    _throttle_mb()
    return musicbrainzngs.get_artist_by_id(aid, includes=["tags","aliases"])  # type: ignore[arg-type]


def search_recording(artist: str, title: str, duration: Optional[int] = None) -> Optional[RecordingMatch]:
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist and not title:
        return None
    q_parts: List[str] = []
    if artist:
        q_parts.append(f'artist:"{artist}"')
    if title:
        q_parts.append(f'recording:"{title}"')
    if duration:
        pass  # could add approx duration to query once WS supports; we score locally
    q = " AND ".join(q_parts)
    try:
        data = _search_recordings(q, limit=5)
        recs = (data or {}).get("recording-list") or []
        best: Optional[RecordingMatch] = None
        best_score_val: float = -1.0
        for rec in recs:
            rid = rec.get("id")
            length_ms = None
            try:
                length_ms = int(rec.get("length")) if rec.get("length") else None
            except Exception:
                length_ms = None
            ac = _join_artist_credit(rec.get("artist-credit") or [])
            score = int(rec.get("ext:score", 0))
            # Find earliest release-group (original release, not live/compilation/remaster)
            rgid = None
            if rec.get("release-list"):
                releases = rec.get("release-list") or []
                # Filter out live/compilation releases, prefer original studio releases
                studio_releases = [
                    r for r in releases
                    if r.get("release-group", {}).get("primary-type") in ["Single", "Album", "EP"]
                ]
                candidates = studio_releases if studio_releases else releases
                # Sort by date (earliest first)
                dated_releases = [
                    (r, r.get("date", "9999"))
                    for r in candidates
                    if r.get("date")
                ]
                if dated_releases:
                    dated_releases.sort(key=lambda x: x[1])
                    earliest = dated_releases[0][0]
                    rgid = earliest.get("release-group", {}).get("id")
                elif candidates:
                    # Fallback: use first if no dates available
                    rgid = candidates[0].get("release-group", {}).get("id")
            aid = None
            if rec.get("artist-credit"):
                ent = (rec.get("artist-credit")[0] or {}).get("artist") or {}
                aid = ent.get("id")
            # local scoring: MB score + duration closeness
            bonus = 0.0
            if duration and length_ms:
                diff = abs(length_ms - duration * 1000)
                # within 2s -> +20, 5s -> +10, 15s -> +3
                if diff <= 2000:
                    bonus = 20
                elif diff <= 5000:
                    bonus = 10
                elif diff <= 15000:
                    bonus = 3
            s_val = float(score) + bonus
            if s_val > best_score_val:
                best_score_val = s_val
                best = RecordingMatch(
                    recording_id=rid,
                    title=rec.get("title", ""),
                    artist_credit=ac,
                    release_group_id=rgid,
                    artist_id=aid,
                    score=score,
                    length_ms=length_ms,
                )
        return best
    except Exception:
        return None


def _tags_to_list(tags: list) -> List[str]:
    out: List[str] = []
    for t in tags or []:
        name = (t.get("name") or t.get("genre", {}).get("name") or "").strip()
        if name:
            out.append(name)
    return out


def get_recording_genres(recording_id: str, *, release_group_id: Optional[str] = None, artist_id: Optional[str] = None) -> List[str]:
    """Collect tags/genres from recording -> release-group -> artist."""
    genres: List[str] = []
    try:
        r = _get_recording_by_id(recording_id)
        rec = (r or {}).get("recording", {})
        genres.extend(_tags_to_list(rec.get("tag-list", [])))
        # genres key (WS2+) may be present depending on entity
        genres.extend(_tags_to_list(rec.get("genre-list", [])))
        if not release_group_id:
            try:
                rg = (rec.get("release-list") or [{}])[0].get("release-group", {})
                release_group_id = rg.get("id")
            except Exception:
                pass
    except Exception:
        pass
    try:
        if release_group_id:
            rg = _get_release_group_by_id(release_group_id)
            ent = (rg or {}).get("release-group", {})
            genres.extend(_tags_to_list(ent.get("tag-list", [])))
            genres.extend(_tags_to_list(ent.get("genre-list", [])))
    except Exception:
        pass
    try:
        if artist_id:
            a = _get_artist_by_id(artist_id)
            ent = (a or {}).get("artist", {})
            genres.extend(_tags_to_list(ent.get("tag-list", [])))
            genres.extend(_tags_to_list(ent.get("genre-list", [])))
    except Exception:
        pass
    # de-dup preserve order
    seen = set()
    uniq = [g for g in genres if not (g.lower() in seen or seen.add(g.lower()))]
    return uniq


def get_original_release_year(artist: str, title: str) -> Optional[str]:
    """Get original release year using release-group search."""
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist and not title:
        return None
    q_parts: List[str] = []
    if artist:
        q_parts.append(f'artist:"{artist}"')
    if title:
        q_parts.append(f'releasegroup:"{title}"')
    q = " AND ".join(q_parts)
    try:
        data = _search_release_groups(q, limit=10)
        rg_list = (data or {}).get("release-group-list") or []
        # Filter for Single/Album/EP (not Live/Compilation)
        studio_rgs = [
            rg for rg in rg_list
            if rg.get("primary-type") in ["Single", "Album", "EP"]
        ]
        candidates = studio_rgs if studio_rgs else rg_list
        # Sort by score and take best match
        if candidates:
            candidates.sort(key=lambda x: int(x.get("ext:score", 0)), reverse=True)
            best = candidates[0]
            first_release = best.get("first-release-date", "")
            if first_release and len(first_release) >= 4:
                return first_release[:4]
    except Exception:
        pass
    return None


def get_original_release_info(artist: str, title: str) -> Optional[Tuple[str, str, str]]:
    """Get original release info: (year, album, release_group_id).
    
    Uses release-group search to find earliest studio release.
    Returns tuple of (year, album_title, release_group_id) or None.
    """
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist and not title:
        return None
    q_parts: List[str] = []
    if artist:
        q_parts.append(f'artist:"{artist}"')
    if title:
        q_parts.append(f'releasegroup:"{title}"')
    q = " AND ".join(q_parts)
    try:
        data = _search_release_groups(q, limit=10)
        rg_list = (data or {}).get("release-group-list") or []
        # Filter for Single/Album/EP (not Live/Compilation)
        studio_rgs = [
            rg for rg in rg_list
            if rg.get("primary-type") in ["Single", "Album", "EP"]
        ]
        candidates = studio_rgs if studio_rgs else rg_list
        # Sort by score and take best match
        if candidates:
            candidates.sort(key=lambda x: int(x.get("ext:score", 0)), reverse=True)
            best = candidates[0]
            
            year = ""
            first_release = best.get("first-release-date", "")
            if first_release and len(first_release) >= 4:
                year = first_release[:4]
            
            album = (best.get("title") or "").strip()
            release_group_id = best.get("id", "")
            
            if year and album and release_group_id:
                return (year, album, release_group_id)
    except Exception:
        pass
    return None


def mb_fetch_first_release_for_recording(recording_mbid: str, artist: str = "", title: str = "") -> Optional[FirstReleaseAlbum]:
    """
    Resolve the first/canonical studio release using release-group lookup.
    
    Strategy:
    1. Get recording_mbid (from AcoustID or search)
    2. Fetch releases for recording → extract unique release-group IDs
    3. Fetch details for each RG, filter studio (not Live/Compilation)
    4. Return earliest studio RG
    
    Note: This requires multiple API calls due to MB API limitations:
    - recording?inc=releases returns releases (not release-groups)
    - Must extract RG IDs from releases and fetch each separately
    
    Args:
        recording_mbid: MusicBrainz recording ID (required)
        artist: Artist name (used if no recording_mbid)
        title: Track title (used if no recording_mbid)
    
    Returns:
        FirstReleaseAlbum or None if resolution fails
    """
    if not recording_mbid and not (artist and title):
        return None
    
    try:
        # Step 1: Get recording_mbid if needed
        if not recording_mbid and artist and title:
            match = search_recording(artist, title)
            if match and match.recording_id:
                recording_mbid = match.recording_id
        
        if not recording_mbid:
            return None
        
        # Step 2: Get releases for this recording
        rec_data = _get_recording_by_id_with_releases(recording_mbid)
        rec = (rec_data or {}).get("recording", {})
        releases = rec.get("release-list", [])
        
        if not releases:
            return None
        
        # Step 3: Extract unique release-group IDs
        rg_ids = set()
        for rel in releases:
            rg = rel.get("release-group", {})
            rg_id = rg.get("id")
            if rg_id:
                rg_ids.add(rg_id)
        
        if not rg_ids:
            return None
        
        # Step 4: Fetch details for each RG and filter for studio
        studio_rgs = []
        for rg_id in rg_ids:
            try:
                rg_data = _get_release_group_by_id(rg_id)
                rg = (rg_data or {}).get("release-group", {})
                
                primary_type = rg.get("primary-type", "")
                secondary_types = rg.get("secondary-type-list", [])
                
                is_live = "Live" in secondary_types or primary_type == "Live"
                is_compilation = "Compilation" in secondary_types
                
                # Keep only studio albums with release date
                if not is_live and not is_compilation and rg.get("first-release-date"):
                    studio_rgs.append(rg)
            except Exception:
                continue
        
        if not studio_rgs:
            return None
        
        # Step 5: Sort by first-release-date and pick earliest
        studio_rgs.sort(key=lambda rg: rg.get("first-release-date", ""))
        earliest_rg = studio_rgs[0]
        
        album_title = (earliest_rg.get("title") or "").strip()
        first_release_date = earliest_rg.get("first-release-date", "")
        rg_id = earliest_rg.get("id", "")
        
        if not album_title or not first_release_date or not rg_id:
            return None
        
        # Extract year
        year = int(first_release_date[:4]) if len(first_release_date) >= 4 else 0
        if not year:
            return None
        
        # Step 6: Get earliest official release MBID from this RG
        release_mbid = ""
        try:
            releases = earliest_rg.get("release-list", [])
            
            official_releases = [
                r for r in releases
                if r.get("status") == "Official" and r.get("date")
            ]
            
            if official_releases:
                official_releases.sort(key=lambda r: r.get("date", ""))
                release_mbid = official_releases[0].get("id", "")
                # Use actual earliest release date (may be more precise)
                earliest_date = official_releases[0].get("date", "")
                if earliest_date and len(earliest_date) >= len(first_release_date):
                    first_release_date = earliest_date
                    if len(earliest_date) >= 4:
                        year = int(earliest_date[:4])
        except Exception:
            pass
        
        return FirstReleaseAlbum(
            recording_mbid=recording_mbid,
            release_mbid=release_mbid,
            release_group_mbid=rg_id,
            album_title=album_title,
            original_release_date=first_release_date,
            original_release_year=year,
            release_category="studio",
            source="musicbrainz_first_release",
        )
    
    except Exception:
        return None
        return None

