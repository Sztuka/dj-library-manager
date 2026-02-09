from __future__ import annotations
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

log = logging.getLogger(__name__)

# Ensure requests-cache side effects
import djlib.metadata  # noqa: F401

from . import mb_client
from . import lastfm
from .soundcloud import track_tags as sc_track_tags


# ============================================================================
# GENRES.YML - single source of truth for taxonomy
# ============================================================================

_GENRES_FILE = Path(__file__).resolve().parents[2] / "genres.yml"


def _load_genres_yml() -> Dict:
    """Load genres.yml once at import time."""
    try:
        with _GENRES_FILE.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        log.warning("Failed to load genres.yml: %s", e)
        return {}


_GENRES_DATA = _load_genres_yml()


def _build_electronic_genres() -> frozenset[str]:
    """Build set of electronic genre synonyms from genres.yml category=electronic."""
    result = set()
    for _key, info in _GENRES_DATA.items():
        if not isinstance(info, dict):
            continue
        if info.get("category") != "electronic":
            continue
        for syn in info.get("synonyms", []):
            result.add(syn.strip().lower())
    return frozenset(result)


def _build_specificity_boost() -> Dict[str, float]:
    """Build specificity boost map from genres.yml boost field.
    
    For each genre with boost != 1.0, maps every synonym (normalized)
    to that boost value. This lets subgenres beat their parent genres
    in weighted scoring.
    """
    result: Dict[str, float] = {}
    for _key, info in _GENRES_DATA.items():
        if not isinstance(info, dict):
            continue
        boost = info.get("boost", 1.0)
        if not isinstance(boost, (int, float)) or boost == 1.0:
            continue
        for syn in info.get("synonyms", []):
            n = _norm(syn)
            if n:
                result[n] = float(boost)
        # Also map the label
        label = info.get("label", "")
        if label:
            result[_norm(label)] = float(boost)
    return result


def _norm(tag: str) -> str:
    t = (tag or "").strip().lower()
    t = t.replace("_", " ").replace("-", " ")
    t = " ".join(t.split())
    return t


# Aliases: additional mappings not captured by genres.yml synonyms.
# These handle compound / shorthand tags from Beatport/MB/LFM.
ALIASES = {
    "edm": "electronic",
    "tech-house": "tech house",
    "techno house": "tech house",
    "d n b": "drum and bass",
    "d&b": "drum and bass",
    # Beatport compound genre names
    "nu disco / disco": "nu disco",  # Beatport ID 50
}


def canonical(tag: str) -> str:
    """Normalize and resolve aliases for a genre tag."""
    t = _norm(tag)
    return ALIASES.get(t, t)


# Non-genre noise patterns occasionally present in MB/LFM tags
_NOISE_TERMS = {
    "offizielle charts",  # german charts label
    "offizielle",
    "charts",
    "chart",
    "ph temp checken",
    "favourite", "favorite", "favorites",
    "seen live",
    "plattentests.de",
    "germany", "deutschland",
    # newly filtered buzz / generic popularity tokens and year/season fluff
    "viral", "trending", "new", "new music", "summer mix", "summer", "remixes", "mix",
    # project-specific: do not use 'folk indie' at all
    "folk indie",
    # geographic locations (not genres)
    "puerto rico", "puerto rican", "jamaica", "jamaica dancehall",
    "united states", "usa", "uk", "united kingdom", "brazil", "brasil",
    # garbage/noise tokens from MB/LFM
    "merge", "various", "compilation", "soundtrack", "ost",
}

# Pre-compiled regex patterns for noise detection
_RE_WOCHEN = re.compile(r"\b\d+\s*[–-]?\s*\d*\s*wochen\b")
_RE_YEAR_ONLY = re.compile(r"20[0-3][0-9]$")
_RE_CONTAINS_YEAR = re.compile(r"20[0-3][0-9]")


def _is_noise(tag: str) -> bool:
    """Return True if tag is a non-genre noise term (charts, dates, etc.)."""
    t = _norm(tag)
    if not t:
        return True
    if t in _NOISE_TERMS:
        return True
    # domain-like tokens
    if "." in t and not t.replace(".", "").isalpha():
        return True
    # '1–4 wochen' / '1-4 wochen' etc.
    if _RE_WOCHEN.search(t):
        return True
    # very short or purely numeric
    if len(t) <= 2 or t.isdigit():
        return True
    # Year-only tags (2023, 2024, etc.) or tokens ending with year markers
    if _RE_YEAR_ONLY.match(t):
        return True
    if _RE_CONTAINS_YEAR.search(t) and len(t.split()) == 1:
        return True
    return False


def _downweight_factor(tag: str) -> float:
    """Return a multiplicative factor (0..1] to reduce influence of overly broad tags.

    Only penalize truly problematic broad tags like folk/indie combos.
    Generic parent genres (rock, pop, electronic) keep weight 1.0 - 
    they can still win over false positives, but lose to specific subgenres via boost.
    """
    t = _norm(tag)
    if not t:
        return 1.0
    if t == "folk":
        return 0.30
    # Indie is OK; only penalize the folk+indie combo (any order)
    if t in {"indie folk"}:  # 'folk indie' is fully ignored by _NOISE_TERMS
        return 0.40
    if t in {"alternative", "alternative rock"}:
        return 0.60
    # NOTE: Generic parent genres (rock, pop, electronic, dance) 
    # are NOT downweighted - they keep 1.0 to beat false positives.
    # Specific subgenres win via _specificity_boost() instead.
    return 1.0


# Specificity boost map: derived from genres.yml `boost` field at import time.
# Subgenres with boost > 1.0 beat their parent genre in weighted scoring.
_SPECIFIC_GENRE_BOOST = _build_specificity_boost()


def _specificity_boost(tag: str) -> float:
    """Return boost factor for specific subgenres over generic parents."""
    t = _norm(tag)
    return _SPECIFIC_GENRE_BOOST.get(t, 1.0)


# Beatport-authoritative electronic genres: derived from genres.yml category=electronic.
# If Beatport returns one of these, trust it over Last.fm/MB.
BEATPORT_ELECTRONIC_GENRES = _build_electronic_genres()

# Weight constants for genre scoring
# These values are tuned to balance different sources' reliability
WEIGHT_BEATPORT_BASE = 10.0
WEIGHT_BEATPORT_REMIX = 8.0
WEIGHT_BEATPORT_ELECTRONIC = 25.0      # When BP returns specific EDM genre
WEIGHT_BEATPORT_ELECTRONIC_REMIX = 15.0
WEIGHT_LASTFM_BASE = 6.0
WEIGHT_LASTFM_WHEN_BP_ELECTRONIC = 2.0  # Reduced to let Beatport dominate for EDM
WEIGHT_LASTFM_REMIX = 0.5
WEIGHT_MB_BASE = 3.0
WEIGHT_MB_REMIX = 1.5
WEIGHT_SC_BASE = 2.0
WEIGHT_SC_REMIX = 20.0  # High for remixes: SC often has remix-specific genre tags


def _is_beatport_electronic(genre: str) -> bool:
    """Check if Beatport genre is a specific electronic genre (not generic Dance/Pop)."""
    g = _norm(genre)
    # Direct match
    if g in BEATPORT_ELECTRONIC_GENRES:
        return True
    # Partial match for compound genres like "Techno (Peak Time / Driving)"
    for eg in BEATPORT_ELECTRONIC_GENRES:
        if eg in g or g.startswith(eg):
            return True
    return False


# ============================================================================
# FETCH HELPERS - Used for parallel API calls
# ============================================================================

def _fetch_beatport(artist: str, title: str, duration_s: Optional[int], version: str = "") -> Optional[Dict[str, object]]:
    """Fetch genre data from Beatport. Returns dict with 'genre' key or None."""
    try:
        from djlib.metadata.beatport import search_track as bp_search
        result = bp_search(artist, title, duration_s, version=version)
        log.debug("Beatport: %s - %s (%s) → %s", artist, title, version or "original", result.get('genre') if result else None)
        return result
    except Exception as e:
        log.warning("Beatport fetch failed for %s - %s: %s", artist, title, e)
        return None


def _fetch_lastfm(artist: str, title: str) -> Optional[List[Tuple[str, int]]]:
    """Fetch top tags from Last.fm. Returns list of (tag_name, count) or None."""
    try:
        result = lastfm.top_tags(artist, title)
        log.debug("Last.fm: %s - %s → %d tags", artist, title, len(result) if result else 0)
        return result
    except Exception as e:
        log.warning("Last.fm fetch failed for %s - %s: %s", artist, title, e)
        return None


def _fetch_soundcloud(artist: str, title: str, version: str) -> Optional[Dict[str, object]]:
    """Fetch tags from SoundCloud. Returns dict with 'tags' key or None."""
    try:
        result = sc_track_tags(artist, title, version)
        tags = result.get('tags', []) if result else []
        log.debug("SoundCloud: %s - %s (%s) → %d tags", artist, title, version, len(tags))
        return result
    except Exception as e:
        log.warning("SoundCloud fetch failed for %s - %s: %s", artist, title, e)
        return None


def _fetch_musicbrainz(artist: str, title: str, duration_s: Optional[int], 
                       mb_recording: Optional["mb_client.RecordingMatch"]) -> Optional[List[str]]:
    """Fetch genres from MusicBrainz. Returns list of genre strings or None."""
    try:
        rec = mb_recording if mb_recording else mb_client.search_recording(artist, title, duration=duration_s)
        if rec:
            genres = mb_client.get_recording_genres(
                rec.recording_id, 
                release_group_id=rec.release_group_id, 
                artist_id=rec.artist_id
            )
            log.debug("MusicBrainz: %s - %s → %s", artist, title, genres[:3] if genres else None)
            return genres
    except Exception as e:
        log.warning("MusicBrainz fetch failed for %s - %s: %s", artist, title, e)
    return None


def _score_tag(tag: str, base_weight: float, scores: Dict[str, float], local: Dict[str, float]) -> None:
    """Score a single tag and update scores dict. Applies canonical, noise filter, and boosts.
    
    Args:
        tag: Raw tag string from source
        base_weight: Base weight for this source (e.g., WEIGHT_BEATPORT_BASE)
        scores: Global scores dict to update
        local: Local scores dict for this source to update
    """
    c = canonical(tag)
    if _is_noise(c):
        return
    f = _downweight_factor(c) * _specificity_boost(c)
    w = base_weight * f
    if w <= 0:
        return
    scores[c] = scores.get(c, 0.0) + w
    local[c] = local.get(c, 0.0) + w


# Confidence threshold for Beatport early exit
# If Beatport returns specific EDM genre, skip other sources (they're less reliable for EDM)
BEATPORT_EARLY_EXIT_CONFIDENCE = 0.8

# Keywords that indicate a TRUE remix (another artist reworked the track)
_REMIX_KEYWORDS = frozenset(["remix", "rework", "bootleg", "mashup", "dub mix", "vip mix", "vip edit"])
# Edit types that are NOT remixes (same artist, different version)
_NON_REMIX_EDITS = frozenset([
    "radio edit", "original edit", "extended edit", "club edit", 
    "single edit", "album edit", "short edit"
])


@dataclass
class GenreResolution:
    main: str
    subs: List[str]
    confidence: float
    breakdown: List[Tuple[str, float, Dict[str, float]]]


def resolve(artist: str, title: str, version: str = "", *, duration_s: int | None = None, disable_soundcloud: bool = False, disable_beatport: bool = False, disable_mb: bool = False, mb_recording: "mb_client.RecordingMatch | None" = None) -> GenreResolution | None:
    """Resolve genres using Beatport -> Last.fm -> MB -> SoundCloud with scoring.

    Fetches genre data from multiple sources and aggregates scores.
    Early exit when Beatport returns confident EDM match (skips scoring other sources).

    Version info (remix names) helps SoundCloud queries disambiguate edits.
    Weights (relative): Beatport=10, LFM=6, MB=3, SC=2 (base).
    For remixes (version provided): SC and BP weights increased, LFM/MB decreased.
    Returns main + up to 2 subs.
    
    Args:
        mb_recording: Pre-fetched MusicBrainz RecordingMatch to avoid redundant API calls.
                      If provided, skips search_recording call.
        disable_mb: Skip MusicBrainz lookups entirely (useful for remixes where MB data is 
                    misleading - returns original track, not remix-specific info).
    """
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist and not title:
        return None

    # Detect if this is a TRUE remix (another artist/producer reworked it)
    # Default is NOT remix. Only explicit remix keywords make it a remix.
    # This prevents "(Hear Me Tonight)", "(Remastered)", "(Version 1)" from being treated as remixes.
    is_remix = False
    version_lower = (version or "").strip().lower()
    if version_lower:
        is_remix = any(kw in version_lower for kw in _REMIX_KEYWORDS)
        # Producer edits: "X edit" where X is not radio/original/extended/club/single
        # e.g. "City Boys Edit", "Merchant Edit" should be treated as remixes
        if not is_remix and "edit" in version_lower:
            if not any(ne in version_lower for ne in _NON_REMIX_EDITS):
                is_remix = True
    
    # Fallback: if version is empty, check title and artist for remix keywords
    # This handles cases where filename parsing swaps artist/title or misses version
    if not is_remix and not version_lower:
        title_lower = (title or "").lower()
        artist_lower = (artist or "").lower()
        is_remix = any(kw in title_lower or kw in artist_lower for kw in _REMIX_KEYWORDS)
    
    scores: Dict[str, float] = {}
    parts: List[Tuple[str, float, Dict[str, float]]] = []

    # =========================================================================
    # Sequential API calls (parallelization causes segfaults)
    # 
    # Attempted parallelization with ThreadPoolExecutor (both SQLite and filesystem
    # cache backends) resulted in segmentation faults. Root causes:
    # - Playwright (Beatport token) is not thread-safe
    # - requests_cache filesystem backend has internal threading issues
    # - Some native extensions (SSL, regex) don't like multi-threaded access
    # 
    # Current approach: Sequential with early exit optimization
    # 1. Call Beatport first (gold standard for EDM)
    # 2. If confident EDM match → return immediately (skip other APIs)
    # 3. Otherwise fetch remaining sources sequentially
    # =========================================================================
    
    # Step 1: Fetch Beatport FIRST (gold standard for EDM, enables early exit)
    bp_result = None
    if not disable_beatport:
        bp_result = _fetch_beatport(artist, title, duration_s, version=version)
    
    # Process Beatport result immediately to check for early exit
    beatport_is_electronic = False
    if bp_result and bp_result.get("genre"):
        bp_w = WEIGHT_BEATPORT_REMIX if is_remix else WEIGHT_BEATPORT_BASE
        bp_local: Dict[str, float] = {}
        bp_genres = [g.strip() for g in bp_result["genre"].split(",")]
        
        for t in bp_genres:
            if _is_beatport_electronic(t):
                beatport_is_electronic = True
                bp_w = WEIGHT_BEATPORT_ELECTRONIC_REMIX if is_remix else WEIGHT_BEATPORT_ELECTRONIC
                break
        
        for t in bp_genres:
            _score_tag(t, bp_w, scores, bp_local)
        if bp_local:
            parts.append(("beatport", bp_w, bp_local))
            
            # Early exit: single specific EDM genre from Beatport → trust it completely
            # (Beatport is authoritative for electronic music; other sources add noise)
            if beatport_is_electronic and len(bp_local) == 1:
                main = list(bp_local.keys())[0]
                return GenreResolution(main=main, subs=[], confidence=BEATPORT_EARLY_EXIT_CONFIDENCE, breakdown=parts)
    
    # Step 2: Fetch SoundCloud FIRST for remixes (it has remix-specific tags)
    sc_result = None
    if not disable_soundcloud and is_remix:
        sc_result = _fetch_soundcloud(artist, title, version)
    
    # For remixes: if SoundCloud found something, skip MB/LFM (they have original track info)
    # If SoundCloud found nothing, fall back to MB/LFM (original genre better than nothing)
    skip_mb_lfm_for_remix = is_remix and not bp_result and sc_result and sc_result.get("tags")
    
    lfm_result = None
    if not skip_mb_lfm_for_remix:
        lfm_result = _fetch_lastfm(artist, title)
    
    mb_result = None
    if not disable_mb and not skip_mb_lfm_for_remix:
        mb_result = _fetch_musicbrainz(artist, title, duration_s, mb_recording)

    # =========================================================================
    # Process MusicBrainz
    # =========================================================================
    if mb_result:
        mb_w = WEIGHT_MB_REMIX if is_remix else WEIGHT_MB_BASE
        mb_local: Dict[str, float] = {}
        for t in mb_result:
            _score_tag(t, mb_w, scores, mb_local)
        if mb_local:
            parts.append(("musicbrainz", mb_w, mb_local))

    # =========================================================================
    # Process Last.fm (stronger influence to reflect community tags importance)
    # Weights tuned to balance: LFM has good coverage but can be noisy for EDM
    # =========================================================================
    if is_remix:
        lfm_w = WEIGHT_LASTFM_REMIX
    elif beatport_is_electronic:
        lfm_w = WEIGHT_LASTFM_WHEN_BP_ELECTRONIC
    else:
        lfm_w = WEIGHT_LASTFM_BASE
    
    if lfm_result:
        lfm_local: Dict[str, float] = {}
        # weight by log(count), scale with lfm_w
        for name, cnt in lfm_result:
            base = (math.log(max(cnt, 1)) if cnt > 0 else 0.0) * lfm_w
            c = canonical(name)
            if _is_noise(c):
                continue
            f = _downweight_factor(c) * _specificity_boost(c)
            w = base * f
            if w <= 0:
                continue
            scores[c] = scores.get(c, 0.0) + w
            lfm_local[c] = lfm_local.get(c, 0.0) + w
        if lfm_local:
            parts.append(("lastfm", lfm_w, lfm_local))

    # =========================================================================
    # Process SoundCloud (light weight for originals, higher for remixes)
    # =========================================================================
    if sc_result and sc_result.get("tags"):
        sc_w = WEIGHT_SC_REMIX if is_remix else WEIGHT_SC_BASE
        sc_local: Dict[str, float] = {}
        for name in sc_result["tags"]:
            _score_tag(name, sc_w, scores, sc_local)
        if sc_local:
            parts.append(("soundcloud", sc_w, sc_local))

    if not scores:
        return None

    # rank and choose main + up to 2 subs
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    main = ranked[0][0]
    subs = [k for k, _ in ranked[1:3]]

    # crude confidence: main share of total weight (0..1)
    total_w = sum(scores.values()) or 1.0
    conf = ranked[0][1] / total_w
    return GenreResolution(main=main, subs=subs, confidence=conf, breakdown=parts)
