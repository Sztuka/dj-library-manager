from __future__ import annotations
from pathlib import Path
from typing import Dict
from djlib.filename import parse_from_filename
from djlib.tags import read_tags
import json
import os

MB_ENDPOINT = "https://musicbrainz.org/ws/2/recording"
MB_UA = "DJLibraryManager/0.1 (+https://github.com/Sztuka/dj-library-manager)"


def suggest_metadata(path: Path, tags: Dict[str, str]) -> Dict[str, str]:
    """
    Zwraca proponowane metadane do akceptacji. Priorytety:
    1) (docelowo) online lookup (MusicBrainz/AcoustID) – TODO
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

    # Gatunek/album/rok/czas – na razie puste, będą uzupełniane online w przyszłości
    return {
        "artist_suggest": artist,
        "title_suggest": title,
        "version_suggest": version,
        "genre_suggest": "",
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
    """Prosty lookup przez MusicBrainz (bez kluczy/API key). Zwraca dict sug_* albo None.
    Używa tylko artist/title. W przyszłości można dołożyć AcoustID → MBID.
    """
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not title and not artist:
        return None
    try:
        import requests  # import lokalny, żeby testy/offline nie wymagały requests
    except Exception:
        return None

    q = []
    if artist:
        q.append(f'artist:"{artist}"')
    if title:
        q.append(f'recording:"{title}"')
    query = " AND ".join(q)
    params = {"query": query, "fmt": "json", "limit": 1, "inc": "releases+tags"}
    headers = {"User-Agent": MB_UA}
    try:
        resp = requests.get(MB_ENDPOINT, params=params, headers=headers, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        recs = data.get("recordings") or []
        if not recs:
            return None
        rec = recs[0]
        out_artist = _join_artist_credit(rec.get("artist-credit") or []) or artist
        out_title = rec.get("title") or title
        releases = rec.get("releases") or []
        album = releases[0].get("title") if releases else ""
        date = releases[0].get("date") if releases else ""
        year = (date or "").split("-")[0] if date else ""
        length_ms = rec.get("length")
        duration = _format_duration(length_ms if isinstance(length_ms, int) else None)
        # tags jako prosty genre hint
        tags = rec.get("tags") or []
        genre = ""
        if tags:
            # weź najpopularniejszy tag jako genre_suggest
            try:
                genre = sorted(tags, key=lambda t: int(t.get("count", 0)), reverse=True)[0].get("name","")
            except Exception:
                genre = tags[0].get("name","")
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
        params = {"fmt": "json", "inc": "artists+releases+tags"}
        headers = {"User-Agent": MB_UA}
        r = requests.get(url, params=params, headers=headers, timeout=10)
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
        tags = rec.get("tags") or []
        genre = ""
        if tags:
            try:
                genre = sorted(tags, key=lambda t: int(t.get("count", 0)), reverse=True)[0].get("name", "")
            except Exception:
                genre = tags[0].get("name", "")
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
    """Spróbuj wzbogacić metadane online (MusicBrainz) bazując na suggest_* lub nazwie.
    Nie rusza BPM/Key. Zwraca uzupełnienia sugerowanych pól albo None.
    """
    artist = (row.get("artist_suggest") or "").strip()
    title = (row.get("title_suggest") or "").strip()
    if not artist and not title:
        a, t, v = parse_from_filename(path)
        artist, title = a, t
    # 1) jeśli mamy fingerprint i duration, spróbuj AcoustID
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
    # 2) fallback: MusicBrainz search — spróbuj kilku wariantów
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
