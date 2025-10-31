from __future__ import annotations
from typing import List
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

# Ensure requests-cache is installed
import djlib.metadata  # noqa: F401
from djlib.config import get_spotify_credentials


def _normalize_tag(tag: str) -> str:
    t = (tag or "").strip().lower()
    t = t.replace("_", " ").replace("-", " ")
    t = " ".join(t.split())
    return t


def _get_client() -> spotipy.Spotify | None:
    cid, secret = get_spotify_credentials()
    if not cid or not secret:
        return None
    auth = SpotifyClientCredentials(client_id=cid, client_secret=secret)
    return spotipy.Spotify(auth_manager=auth, requests_timeout=15, retries=2)


def artist_genres_by_track(artist: str, title: str, *, limit_artists: int = 3) -> List[str]:
    """Search track, take top artists and return their genres (normalized)."""
    sp = _get_client()
    if not sp:
        return []
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist and not title:
        return []
    q_parts = []
    if artist:
        q_parts.append(f'artist:"{artist}"')
    if title:
        q_parts.append(f'track:"{title}"')
    q = " ".join(q_parts) or (artist or title)
    try:
        res = sp.search(q=q, type="track", limit=5)
        items = ((res or {}).get("tracks") or {}).get("items") or []
        if not items:
            return []
        # pick the first best match
        it = items[0]
        artist_ids = [a.get("id") for a in it.get("artists", []) if a.get("id")]
        artist_ids = artist_ids[:limit_artists]
        if not artist_ids:
            return []
        arts = sp.artists(artist_ids).get("artists", [])
        genres: List[str] = []
        for a in arts:
            for g in a.get("genres", []) or []:
                gg = _normalize_tag(g)
                if gg:
                    genres.append(gg)
        # de-dup preserve order
        seen = set()
        uniq = [g for g in genres if not (g in seen or seen.add(g))]
        return uniq
    except Exception:
        return []
