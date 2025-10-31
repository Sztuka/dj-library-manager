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
def _get_recording_by_id(rid: str) -> dict:
    _throttle_mb()
    return musicbrainzngs.get_recording_by_id(rid, includes=["tags","artists","releases"])  # type: ignore[arg-type]

@retry(wait=wait_exponential_jitter(initial=1, max=10), stop=stop_after_attempt(5), reraise=True)
def _get_release_group_by_id(rgid: str) -> dict:
    _throttle_mb()
    return musicbrainzngs.get_release_group_by_id(rgid, includes=["tags"])  # type: ignore[arg-type]

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
            rgid = None
            if rec.get("release-list"):
                rgid = (rec.get("release-list")[0] or {}).get("release-group", {}).get("id")
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
