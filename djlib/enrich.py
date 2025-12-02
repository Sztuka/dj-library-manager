from __future__ import annotations
from pathlib import Path
from typing import Dict, Tuple
from djlib.filename import parse_from_filename
from djlib.tags import read_tags
import json
import os
import re
import unicodedata
from djlib.metadata import mb_client

# Compiled regexes for feature normalization (performance optimization)
_FEAT_FROM_ARTIST = re.compile(
    r"(?i)^(?P<main>.+?)\s+(?:feat\.?|ft\.?|featuring)\s+(?P<feat>.+)$"
)
_FEAT_IN_BRACKETS = re.compile(
    r"(?i)\((?:feat\.?|ft\.?|featuring)\s+(?P<feat>[^)]+)\)"
)
_FEAT_INLINE = re.compile(
    r"(?i)\s+(?:feat\.?|ft\.?|featuring)\s+(?P<feat>.+)$"
)

MB_ENDPOINT = "https://musicbrainz.org/ws/2/recording"
MB_UA = "DJLibraryManager/0.1 (+https://github.com/Sztuka/dj-library-manager)"


# Special artist names that should preserve uppercase/special formatting
SPECIAL_ARTISTS = {
    "acdc": "AC/DC",
    "ac/dc": "AC/DC",
    "abba": "ABBA",
    "inxs": "INXS",
    "kmfdm": "KMFDM",
    "haim": "HAIM",
    "mgmt": "MGMT",
    "chvrches": "CHVRCHES",
    "pvris": "PVRIS",
    "sbtrkt": "SBTRKT",
    "mstrkrft": "MSTRKRFT",
    "strfkr": "STRFKR",
    "tlc": "TLC",
    "swv": "SWV",
    "bts": "BTS",
    "sza": "SZA",
    "nofx": "NOFX",
    "afi": "AFI",
    "gwar": "GWAR",
    "nwa": "N.W.A",
    "rem": "R.E.M.",
}

# Known bad uppercase words that should still be title-cased
BAD_UPPERWORDS = {
    "VARIOUS ARTISTS",
    "VARIOUS",
    "UNKNOWN",
    "UNSPECIFIED",
}


def _normalize_features(artist: str, title: str) -> Tuple[str, str]:
    """
    Normalize featuring information between artist and title fields.
    
    Rules:
    - Extract all feat/ft/featuring from artist and move to title
    - Extract all feat/ft/featuring from title (both bracketed and inline)
    - Normalize all variations to "feat." (with dot)
    - Collect all featuring artists and deduplicate (case-insensitive)
    - Append to title as "(feat. Artist1, Artist2, ...)"
    - Preserve & collaborations in artist (e.g., "Bob & Alice" stays in artist)
    
    Args:
        artist: Artist name (may contain feat info)
        title: Track title (may contain feat info)
    
    Returns:
        Tuple of (cleaned_artist, cleaned_title) with normalized featuring info
    """
    if not artist and not title:
        return "", ""
    
    artist = (artist or "").strip()
    title = (title or "").strip()
    
    # Collect all featuring artists from both fields
    feat_artists: list[str] = []
    
    # 1. Extract feat from artist (trailing only)
    m = _FEAT_FROM_ARTIST.match(artist)
    if m:
        artist = m.group("main").strip()
        feat_from_artist = m.group("feat").strip()
        if feat_from_artist:
            feat_artists.append(feat_from_artist)
    
    # 2. Extract feat from title (bracketed format first)
    title_cleaned = title
    m = _FEAT_IN_BRACKETS.search(title_cleaned)
    if m:
        feat_from_title = m.group("feat").strip()
        if feat_from_title:
            feat_artists.append(feat_from_title)
        # Remove the bracketed feat segment
        title_cleaned = _FEAT_IN_BRACKETS.sub("", title_cleaned).strip()
    else:
        # If no bracketed feat, try inline at the end
        m = _FEAT_INLINE.search(title_cleaned)
        if m:
            feat_from_title = m.group("feat").strip()
            if feat_from_title:
                feat_artists.append(feat_from_title)
            # Remove the inline feat segment
            title_cleaned = _FEAT_INLINE.sub("", title_cleaned).strip()
    
    # Clean up title: remove trailing dashes/spaces
    title_cleaned = re.sub(r"[\s\-–—]+$", "", title_cleaned).strip()
    
    # 3. Process collected featuring artists
    if feat_artists:
        # Parse multiple artists from each segment (split by &, comma, "and")
        all_feat = []
        for segment in feat_artists:
            # Split by common separators: & or comma
            parts = re.split(r'\s*[&,]\s*|\s+and\s+', segment, flags=re.IGNORECASE)
            for part in parts:
                part = part.strip()
                if part:
                    all_feat.append(part)
        
        # Deduplicate case-insensitively while preserving first occurrence casing
        seen_lower = set()
        unique_feat = []
        for feat in all_feat:
            feat_lower = feat.lower()
            if feat_lower not in seen_lower:
                seen_lower.add(feat_lower)
                unique_feat.append(feat)
        
        # Clean up featuring artist names (normalize whitespace)
        unique_feat = [re.sub(r"\s+", " ", f).strip() for f in unique_feat]
        unique_feat = [f for f in unique_feat if f]  # Remove empty strings
        
        # Append to title in canonical format (without parentheses)
        if unique_feat:
            feat_str = ", ".join(unique_feat)
            title_cleaned = f"{title_cleaned} feat. {feat_str}"
    
    return artist, title_cleaned


def derive_local_metadata(path: Path, tags: Dict[str, str]) -> Tuple[str, str, str]:
    """
    Normalize and derive artist, title, version from audio tags and filename.
    Returns (artist, title, version) tuple with proper capitalization and cleanup.
    """
    
    def _sanitize_artist(val: str) -> str:
        # Check special artists map first
        raw = (val or "").strip()
        if not raw:
            return ""
        
        # Normalize key for lookup: remove spaces, underscores, dots
        key = re.sub(r"[ _\.]+", "", raw).lower()
        if key in SPECIAL_ARTISTS:
            return SPECIAL_ARTISTS[key]
        
        # Standard cleaning
        s = raw.replace("_", " ")
        s = re.sub(r"\bwww\.[\w\.-]+\b", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s+", " ", s).strip()
        
        if not s:
            return ""
        
        # Heuristic for short all-caps acronym-like artist names
        if s.isupper():
            alpha = re.sub(r"[^A-Za-z]", "", s)
            if (
                s not in BAD_UPPERWORDS
                and " " not in s  # single token, no spaces
                and len(alpha) <= 4  # short acronym, e.g. ABBA, INXS, BTS, M83, U2
            ):
                return s
        
        # Apply title case for lowercase or UPPERCASE strings (preserve MixedCase)
        if s and (s.islower() or s.isupper()):
            # Split by common separators to handle multi-artist strings
            parts = []
            for separator in [" feat. ", " feat ", " ft. ", " ft ", " vs. ", " vs ", " & "]:
                if separator in s.lower():
                    # Find actual separator in original string (case-insensitive)
                    pattern = re.compile(re.escape(separator), re.IGNORECASE)
                    split_parts = pattern.split(s)
                    for i, part in enumerate(split_parts):
                        parts.append(part.strip().title())
                        if i < len(split_parts) - 1:
                            parts.append(separator.strip().lower())
                    s = " ".join(parts)
                    break
            else:
                s = s.title()
            
            # Fix common patterns after title-casing
            s = re.sub(r"\bDj\b", "DJ", s)
            s = re.sub(r"\bMc\b", "MC", s)
            s = re.sub(r"\bAc/dc\b", "AC/DC", s, flags=re.IGNORECASE)
        
        return s

    def _sanitize_title(val: str) -> str:
        if not val:
            return ""
        s = val.replace("_", " ").replace("–", "-").replace("—", "-")
        s = re.sub(r"\b(pobrano|pobrane)\s+z\b.*$", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\bdownloaded\s+from\b.*$", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\bwww\.[\w\.-]+\b", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\.(?:mp3|wav|flac|aiff|m4a|aac)$", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s+-\s+", " - ", s)
        s = re.sub(r"\s+", " ", s)
        s = s.strip()
        
        # Apply title case for lowercase or UPPERCASE strings (preserve MixedCase)
        if s and (s.islower() or s.isupper()):
            s = s.title()
        
        return s

    def _sanitize_version(val: str) -> str:
        if not val:
            return ""
        s = val.replace("_", " ")
        s = re.sub(r"\b(pobrano|pobrane)\s+z\b.*$", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\bdownloaded\s+from\b.*$", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\bwww\.[\w\.-]+\b", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\.(?:mp3|wav|flac|aiff|m4a|aac)$", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s+", " ", s)
        return s.strip()

    def _canonical(val: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (val or "").lower())

    def _clean_stem(stem: str) -> str:
        s = stem.replace("_", " ")
        s = re.sub(r"\((?:https?://|www\.)[^)]*\)", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\bwww\.[\w\.-]+\b", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\b(pobrano|pobrane)\s+z\b.*$", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\bdownloaded\s+from\b.*$", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s+", " ", s)
        return s.strip()

    def _normalize_ascii(val: str) -> str:
        if not val:
            return ""
        return unicodedata.normalize("NFKD", val).encode("ascii", "ignore").decode("ascii").lower()

    def _strip_artist_prefix(title: str, artist: str) -> str:
        if not title or not artist:
            return title
        pattern = rf"^\s*{re.escape(artist)}\s*-\s*(.+)$"
        m = re.match(pattern, title, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
        # Fallback: compare in ASCII-only space (ignoring diacritics)
        norm_title = _normalize_ascii(title)
        norm_artist = _normalize_ascii(artist)
        if norm_artist and norm_title.startswith(norm_artist + " - "):
            dash_idx = title.find("-")
            if dash_idx != -1:
                return title[dash_idx + 1:].strip(" -–—")
        return title

    def _strip_version_suffix(title: str, version: str) -> str:
        if not title or not version:
            return title
        lt = title.lower()
        lv = version.lower()
        if lt.endswith(lv):
            trimmed = title[:len(title) - len(version)]
            trimmed = re.sub(r"[ \-_/]+$", "", trimmed)
            return trimmed.strip()
        return title

    # Get metadata from tags and filename
    pf_artist, pf_title, pf_version = parse_from_filename(path)
    
    artist = _sanitize_artist(tags.get("artist", ""))
    title = _sanitize_title(tags.get("title", ""))
    version = _sanitize_version(tags.get("version_info", ""))

    # Compare canonicalized versions to detect if tags look like filename
    stem_clean = _clean_stem(path.stem)
    full_combo = f"{pf_artist} {pf_title} {pf_version}".strip()
    canon_title = _canonical(title)
    canon_stem = _canonical(stem_clean)
    canon_combo = _canonical(full_combo)

    # Use filename parsing if tags are missing or look like filename
    if not artist and pf_artist:
        artist = _sanitize_artist(pf_artist)

    looks_like_filename = bool(canon_title and canon_stem and canon_title == canon_stem)
    looks_like_combo = bool(canon_title and canon_combo and canon_title == canon_combo)

    if (not title or looks_like_filename or looks_like_combo) and pf_title:
        title = pf_title
        if not version and pf_version:
            version = pf_version

    if not version and pf_version:
        version = pf_version

    # Strip artist prefix after deciding final title source
    title = _strip_artist_prefix(title, artist)
    title = _strip_version_suffix(title, version)
    
    # Apply title case normalization for all-lowercase or all-uppercase
    if title and (title.islower() or title.isupper()):
        title = title.title()
    
    # Normalize featuring information (extract from artist, consolidate in title)
    artist, title = _normalize_features(artist, title)

    return artist.strip(), title.strip(), version.strip()


def suggest_metadata(path: Path, tags: Dict[str, str], enable_online: bool = True) -> Dict[str, str]:
    """
    Zwraca proponowane metadane do akceptacji. Priorytety:
    1) online lookup (AcoustID + MusicBrainz) – opcjonalne (enable_online=True)
    2) fallback: parsowanie z nazwy pliku
    3) ostatecznie: to co w tagach (tylko gdy brak czegokolwiek sensownego)

    Z pliku zachowujemy BPM i Key (poza zakresem tej funkcji).
    
    Args:
        enable_online: If False, skip AcoustID/MusicBrainz/genre resolver (faster for scan)
    """
    # Use derive_local_metadata to get normalized artist, title, version
    artist, title, version = derive_local_metadata(path, tags)

    if enable_online:
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
                # Preserve filename-derived version if online lacks it
                if version and not (online.get("version_suggest") or "").strip():
                    online = {**online, "version_suggest": version}
                return online
        
        # Następnie spróbuj MusicBrainz search
        online = lookup_musicbrainz(artist, title)
        if online:
            if version and not (online.get("version_suggest") or "").strip():
                online = {**online, "version_suggest": version}
            return online
        
        # Jeśli MusicBrainz nie znalazł, spróbuj gatunki z Last.fm/SoundCloud/Beatport (resolver)
        # oraz wyciągnij rok i album z Last.fm/Beatport lub tagów pliku
        # Priority: Last.fm/Beatport > tagi pliku (online ma więcej kontekstu o wydaniach)
        year_from_tags = tags.get("year", "").strip()
        album_from_tags = tags.get("album", "").strip()
        year_from_online = ""
        album_from_online = ""
        try:
            from djlib.metadata.genre_resolver import resolve as resolve_genres
            from djlib.metadata import lastfm, beatport
            
            dur_s = dur_sec if dur_sec else None
            genre_res = resolve_genres(
                artist, title, version=version, duration_s=dur_s,
                disable_soundcloud=False,
                disable_beatport=False
            )
            
            # Try Last.fm first for year and album (better metadata quality)
            try:
                lastfm_info = lastfm.track_info(artist, title)
                if lastfm_info.get("year"):
                    year_from_online = lastfm_info["year"]
                if lastfm_info.get("album"):
                    album_from_online = lastfm_info["album"]
            except Exception:
                pass
            
            # Fallback to Beatport if Last.fm didn't provide
            if not year_from_online or not album_from_online:
                try:
                    beatport_data = beatport.search_track(artist, title, duration_s=dur_s)
                    if beatport_data:
                        if not year_from_online:
                            release_date = beatport_data.get("release_date")
                            if release_date and release_date.strip():
                                year_from_online = release_date.split("-")[0]
                        if not album_from_online:
                            album_name = beatport_data.get("album")
                            if album_name and album_name.strip():
                                album_from_online = album_name
                except Exception:
                    pass
            
            if genre_res and genre_res.confidence >= 0.03:
                genres = [genre_res.main] + genre_res.subs[:2]
                genre_str = ", ".join(genres)
                sources = [src for src, _, _ in genre_res.breakdown]
                meta_source = f"genres({','.join(sources)})" if sources else "genres"
                # Prioritize online over tags (online has more context about releases)
                final_year = year_from_online if year_from_online else year_from_tags
                final_album = album_from_online if album_from_online else album_from_tags
                return {
                    "artist_suggest": artist,
                    "title_suggest": title,
                    "version_suggest": version,
                    "genre_suggest": genre_str,
                    "album_suggest": final_album,
                    "year_suggest": final_year,
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
            genre_fallback = "house"  # Generic electronic -> house
    
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
                if frd and frd.strip():
                    year = frd.split("-")[0]
                # Fallback: try to get date from first release if first-release-date not available
                if not year:
                    releases = ent.get("release-list", [])
                    if releases:
                        first_release_date = releases[0].get("date", "")
                        if first_release_date and first_release_date.strip():
                            year = first_release_date.split("-")[0]
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
            "release_group_id": match.release_group_id or "",  # For cover art fetching
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
    """Spróbuj wzbogacić metadane online (AcoustID + MusicBrainz + Beatport + Last.fm).
    Nie rusza BPM/Key. Zwraca uzupełnienia sugerowanych pól albo None.
    """
    # Use full suggest_metadata with enable_online=True
    # This includes AcoustID, MusicBrainz, and fallback to Beatport/Last.fm for year
    tags = {
        "fingerprint": row.get("fingerprint", ""),
        "duration": row.get("duration_suggest", ""),
        "artist": row.get("artist", ""),
        "title": row.get("title", ""),
        "genre": row.get("genre", ""),
    }
    
    return suggest_metadata(path, tags, enable_online=True)
    # b) z uproszczonym tytułem
    t2 = _clean_title(title)
    if t2 and t2 != title:
        out = lookup_musicbrainz(artist, t2)
        if out:
            genre_cur = (out.get("genre_suggest") or "").lower()
            if ("zeppelin" in (artist.lower() + " " + t2.lower())) and any(g in genre_cur for g in ["gospel","christian","worship"]):
                out["genre_suggest"] = "rock, hard rock"
            return out
    # c) jeśli mamy tagi w pliku — użyj ich
    try:
        tags = read_tags(path)
        a3 = (tags.get("artist") or "").strip()
        t3 = _clean_title((tags.get("title") or "").strip())
        if a3 or t3:
            out = lookup_musicbrainz(a3, t3)
            if out:
                genre_cur = (out.get("genre_suggest") or "").lower()
                if ("zeppelin" in (a3.lower() + " " + t3.lower())) and any(g in genre_cur for g in ["gospel","christian","worship"]):
                    out["genre_suggest"] = "rock, hard rock"
                return out
    except Exception:
        pass
    # d) sam tytuł (np. bootlegi bez artysty)
    if title and not artist:
        out = lookup_musicbrainz("", _clean_title(title))
        if out:
            genre_cur = (out.get("genre_suggest") or "").lower()
            if ("zeppelin" in _clean_title(title).lower()) and any(g in genre_cur for g in ["gospel","christian","worship"]):
                out["genre_suggest"] = "rock, hard rock"
            return out
    return None
