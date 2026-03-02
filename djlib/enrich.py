from __future__ import annotations
from pathlib import Path
from typing import Dict, Tuple
from djlib.filename import parse_from_filename, split_title_and_version
from djlib.tags import read_tags
import json
import logging
import os
import re
import unicodedata
from djlib.metadata import mb_client
from djlib.metadata.canonical_mb import lookup_canonical_release
from djlib.metadata import archive_org

logger = logging.getLogger(__name__)

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


def _normalize_title_from_canonical(local_title: str, canonical_title: str) -> Tuple[str, bool]:
    """
    Compare local title with MusicBrainz canonical title and normalize if appropriate.
    
    Normalization happens when:
    - Local title is a prefix of canonical (e.g., "Lady" vs "Lady (Hear Me Tonight)")
    - Canonical has additional parenthetical info that's NOT a version/remix
    
    Returns:
        Tuple of (normalized_title, was_normalized)
    
    Examples:
        "Lady" vs "Lady (Hear Me Tonight)" -> ("Lady (Hear Me Tonight)", True)
        "One Way or Another" vs "One Way or Another (Teenage Kicks)" -> normalized
        "Billie Jean" vs "Billie Jean (Live)" -> NOT normalized (Live is a version)
        "Song" vs "Song (Remix)" -> NOT normalized (Remix is a version)
    """
    local = (local_title or "").strip()
    canonical = (canonical_title or "").strip()
    
    if not local or not canonical:
        return local_title, False
    
    # If titles are identical (case-insensitive), no normalization needed
    if local.lower() == canonical.lower():
        return local_title, False
    
    # Check if local is a prefix of canonical
    # e.g., "Lady" is prefix of "Lady (Hear Me Tonight)"
    canonical_lower = canonical.lower()
    local_lower = local.lower()
    
    if not canonical_lower.startswith(local_lower):
        return local_title, False
    
    # Get the extra part from canonical
    extra = canonical[len(local):].strip()
    if not extra:
        return local_title, False
    
    # Check if extra part starts with parenthesis
    if not extra.startswith("("):
        return local_title, False
    
    # Extract content inside parentheses
    match = re.match(r"\(([^)]+)\)", extra)
    if not match:
        return local_title, False
    
    paren_content = match.group(1).lower()
    
    # DON'T normalize if parenthetical content is a version/remix indicator
    # These indicate different versions, not canonical title variants
    version_indicators = [
        "remix", "rework", "bootleg", "dub", "vip",
        "live", "acoustic", "unplugged", "concert",
        "radio edit", "extended", "club mix", "original mix",
        "remaster", "remastered", "version", "edit", "mix",
        "instrumental", "acapella", "a capella",
    ]
    
    for indicator in version_indicators:
        if indicator in paren_content:
            return local_title, False
    
    # Parenthetical content is NOT a version - it's part of the canonical title
    # e.g., "(Hear Me Tonight)", "(Teenage Kicks)" - these are title additions
    return canonical, True


def get_audio_duration(path: Path) -> int:
    """
    Get audio file duration in seconds using mutagen.
    
    Args:
        path: Path to audio file
    
    Returns:
        Duration in seconds (int) or 0 if failed
    """
    try:
        from mutagen import File
        audio = File(path)
        if audio and audio.info:
            return int(round(audio.info.length))
    except Exception:
        pass
    return 0


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


def _split_artist_title_from_combined(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Split combined 'Artist - Title' or 'Artist feat. X - Title' string into (artist, title).
    
    Handles patterns like:
    - "Major Lazer feat. Sean Paul - Come On To Me" → ("Major Lazer feat. Sean Paul", "Come On To Me")
    - "Seb Skalski - My Religion" → ("Seb Skalski", "My Religion")
    - "Just Title" → (None, None) - no split possible
    
    Returns:
        Tuple of (artist, title). If no dash found or invalid format, returns (None, None).
    """
    text = (text or "").strip()
    if not text:
        return None, None
    
    # Look for " - " separator (canonical artist-title split)
    # But NOT inside parentheses (e.g., "Title (Some - Mix)" should not split on inner dash)
    if " - " in text:
        # Find the first " - " that's not inside parentheses
        depth = 0
        for i, char in enumerate(text):
            if char == '(':
                depth += 1
            elif char == ')':
                depth = max(0, depth - 1)
            elif char == '-' and depth == 0:
                # Check if it's " - " pattern
                if i > 0 and i < len(text) - 1:
                    before = text[i-1:i]
                    after = text[i+1:i+2]
                    if before == ' ' and after == ' ':
                        artist_part = text[:i-1].strip()
                        title_part = text[i+2:].strip()
                        if artist_part and title_part:
                            return artist_part, title_part
    
    return None, None



# ---------------------------------------------------------------------------
# Sanitization / canonicalization helpers for derive_local_metadata
# ---------------------------------------------------------------------------

def _sanitize_artist(val: str) -> str:
    # Check special artists map first
    raw = (val or "").strip()
    if not raw:
        return ""
    
    # Remove track number prefix (e.g., "09. One Direction" -> "One Direction")
    # Pattern: digits followed by dot/dash and optional space at start
    raw = re.sub(r"^\d{1,3}[\.\-]\s*", "", raw)
    
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
    
    # Remove track number prefix (e.g., "66. Artist - Title" or "01. Title")
    # Pattern: digits followed by dot/dash and optional space at start
    s = re.sub(r"^\d{1,3}[\.\-]\s*", "", s)
    
    s = re.sub(r"\b(pobrano|pobrane)\s+z\b.*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\bdownloaded\s+from\b.*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\bwww\.[\w\.-]+\b", "", s, flags=re.IGNORECASE)
    # Remove website watermarks: [site.com], (site.com), | site.com, bare domain.tld
    s = re.sub(r"[\[\(]\s*\w+\.(?:com|net|org|pl|info|club|xyz|io)\s*[\]\)]", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*\|\s*\w+\.(?:com|net|org|pl|info|club|xyz|io)\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\b\w+\.(?:com|net|org|pl|info|club|xyz|io)\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\.(?:mp3|wav|flac|aiff|m4a|aac)$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+-\s+", " - ", s)
    s = re.sub(r"\s+", " ", s)
    s = s.strip()
    # Strip trailing punctuation that isn't part of valid titles
    s = re.sub(r'[,;]+$', '', s).strip()
    
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
    # Remove website watermarks: [site.com], (site.com), | site.com, bare domain.tld
    s = re.sub(r"[\[\(]\s*\w+\.(?:com|net|org|pl|info|club|xyz|io)\s*[\]\)]", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*\|\s*\w+\.(?:com|net|org|pl|info|club|xyz|io)\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\b\w+\.(?:com|net|org|pl|info|club|xyz|io)\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\.(?:mp3|wav|flac|aiff|m4a|aac)$", "", s, flags=re.IGNORECASE)
    # Strip junk "Version N" / "Ver N" / "V.N" tokens from download sites
    s = re.sub(r",?\s*\b(?:version|ver\.?|v\.?)\s*\d+\b", "", s, flags=re.IGNORECASE)
    # Clean leftover punctuation: trailing, leading, and doubled commas/semicolons
    s = re.sub(r"[,;]\s*[,;]", ",", s)  # collapse doubled separators
    s = re.sub(r"^[,;\s]+|[,;\s]+$", "", s)  # strip leading/trailing
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


def derive_local_metadata(path: Path, tags: Dict[str, str]) -> Tuple[str, str, str]:
    """Normalize and derive artist, title, version from audio tags and filename.

    Returns (artist, title, version) tuple with proper capitalization and cleanup.
    """
    # Get metadata from tags and filename
    pf_artist, pf_title, pf_version = parse_from_filename(path)
    
    artist = _sanitize_artist(tags.get("artist", ""))
    title = _sanitize_title(tags.get("title", ""))
    version = _sanitize_version(tags.get("version_info", ""))

    # CHECK: If title contains "Artist - Title" pattern (tracklist dump), split it
    # This handles cases like title="66. Major Lazer feat. Sean Paul - Come On To Me"
    # where the track number was stripped but artist-title combo remains
    # ONLY do this if artist is empty or artist is a PREFIX of title (not substring)
    should_split_title = False
    if title and " - " in title:
        if not artist:
            should_split_title = True
        elif artist and title.lower().startswith(artist.lower()):
            # Artist is prefix of title - likely duplicated
            should_split_title = True
    
    if should_split_title:
        extracted_artist, extracted_title = _split_artist_title_from_combined(title)
        if extracted_artist and extracted_title:
            # Use extracted values, apply proper sanitization
            if not artist:
                artist = _sanitize_artist(extracted_artist)
            title = _sanitize_title(extracted_title)

    # Normalize featuring info BEFORE splitting title/version
    # This prevents "(feat. Carol)" from being treated as version info
    artist, title = _normalize_features(artist, title)

    # Split title field to extract version info (handles "Title - Mix/Edit/etc" patterns)
    if title:
        title_split, version_split = split_title_and_version(title)
        if version_split:
            # Merge version from title with existing version (title takes precedence)
            title = title_split
            if version:
                # Combine both versions (from title split and from tag)
                version_parts = []
                for v in [version_split, version]:
                    v = v.strip()
                    if v and v not in version_parts:
                        version_parts.append(v)
                version = ", ".join(version_parts)
            else:
                version = version_split

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

    return artist.strip(), title.strip(), version.strip()


def suggest_metadata(path: Path, tags: Dict[str, str], enable_online: bool = True) -> Dict[str, str]:
    """Return proposed metadata for approval.

    Priority:
      1) AcoustID fingerprint -> MusicBrainz recording
      2) MusicBrainz search (originals & live only -- skipped for remixes)
      3) Genre resolver (Last.fm / SoundCloud / Beatport) + year/album
      4) Offline fallback: filename parsing + existing tags

    BPM and Key are preserved from the file (outside the scope of this function).

    Args:
        enable_online: If False, skip all online lookups (faster for scan).
        tags: Dict with metadata; may include ``skip_fingerprint: yes``.
    """
    artist, title, version = derive_local_metadata(path, tags)
    skip_fp = (tags.get("skip_fingerprint") or "").strip().lower() in {
        "yes", "y", "true", "1",
    }

    if not enable_online:
        return _offline_fallback(artist, title, version, tags)

    # ---- shared context for all online phases ----
    fp = tags.get("fingerprint", "")
    dur_sec = _parse_duration_tag(tags.get("duration", ""))
    if not dur_sec:
        dur_sec = get_audio_duration(path)
    file_album = tags.get("album", "")
    live = _is_live(version, file_album)

    # ---- Phase 1: AcoustID fingerprint ----
    if fp and dur_sec and not skip_fp:
        online = lookup_acoustid(fp, dur_sec, file_album_tag=file_album)
        if online and _acoustid_artist_matches(online, artist):
            # Preserve filename-derived version if online lacks it
            if version and not (online.get("version_suggest") or "").strip():
                online = {**online, "version_suggest": version}
            if live:
                _enrich_archive_org(online, artist, title, dur_sec)
                online.setdefault("version_suggest", "Live")
            return online

    # ---- Phase 2: MusicBrainz search (originals & live -- skip for remixes) ----
    # For remixes MB returns original-track data, not remix-specific info.
    # Exception: "Live" is not a remix.
    if not version or live:
        online = lookup_musicbrainz(artist, title)
        if online:
            if live and dur_sec:
                _enrich_archive_org(online, artist, title, dur_sec)
                online.setdefault("version_suggest", "Live")
            return online

    # ---- Phase 3: genre resolver (Last.fm / SoundCloud / Beatport) ----
    result = _resolve_via_genre_sources(artist, title, version, dur_sec, live, tags)
    if result is not None:
        return result

    # ---- Phase 4: offline fallback ----
    return _offline_fallback(artist, title, version, tags)


def _format_duration(ms: int | None) -> str:
    if not ms or ms <= 0:
        return ""
    s = int(round(ms/1000))
    m = s // 60
    r = s % 60
    return f"{m}:{r:02d}"


# ---------------------------------------------------------------------------
# Constants (P2 #14)
# ---------------------------------------------------------------------------

#: Minimum genre-resolver confidence to accept a result.
MIN_GENRE_CONFIDENCE = 0.03

#: Archive.org duration matching tolerance in seconds.
ARCHIVE_TOLERANCE_S = 1.0

#: MusicBrainz HTTP timeout in seconds.
MB_HTTP_TIMEOUT = 15

#: Minimum word length for AcoustID artist-similarity check.
_MIN_WORD_LEN_SIMILARITY = 3

#: Album-title keywords that indicate a compilation (not a studio release).
_COMPILATION_KEYWORDS = frozenset([
    "greatest hits", "best of", "the best", "collection",
    "anthology", "ultimate", "essential", "gold", "platinum",
])

#: Version/album keywords that indicate a live recording.
_LIVE_KEYWORDS = ("live", "concert", "unplugged", "ao vivo", "in concert")


# ---------------------------------------------------------------------------
# Shared helpers — DRY replacements (P1 #5-#7)
# ---------------------------------------------------------------------------

def _is_live(version: str, album: str = "") -> bool:
    """Return True if version or album indicates a live recording."""
    v = (version or "").lower()
    a = (album or "").lower()
    for kw in _LIVE_KEYWORDS:
        pat = rf"\b{re.escape(kw)}\b"
        if re.search(pat, v) or re.search(pat, a):
            return True
    return False


def _is_compilation_album(album_title: str) -> bool:
    """Return True if *album_title* looks like a compilation / best-of."""
    low = (album_title or "").lower()
    return any(kw in low for kw in _COMPILATION_KEYWORDS)


def _enrich_archive_org(
    result: Dict[str, str],
    artist: str,
    title: str,
    duration_seconds: float | int | None,
) -> None:
    """Search Archive.org for a live recording and merge into *result* in-place."""
    if not duration_seconds or not artist or not title:
        return
    try:
        archive_rec = archive_org.search_by_artist_title_duration(
            artist=artist,
            title=title,
            duration_seconds=float(duration_seconds),
            tolerance_seconds=ARCHIVE_TOLERANCE_S,
        )
        if archive_rec:
            logger.debug(f"Archive.org match: {archive_rec.title}")
            result["archive_org_identifier"] = archive_rec.identifier
            if archive_rec.cover_url:
                result["archive_org_cover_url"] = archive_rec.cover_url
            if archive_rec.title and not result.get("album_suggest"):
                result["album_suggest"] = archive_rec.title
            if archive_rec.year and not result.get("year_suggest"):
                result["year_suggest"] = str(archive_rec.year)
    except Exception as exc:
        logger.debug(f"Archive.org lookup failed: {exc}")


def _resolve_first_release(
    recording_mbid: str | None,
    artist: str,
    title: str,
) -> Dict[str, str]:
    """Try canonical MB dump then API to find earliest studio release.

    Returns dict with ``original_*`` keys (may be empty).
    """
    # 1. Canonical offline dump (instant)
    try:
        canonical = lookup_canonical_release(artist, title, fetch_year=True)
        if canonical and not _is_compilation_album(canonical["album_title"]):
            data: Dict[str, str] = {
                "original_album_title": canonical["album_title"],
                "original_release_mbid": canonical["release_mbid"],
                "recording_mbid": canonical["recording_mbid"],
            }
            if "release_year" in canonical:
                data["original_release_year"] = canonical["release_year"]
            return data
        if canonical:
            logger.debug(f"Rejecting canonical compilation: {canonical['album_title']}")
    except Exception:
        pass

    # 2. API fallback
    if recording_mbid or (artist and title):
        try:
            fr = mb_client.mb_fetch_first_release_for_recording(
                recording_mbid or "", artist, title,
            )
            if fr:
                return {
                    "original_album_title": fr.album_title,
                    "original_release_date": fr.original_release_date,
                    "original_release_year": str(fr.original_release_year),
                    "original_release_mbid": fr.release_mbid,
                    "original_release_group_mbid": fr.release_group_mbid,
                    "original_release_category": fr.release_category,
                    "original_release_source": fr.source,
                }
        except Exception:
            pass
    return {}


def _parse_duration_tag(dur_txt: str) -> int:
    """Parse 'm:ss' string → seconds, or return 0."""
    try:
        if ":" in dur_txt:
            m, s = dur_txt.split(":", 1)
            return int(m) * 60 + int(s)
    except Exception:
        pass
    return 0


def _acoustid_artist_matches(online: Dict[str, str], tags_artist: str) -> bool:
    """Return True when AcoustID artist is compatible with file tags artist.

    A match requires at least one common word (≥ ``_MIN_WORD_LEN_SIMILARITY``
    chars).  When either side is empty the check is skipped (trust AcoustID).
    """
    a_online = (online.get("artist_suggest") or "").lower().strip()
    a_tags = tags_artist.lower().strip()
    if not a_online or not a_tags:
        return True  # can't verify — trust it
    online_words = {w for w in a_online.replace(",", " ").split()
                    if len(w) >= _MIN_WORD_LEN_SIMILARITY}
    tags_words = {w for w in a_tags.replace(",", " ").split()
                  if len(w) >= _MIN_WORD_LEN_SIMILARITY}
    if online_words & tags_words:
        return True
    logger.warning(
        "AcoustID mismatch: tags='%s' vs fingerprint='%s' — using tags",
        tags_artist, online.get("artist_suggest"),
    )
    return False


def _offline_fallback(
    artist: str, title: str, version: str, tags: Dict[str, str],
) -> Dict[str, str]:
    """Return tag / filename-based metadata when online sources fail."""
    return {
        "artist_suggest": artist,
        "title_suggest": title,
        "version_suggest": version,
        "genre_suggest": (tags.get("genre") or "").strip(),
        "album_suggest": "",
        "year_suggest": "",
        "duration_suggest": "",
        "meta_source": "filename|tags_fallback",
    }


def _resolve_via_genre_sources(
    artist: str,
    title: str,
    version: str,
    dur_sec: int,
    live: bool,
    tags: Dict[str, str],
) -> Dict[str, str] | None:
    """Phase 3 of suggest_metadata: genre resolver + online year/album.

    Combines Last.fm, SoundCloud, Beatport and MusicBrainz release-group
    data to produce genre, year and album suggestions.

    Returns a result dict or *None* when genre confidence is too low.
    """
    year_from_tags = tags.get("year", "").strip()
    album_from_tags = tags.get("album", "").strip()
    year_online = ""
    album_online = ""
    release_group_id = None

    # For originals: use MusicBrainz release-group for first-release date + album
    if not version:
        try:
            mb_info = mb_client.get_original_release_info(artist, title)
            if mb_info:
                year_online, album_online, release_group_id = mb_info
        except Exception:
            pass

    try:
        from djlib.metadata.genre_resolver import resolve as resolve_genres, ALL_SOURCES
        from djlib.metadata import lastfm, beatport

        dur_s = dur_sec or None
        is_remix = bool(version and not live)
        genre_sources = set(ALL_SOURCES)
        if is_remix:
            genre_sources.discard("mb")

        genre_res = resolve_genres(
            artist, title, version=version, duration_s=dur_s,
            sources=genre_sources,
            tag_genre=(tags.get("genre") or "").strip(),
        )

        # ---- year / album resolution ----
        if version:
            # Remix: try Beatport for specific remix release date
            try:
                bp_data = beatport.search_track(
                    artist, title, duration_s=dur_s, version=version,
                )
                if bp_data:
                    # Beatport's search_track already validates version
                    # match (version_score > 0) before returning a result,
                    # so if bp_data is not None the match is acceptable.
                    rd = bp_data.get("release_date", "")
                    if rd and rd.strip():
                        year_online = rd.split("-")[0]
                    alb = bp_data.get("release_name", "") or bp_data.get("album", "")
                    if alb and alb.strip():
                        album_online = alb
                    # Extract artist from Beatport when our parsed artist
                    # is empty (e.g. filename without "Artist - Title" pattern).
                    if not artist.strip() and bp_data.get("artist"):
                        artist = bp_data["artist"]
            except Exception:
                pass

            # Fallback: SoundCloud upload year (captured during genre fetch
            # — no extra API call, uses _sc_year_cache).
            if not year_online:
                try:
                    from djlib.metadata import soundcloud
                    sc_year = soundcloud.get_cached_year(artist, title, version)
                    if not sc_year:
                        # Cache miss (genre fetch not called yet?) → full lookup
                        sc_year = soundcloud.get_track_year(artist, title, version)
                    if sc_year:
                        year_online = sc_year
                except Exception:
                    pass

        # Last.fm for year/album (skip year for remixes — returns original year)
        if not year_online or not album_online:
            try:
                lf = lastfm.track_info(artist, title)
                if not year_online and lf.get("year") and not version:
                    year_online = lf["year"]
                if not album_online and lf.get("album"):
                    album_online = lf["album"]
            except Exception:
                pass

        # Beatport fallback for originals
        if not version and (not year_online or not album_online):
            try:
                bp = beatport.search_track(artist, title, duration_s=dur_s)
                if bp:
                    if not year_online:
                        rd = bp.get("release_date", "")
                        if rd and rd.strip():
                            year_online = rd.split("-")[0]
                    if not album_online:
                        an = bp.get("album", "")
                        if an and an.strip():
                            album_online = an
            except Exception:
                pass

        # ---- Build result if genre confidence is high enough ----
        if genre_res and genre_res.confidence >= MIN_GENRE_CONFIDENCE:
            genres = [genre_res.main] + genre_res.subs[:2]
            src_names = [s.source for s in genre_res.breakdown]
            meta_source = f"genres({','.join(src_names)})" if src_names else "genres"
            final_year = year_online or year_from_tags
            final_album = album_online or album_from_tags

            result: Dict[str, str] = {
                "artist_suggest": artist,
                "title_suggest": title,
                "version_suggest": version,
                "genre_suggest": ", ".join(genres),
                "album_suggest": final_album,
                "year_suggest": final_year,
                "duration_suggest": "",
                "meta_source": meta_source,
            }
            if release_group_id:
                result["release_group_id"] = release_group_id

            if live and dur_sec:
                _enrich_archive_org(result, artist, title, dur_sec)

            return result
    except Exception:
        pass

    return None


def _clean_title(t: str) -> str:
    """Uprość tytuł do wyszukiwania: usuń nawiasy, 'feat.', 'ft.', itp., podwójne spacje.
    Nie jest destrukcyjne dla oryginalnych danych — tylko dla zapytania.
    """
    s = (t or "").strip()
    if not s:
        return s
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
    
    Strategy:
    1. Try Canonical MusicBrainz Data lookup (instant, no API calls)
    2. Fallback to live MusicBrainz API search
    3. Normalize title if local is prefix of canonical (e.g., "Lady" -> "Lady (Hear Me Tonight)")
    """
    artist = (artist or "").strip()
    original_title = (title or "").strip()  # Keep original for comparison
    title = original_title
    if not title and not artist:
        return None
    
    # Step 1: Try canonical lookup first (offline, instant)
    canonical_data = None
    try:
        canonical_data = lookup_canonical_release(artist, title, fetch_year=True)
        if canonical_data:
            if _is_compilation_album(canonical_data['album_title']):
                logger.debug(f"Rejecting canonical compilation: {canonical_data['album_title']}")
                canonical_data = None
            else:
                # Got canonical data - prioritize it!
                # Try to get MB title for normalization (canonical lookup uses recording name)
                canonical_title = title  # Default to input title
                was_normalized = False
                try:
                    match = mb_client.search_recording(artist, title)
                    if match and match.title:
                        normalized_title, was_normalized = _normalize_title_from_canonical(original_title, match.title)
                        if was_normalized:
                            canonical_title = normalized_title
                except Exception:
                    pass
                
                result = {
                    "artist_suggest": canonical_data['artist_name'],
                    "title_suggest": canonical_title,
                    "album_suggest": canonical_data['album_title'],
                    "original_album_title": canonical_data['album_title'],
                    "original_release_mbid": canonical_data['release_mbid'],
                    "recording_mbid": canonical_data['recording_mbid'],
                    "meta_source": "musicbrainz_canonical",
                    "title_normalized": "yes" if was_normalized else "",
                }
                # Add year if available
                if 'release_year' in canonical_data:
                    result['year_suggest'] = canonical_data['release_year']
                    result['original_release_year'] = canonical_data['release_year']
                
                # Still fetch genres from API (canonical doesn't have genres)
                try:
                    match = mb_client.search_recording(artist, title)
                    if match:
                        genres = mb_client.get_recording_genres(
                            match.recording_id, 
                            release_group_id=match.release_group_id, 
                            artist_id=match.artist_id
                        )
                        if genres:
                            result['genre_suggest'] = genres[0]
                except Exception:
                    pass
                
                return result
    except Exception as e:
        # Canonical lookup failed - continue to API
        logger.debug(f"Canonical lookup failed: {e}")
        canonical_data = None
    
    # Step 2: Fallback to live MusicBrainz API
    try:
        match = mb_client.search_recording(artist, title)
        if not match:
            return None
        # podstawowe pola
        out_artist = match.artist_credit or artist
        out_title = match.title or title
        duration = _format_duration(match.length_ms) if isinstance(match.length_ms, int) else ""

        # album i rok – najpierw spróbuj z release-group search (najbardziej wiarygodne dla roku)
        album = ""
        year = ""
        release_group_id_filtered = None  # Filtered RG ID (studio releases only)
        
        try:
            # get_original_release_info filters out Live/Compilation, returns (year, album, release_group_id)
            mb_info = mb_client.get_original_release_info(out_artist, out_title)
            if mb_info:
                year_from_rg_search, album_from_rg_search, rg_id_from_search = mb_info
                if year_from_rg_search:
                    year = year_from_rg_search
                if album_from_rg_search:
                    album = album_from_rg_search
                if rg_id_from_search:
                    release_group_id_filtered = rg_id_from_search
        except Exception:
            pass
        
        # If get_original_release_info didn't find anything, check if match.release_group_id is studio type
        if not release_group_id_filtered and match.release_group_id:
            try:
                rg_type = mb_client.get_release_group_type(match.release_group_id)
                # Accept only studio releases (Album, Single, EP) - reject Live, Compilation, etc.
                if rg_type in ["Album", "Single", "EP"]:
                    release_group_id_filtered = match.release_group_id
            except Exception:
                pass
        
        # Jeśli nie udało się przez RG search, spróbuj z release-group-id z recording
        if not year and match.release_group_id:
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
        
        # Pobierz album z RG jeśli jeszcze nie mamy
        if not album and match.release_group_id:
            try:
                rg = mb_client._get_release_group_by_id(match.release_group_id)
                ent = (rg or {}).get("release-group", {})
                album = ent.get("title", "")
            except Exception:
                pass

        # gatunki: recording → release-group → artist
        genres = mb_client.get_recording_genres(match.recording_id, release_group_id=match.release_group_id, artist_id=match.artist_id)
        genre = genres[0] if genres else ""

        # Resolve canonical first release (canonical dump -> API fallback)
        first_release_data = _resolve_first_release(
            match.recording_id, out_artist, out_title,
        )

        # Check if MB title is more complete than local (e.g., "Lady" vs "Lady (Hear Me Tonight)")
        normalized_title, was_normalized = _normalize_title_from_canonical(original_title, out_title)
        final_title = normalized_title if was_normalized else out_title

        result = {
            "artist_suggest": out_artist,
            "title_suggest": final_title,
            "version_suggest": "",
            "genre_suggest": genre,
            "album_suggest": album,
            "year_suggest": year,
            "duration_suggest": duration,
            "meta_source": "musicbrainz",
            "release_group_id": release_group_id_filtered or match.release_group_id or "",  # Prefer filtered (studio) over raw recording RG
            "title_normalized": "yes" if was_normalized else "",
        }
        
        # Merge first release data (non-invasive, only adds new fields)
        result.update(first_release_data)
        
        return result
    except Exception:
        return None

def lookup_acoustid(fp: str, duration_sec: int, file_album_tag: str = "") -> Dict[str, str] | None:
    """Lookup przez AcoustID (wymaga Application API key) → MusicBrainz recording → metadane.
    Używa pyacoustid.lookup + parse_lookup_result zgodnie z dokumentacją.
    Zwraca słownik suggest_* albo None.
    
    Args:
        fp: AcoustID fingerprint
        duration_sec: Duration in seconds
        file_album_tag: Album tag from file (for live detection when MB data incomplete)
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
        r = requests.get(url, params=params, headers=headers, timeout=MB_HTTP_TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return None
        rec = r.json()
        out_artist = mb_client._join_artist_credit(rec.get("artist-credit") or []) or best_artist
        out_title = rec.get("title") or best_title
        releases = rec.get("releases") or []
        album = releases[0].get("title") if releases else ""
        
        # Get original release year using release-group search (more reliable)
        year = ""
        try:
            year_from_rg = mb_client.get_original_release_year(out_artist, out_title)
            if year_from_rg:
                year = year_from_rg
        except Exception:
            pass
        
        # Fallback: use first release date if RG search didn't work
        if not year:
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
        
        # NEW: Try to resolve canonical first release from recording MBID
        first_release_data = {}
        canonical_data = None
        version_info = ""  # Initialize version_info early
        
        # Detect if this is a live recording FIRST (before canonical/API lookup)
        # Check release-group type and album title
        is_live_recording = False
        is_compilation = False
        rgid = None
        try:
            # Extract RG ID from releases (not from top-level release-group)
            if releases:
                rgid = releases[0].get("release-group", {}).get("id") or None
            if rgid:
                # Check RG type for Live/Broadcast/Compilation
                rg_data = mb_client._get_release_group_by_id(rgid)
                rg = rg_data.get("release-group", {})
                primary_type = rg.get("primary-type", "")
                secondary_types = rg.get("secondary-type-list", [])
                
                # Check for compilation FIRST - if it's a compilation, skip it entirely
                if "Compilation" in secondary_types:
                    is_compilation = True
                elif "Live" in secondary_types or primary_type in ["Live", "Broadcast"]:
                    is_live_recording = True
        except Exception:
            pass
        
        # Also check album title for live indicators
        if not is_live_recording:
            album_lower = album.lower()
            if any(keyword in album_lower for keyword in ['live', 'concert', 'unplugged']):
                is_live_recording = True
        
        # Fallback: check file album tag if MusicBrainz has no RG data
        live_from_file_tag = False
        if not is_live_recording and not is_compilation and file_album_tag:
            file_album_lower = file_album_tag.lower()
            if any(keyword in file_album_lower for keyword in ['live', 'concert', 'unplugged', 'ao vivo']):
                is_live_recording = True
                live_from_file_tag = True  # Remember we detected live from file tag, not MB
        
        # If RG is a compilation, SKIP it entirely and look for canonical/API data instead
        if is_compilation:
            logger.debug(f"Skipping compilation RG: {rgid}")
            rgid = None  # Clear RG ID so we don't use it
        
        # If this is a live recording (and NOT compilation), DON'T try to find studio album
        # Use the live album as-is
        if is_live_recording and not is_compilation:
            version_info = "Live"
            # If live detected from file tag (not MB RG), search for live album in MB
            if live_from_file_tag and file_album_tag:
                # Try to find live album/release-group in MusicBrainz
                live_rg_found = False
                try:
                    import musicbrainzngs
                    # Search for live album: artist + file album tag or title + "live"
                    search_result = musicbrainzngs.search_release_groups(
                        artist=out_artist,
                        releasegroup=file_album_tag,
                        limit=10
                    )
                    
                    # Find first RG that is Live/Broadcast type
                    for rg in search_result.get('release-group-list', []):
                        rg_primary = rg.get('primary-type', '')
                        rg_secondary = rg.get('secondary-type-list', [])
                        rg_id = rg.get('id', '')
                        rg_title = rg.get('title', '')
                        rg_date = rg.get('first-release-date', '')
                        
                        # Check if it's Live/Broadcast and NOT compilation
                        is_live_rg = (rg_primary in ['Live', 'Broadcast'] or 'Live' in rg_secondary)
                        is_comp_rg = 'Compilation' in rg_secondary
                        
                        if is_live_rg and not is_comp_rg:
                            album = rg_title
                            if rg_date:
                                year = rg_date[:4]  # Extract year
                            first_release_data = {
                                "original_release_group_mbid": rg_id,
                            }
                            if rg_date:
                                first_release_data["original_release_date"] = rg_date
                                first_release_data["original_release_year"] = year
                            live_rg_found = True
                            logger.debug(f"Found live RG from file album tag: {rg_title} ({rg_id})")
                            break
                except Exception as e:
                    logger.debug(f"Failed to search live RG: {e}")
                
                # If no live RG found in MB, use file album tag as-is
                if not live_rg_found:
                    album = file_album_tag
            elif rgid:
                # Live detected from MB RG - keep RG info
                first_release_data = {
                    "original_release_group_mbid": rgid,
                }
        else:
            # Not live — resolve original studio release (canonical dump → API)
            first_release_data = _resolve_first_release(best_id, out_artist, out_title)
            if first_release_data:
                if first_release_data.get("original_album_title"):
                    album = first_release_data["original_album_title"]
                if first_release_data.get("original_release_year"):
                    year = first_release_data["original_release_year"]
        
        result = {
            "artist_suggest": out_artist,
            "title_suggest": out_title,
            "version_suggest": version_info,
            "genre_suggest": genre,
            "album_suggest": album,
            "year_suggest": year,
            "duration_suggest": duration,
            "meta_source": "acoustid+musicbrainz",
            "recording_mbid": best_id,  # CRITICAL: Save recording MBID for debugging
        }
        # Add release_group_id for cover art if available
        if rgid:
            result["release_group_id"] = rgid
        
        # Merge first release data
        result.update(first_release_data)
        
        # Archive.org for live recordings (uses function param duration_sec directly)
        if is_live_recording:
            _enrich_archive_org(result, out_artist, out_title, duration_sec)
        
        return result
    except Exception:
        return None


def enrich_online_for_row(path: Path, row: Dict[str, str]) -> Dict[str, str] | None:
    """Enrich metadata online (AcoustID + MusicBrainz + Beatport + Last.fm).

    Preserves BPM/Key.  Returns suggested field updates or None.
    """
    # Read album tag via shared utility (avoids extra mutagen open)
    file_tags = read_tags(path) or {}
    file_album = file_tags.get("album", "")

    tags = {
        "fingerprint": row.get("fingerprint", ""),
        "duration": row.get("duration_suggest", ""),
        "artist": row.get("artist", ""),
        "title": row.get("title", ""),
        "genre": row.get("genre", ""),
        "version_info": row.get("version_suggest", ""),
        "album": file_album,
    }

    return suggest_metadata(path, tags, enable_online=True)
