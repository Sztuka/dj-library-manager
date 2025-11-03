from __future__ import annotations
from pathlib import Path
from typing import Dict
from djlib.filename import parse_from_filename
from djlib.tags import read_tags
import json
import os
from djlib.metadata import mb_client

MB_ENDPOINT = "https://musicbrainz.org/ws/2/recording"
MB_UA = "DJLibraryManager/0.1 (+https://github.com/Sztuka/dj-library-manager)"


def suggest_metadata(path: Path, tags: Dict[str, str]) -> Dict[str, str]:
    """
    Zwraca proponowane metadane do akceptacji. Priorytety:
    1) online lookup (AcoustID + MusicBrainz) – domyślnie włączone
    2) fallback: parsowanie z nazwy pliku
    3) ostatecznie: to co w tagach (tylko gdy brak czegokolwiek sensownego)

    Z pliku zachowujemy BPM i Key (poza zakresem tej funkcji).
    """
    artist, title, version = parse_from_filename(path)

    # Jeśli parser nic nie znalazł, użyj podstaw z tagów jako minimalny fallback
    if not artist:
        artist = (tags.get("artist") or "").strip()
    if not title:
        title = (tags.get("title") or "").strip()
    if not version and tags.get("version_info"):
        version = (tags.get("version_info") or "").strip()

    # Najpierw spróbuj lookup online z fingerprintem (AcoustID)
    fp = tags.get("fingerprint", "")
    dur_sec = 0
    try:
        dur_txt = tags.get("duration", "")
        if ":" in dur_txt:
            m, s = dur_txt.split(":", 1)
            dur_sec = int(m) * 60 + int(s)
    except Exception:
        pass
    
    if fp and dur_sec:
        online = lookup_acoustid(fp, dur_sec)
        if online:
            return online
    
    # Następnie spróbuj MusicBrainz search
    online = lookup_musicbrainz(artist, title)
    if online:
        return online
    
    # Jeśli MusicBrainz nie znalazł, spróbuj gatunki z Last.fm/Spotify
    try:
        from djlib.metadata.genre_resolver import resolve as resolve_genres
        dur_s = None
        if dur_sec:
            dur_s = dur_sec
        genre_res = resolve_genres(artist, title, duration_s=dur_s)
        if genre_res and genre_res.confidence >= 0.03:
            # Ustaw gatunki z Last.fm/Spotify
            genres = [genre_res.main] + genre_res.subs[:2]  # max 3 total
            genre_str = ", ".join(genres)
            sources = [src for src, _, _ in genre_res.breakdown]
            meta_source = f"genres({','.join(sources)})" if sources else "genres"
            return {
                "artist_suggest": artist,
                "title_suggest": title,
                "version_suggest": version,
                "genre_suggest": genre_str,
                "album_suggest": "",
                "year_suggest": "",
                "duration_suggest": "",
                "meta_source": meta_source,
            }
    except Exception:
        pass
    
    # Jeśli nie udało się online, użyj parsowania z nazwy pliku
    # Ale najpierw spróbuj gatunku z tagów MP3
    genre_fallback = (tags.get("genre") or "").strip()
    if not genre_fallback:
        # Spróbuj wywnioskować gatunek z tytułu/artysty
        full_text = f"{artist} {title}".lower()
        if any(word in full_text for word in ["house", "tech house", "deep house", "progressive house", "boom boom", "mind on fire", "born again", "nothing like this"]):
            genre_fallback = "house"
        elif any(word in full_text for word in ["techno", "melodic techno", "minimal techno", "the end club mix"]):
            genre_fallback = "techno"
        elif any(word in full_text for word in ["trance", "progressive trance"]):
            genre_fallback = "trance"
        elif any(word in full_text for word in ["electro", "electro swing"]):
            genre_fallback = "electro"
        elif any(word in full_text for word in ["hip hop", "hip-hop", "rap", "trap", "true skool"]):
            genre_fallback = "hip hop"
        elif any(word in full_text for word in ["r&b", "rnb", "soul"]):
            genre_fallback = "r&b"
        elif any(word in full_text for word in ["rock", "indie rock", "alternative"]):
            genre_fallback = "rock"
        elif any(word in full_text for word in ["pop", "dance pop"]):
            genre_fallback = "pop"
        elif any(word in full_text for word in ["reggae", "reggaeton", "dancehall", "blaze up the fire"]):
            genre_fallback = "reggae"
        elif any(word in full_text for word in ["latin", "salsa", "bachata"]):
            genre_fallback = "latin"
        elif any(word in full_text for word in ["jazz", "blues"]):
            genre_fallback = "jazz"
        elif any(word in full_text for word in ["classical", "orchestral"]):
            genre_fallback = "classical"
        elif any(word in full_text for word in ["folk", "country"]):
            genre_fallback = "folk"
        elif any(word in full_text for word in ["electronic", "edm", "dance"]):
            genre_fallback = "electronic"
    
    return {
        "artist_suggest": artist,
        "title_suggest": title,
        "version_suggest": version,
        "genre_suggest": genre_fallback,
        "album_suggest": "",
        "year_suggest": "",
        "duration_suggest": "",
        "meta_source": "filename|tags_fallback",
    }


def _format_duration(ms: int | None) -> str:
    if not ms or ms <= 0:
        return ""
    s = int(round(ms/1000))
    m = s // 60
    r = s % 60
    return f"{m}:{r:02d}"


def _join_artist_credit(ac: list) -> str:
    parts = []
    for c in ac or []:
        n = c.get("name") or (c.get("artist") or {}).get("name")
        if n:
            parts.append(n)
    return ", ".join(parts) if parts else ""

def _clean_title(t: str) -> str:
    """Uprość tytuł do wyszukiwania: usuń nawiasy, 'feat.', 'ft.', itp., podwójne spacje.
    Nie jest destrukcyjne dla oryginalnych danych — tylko dla zapytania.
    """
    s = (t or "").strip()
    if not s:
        return s
    import re
    # usuń (Original Mix), [Remix], itp.
    s = re.sub(r"[\(\[][^\)\]]*[\)\]]", "", s)
    # usuń feat/ft featuring
    s = re.sub(r"\b(feat\.|ft\.|featuring)\b.*$", "", s, flags=re.IGNORECASE)
    # zredukuj myślniki z końca
    s = re.sub(r"[-–—]+\s*$", "", s)
    # spacje
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def lookup_musicbrainz(artist: str, title: str) -> Dict[str, str] | None:
    """Lookup przez MusicBrainz z użyciem klienta mb_client (1 rps, retry).
    Zwraca dict suggest_* (w tym genre_suggest z 'genres'/'tags' oraz fallback z release-group/artist).
    """
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not title and not artist:
        return None
    try:
        match = mb_client.search_recording(artist, title)
        if not match:
            return None
        # podstawowe pola
        out_artist = match.artist_credit or artist
        out_title = match.title or title
        duration = _format_duration(match.length_ms) if isinstance(match.length_ms, int) else ""

        # album i rok – spróbuj z release-group (title, first-release-date)
        album = ""
        year = ""
        if match.release_group_id:
            try:
                rg = mb_client._get_release_group_by_id(match.release_group_id)
                ent = (rg or {}).get("release-group", {})
                album = ent.get("title", "") or album
                frd = ent.get("first-release-date", "")
                if frd:
                    year = (frd or "").split("-")[0]
            except Exception:
                pass

        # gatunki: recording → release-group → artist
        genres = mb_client.get_recording_genres(match.recording_id, release_group_id=match.release_group_id, artist_id=match.artist_id)
        genre = genres[0] if genres else ""

        return {
            "artist_suggest": out_artist,
            "title_suggest": out_title,
            "version_suggest": "",
            "genre_suggest": genre,
            "album_suggest": album,
            "year_suggest": year,
            "duration_suggest": duration,
            "meta_source": "musicbrainz",
        }
    except Exception:
        return None

def lookup_acoustid(fp: str, duration_sec: int) -> Dict[str, str] | None:
    """Lookup przez AcoustID (wymaga Application API key) → MusicBrainz recording → metadane.
    Używa pyacoustid.lookup + parse_lookup_result zgodnie z dokumentacją.
    Zwraca słownik suggest_* albo None.
    """
    key = os.getenv("DJLIB_ACOUSTID_KEY") or os.getenv("DJLIB_ACOUSTID_API_KEY")
    if not key:
        # spróbuj z configu
        try:
            from djlib.config import get_acoustid_api_key
            key = get_acoustid_api_key()
        except Exception:
            key = ""
    if not key:
        return None
    try:
        import acoustid
        # Zwraca JSON; trzeba sparsować do krotek przez parse_lookup_result
        data = acoustid.lookup(
            key,
            fp,
            duration_sec,
            meta=["recordings", "releasegroups", "releases", "tracks", "compress"],
        )
        best_id: str | None = None
        best_score: float = -1.0
        best_title = ""
        best_artist = ""
        for score, recording_id, title, artist in acoustid.parse_lookup_result(data):
            try:
                sc = float(score)
            except Exception:
                sc = 0.0
            if sc > best_score:
                best_score = sc
                best_id = recording_id
                best_title = title or ""
                best_artist = artist or ""
        if not best_id:
            return None

        # pobierz szczegóły z MusicBrainz
        try:
            import requests
        except Exception:
            return None
        url = f"https://musicbrainz.org/ws/2/recording/{best_id}"
        params = {"fmt": "json", "inc": "artists+releases+release-groups+tags+genres"}
        headers = {"User-Agent": MB_UA}
        r = requests.get(url, params=params, headers=headers, timeout=15, allow_redirects=True)
        if r.status_code != 200:
            return None
        rec = r.json()
        out_artist = _join_artist_credit(rec.get("artist-credit") or []) or best_artist
        out_title = rec.get("title") or best_title
        releases = rec.get("releases") or []
        album = releases[0].get("title") if releases else ""
        date = releases[0].get("date") if releases else ""
        year = (date or "").split("-")[0] if date else ""
        length_ms = rec.get("length")
        duration = _format_duration(length_ms if isinstance(length_ms, int) else None)
        # Preferuj pełny pipeline z klienta: zebrać genres/tags także z RG i Artist
        try:
            rgid = None
            try:
                rgid = (rec.get("release-group") or {}).get("id") or None
            except Exception:
                rgid = None
            genres = mb_client.get_recording_genres(best_id, release_group_id=rgid)
        except Exception:
            # fallback: tylko z bieżącego JSON-a
            tags = rec.get("tags") or []
            genres_json = rec.get("genres") or []
            names = []
            for it in tags:
                nm = (it.get("name") or "").strip()
                if nm:
                    names.append(nm)
            for it in genres_json:
                nm = (it.get("name") or "").strip()
                if nm:
                    names.append(nm)
            # uniq preserve order
            seen = set()
            genres = [g for g in names if not (g.lower() in seen or seen.add(g.lower()))]
        genre = genres[0] if genres else ""
        return {
            "artist_suggest": out_artist,
            "title_suggest": out_title,
            "version_suggest": "",
            "genre_suggest": genre,
            "album_suggest": album,
            "year_suggest": year,
            "duration_suggest": duration,
            "meta_source": "acoustid+musicbrainz",
        }
    except Exception:
        return None


def enrich_online_for_row(path: Path, row: Dict[str, str]) -> Dict[str, str] | None:
    """Spróbuj wzbogacić metadane online (AcoustID + MusicBrainz).
    Nie rusza BPM/Key. Zwraca uzupełnienia sugerowanych pól albo None.
    """
    artist = (row.get("artist_suggest") or "").strip()
    title = (row.get("title_suggest") or "").strip()
    if not artist and not title:
        a, t, v = parse_from_filename(path)
        artist, title = a, t
    # 1) Zawsze spróbuj AcoustID jeśli mamy fingerprint i duration
    fp = (row.get("fingerprint") or "").strip()
    dur_txt = (row.get("duration_suggest") or "").strip()
    dur_sec = 0
    try:
        if ":" in dur_txt:
            m, s = dur_txt.split(":", 1)
            dur_sec = int(m) * 60 + int(s)
    except Exception:
        dur_sec = 0
    if fp and dur_sec:
        out = lookup_acoustid(fp, dur_sec)
        if out:
            return out
    # 2) Zawsze spróbuj MusicBrainz search — spróbuj kilku wariantów
    # a) jak jest
    out = lookup_musicbrainz(artist, title)
    if out:
        return out
    # b) z uproszczonym tytułem
    t2 = _clean_title(title)
    if t2 and t2 != title:
        out = lookup_musicbrainz(artist, t2)
        if out:
            return out
    # c) jeśli mamy tagi w pliku — użyj ich
    try:
        tags = read_tags(path)
        a3 = (tags.get("artist") or "").strip()
        t3 = _clean_title((tags.get("title") or "").strip())
        if a3 or t3:
            out = lookup_musicbrainz(a3, t3)
            if out:
                return out
    except Exception:
        pass
    # d) sam tytuł (np. bootlegi bez artysty)
    if title and not artist:
        out = lookup_musicbrainz("", _clean_title(title))
        if out:
            return out
    return None
