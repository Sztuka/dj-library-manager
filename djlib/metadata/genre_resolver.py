from __future__ import annotations
import logging
import math
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

log = logging.getLogger(__name__)

# Ensure requests-cache side effects
import djlib.metadata  # noqa: F401

from . import mb_client
from . import lastfm
from .soundcloud import track_tags as sc_track_tags


def _norm(tag: str) -> str:
    t = (tag or "").strip().lower()
    t = t.replace("_", " ").replace("-", " ")
    t = " ".join(t.split())
    return t


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
}

import re as _re

def _is_noise(tag: str) -> bool:
    t = _norm(tag)
    if not t:
        return True
    if t in _NOISE_TERMS:
        return True
    # domain-like tokens
    if "." in t and not t.replace(".", "").isalpha():
        return True
    # '1–4 wochen' / '1-4 wochen' etc.
    if _re.search(r"\b\d+\s*[–-]?\s*\d*\s*wochen\b", t):
        return True
    # very short or purely numeric
    if len(t) <= 2 or t.isdigit():
        return True
    # Year-only tags (2023, 2024, etc.) or tokens ending with year markers
    if _re.fullmatch(r"20[0-3][0-9]", t):
        return True
    if _re.search(r"20[0-3][0-9]", t) and len(t.split()) == 1:
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


# Specific subgenres that should be boosted over their parent genres
_SPECIFIC_GENRE_BOOST = {
    # Rock subgenres
    "rock and roll": 2.0,
    "rockabilly": 2.0,
    "punk rock": 1.8,
    "hard rock": 1.8,
    "classic rock": 1.8,
    "progressive rock": 1.8,
    "glam rock": 1.8,
    "alternative rock": 1.5,
    "indie rock": 1.5,
    "new wave": 1.8,
    # Electronic/House subgenres
    "tech house": 1.5,
    "deep house": 1.5,
    "progressive house": 1.5,
    "afro house": 1.8,
    "acid house": 1.5,
    "electro house": 1.5,
    "electro swing": 1.8,
    # Techno subgenres
    "melodic techno": 1.5,
    "minimal techno": 1.5,
    "hard techno": 1.8,
    "hardcore": 1.8,
    # Trance subgenres
    "trance": 1.5,
    "psytrance": 1.8,
    # Pop subgenres
    "synth pop": 1.8,
    "electropop": 1.8,
    "dance pop": 1.5,
    "indie pop": 1.8,
    "eurodance": 2.0,
    # Disco variants
    "disco": 1.8,
    "italo disco": 2.0,
    "euro disco": 2.0,
    "nu disco": 1.8,
    # Urban/Hip-hop
    "hip hop": 1.5,
    "r&b": 1.5,
    "rnb": 1.5,
    # Funk/Soul
    "funk": 1.5,
    "soul": 1.5,
    "blues": 1.5,
    "swing": 1.8,
    # Caribbean/Latin
    "reggae": 1.5,
    "dancehall": 1.8,
    "ska": 1.8,
    "dub": 1.5,
    "reggaeton": 1.8,
    "latin": 1.5,
    "kuduro": 1.8,
    # Bass music
    "drum and bass": 1.5,
    "dnb": 1.5,
    "breakbeat": 1.5,
    # World/Regional
    "afrobeats": 1.8,
    "balkan": 1.8,
}


def _specificity_boost(tag: str) -> float:
    """Return boost factor for specific subgenres over generic parents."""
    t = _norm(tag)
    return _SPECIFIC_GENRE_BOOST.get(t, 1.0)


# Beatport-authoritative electronic genres (if BP returns these, trust it over Last.fm)
# These are specific enough that Beatport classification is reliable
BEATPORT_ELECTRONIC_GENRES = {
    # House variants
    "house", "tech house", "deep house", "progressive house", "afro house",
    "melodic house", "funky house", "jackin house", "tribal house", "soulful house",
    "bass house", "electro house", "future house", "g-house", "minimal house",
    "acid house", "chicago house", "uk garage", "garage", "speed garage",
    # Techno variants
    "techno", "melodic techno", "minimal techno", "hard techno", "peak time techno",
    "driving techno", "dub techno", "detroit techno", "industrial techno",
    # Trance
    "trance", "progressive trance", "psytrance", "uplifting trance", "vocal trance",
    # Bass music
    "drum and bass", "dnb", "liquid dnb", "jungle", "dubstep", "bass", "future bass",
    "breakbeat", "breaks", "uk bass",
    # Other electronic
    "electro", "electronica", "edm", "nu disco", "disco", "italo disco",
    "synthwave", "downtempo", "ambient", "chillout", "lounge",
    "hardstyle", "hardcore", "gabber", "happy hardcore",
}

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
WEIGHT_SC_REMIX = 20.0


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
    # INVERTED LOGIC: Default is NOT remix. Only explicit remix keywords make it a remix.
    # This prevents "(Hear Me Tonight)", "(Remastered)", "(Version 1)" from being treated as remixes.
    is_remix = False
    version_lower = (version or "").strip().lower()
    if version_lower:
        # Explicit remix indicators - ONLY these make it a remix
        remix_keywords = ["remix", "rework", "bootleg", "dub mix", "vip mix", "vip edit"]
        is_remix = any(kw in version_lower for kw in remix_keywords)
        # Producer edits: "X edit" where X is not radio/original/extended/club/single
        # e.g. "City Boys Edit", "Merchant Edit" should be treated as remixes
        if not is_remix and "edit" in version_lower:
            non_remix_edits = ["radio edit", "original edit", "extended edit", "club edit", "single edit", "album edit", "short edit"]
            if not any(ne in version_lower for ne in non_remix_edits):
                is_remix = True
    
    scores: Dict[str, float] = {}
    parts: List[Tuple[str, float, Dict[str, float]]] = []

    # =========================================================================
    # API calls - Sequential execution (requests_cache uses SQLite, not thread-safe)
    # 
    # NOTE: Phase 2 attempted parallel execution with ThreadPoolExecutor, but
    # requests_cache (used by Last.fm, SoundCloud) has a SQLite backend that
    # causes sqlite3.ProgrammingError when accessed from multiple threads.
    # Revert to sequential until we either:
    # - Switch to thread-safe cache backend (memory/filesystem)
    # - Use async/await with aiohttp
    # - Add proper locking around cache access
    # =========================================================================
    
    # Fetch Beatport (gold standard for EDM)
    bp_result = None
    if not disable_beatport:
        bp_result = _fetch_beatport(artist, title, duration_s, version=version)
    
    # Fetch Last.fm
    lfm_result = _fetch_lastfm(artist, title)
    
    # Fetch SoundCloud
    sc_result = None
    if not disable_soundcloud:
        sc_result = _fetch_soundcloud(artist, title, version)
    
    # Fetch MusicBrainz
    mb_result = None
    if not disable_mb:
        mb_result = _fetch_musicbrainz(artist, title, duration_s, mb_recording)

    # =========================================================================
    # Process Beatport (gold standard for EDM - highest weight)
    # BOOST: If Beatport returns specific electronic genre, increase weight
    # =========================================================================
    beatport_is_electronic = False
    if bp_result and bp_result.get("genre"):
        bp_w = WEIGHT_BEATPORT_REMIX if is_remix else WEIGHT_BEATPORT_BASE
        local: Dict[str, float] = {}
        # Beatport returns precise genres like "Techno (Peak Time / Driving)"
        bp_genres = [g.strip() for g in bp_result["genre"].split(",")]
        
        # Check if Beatport returned a specific electronic genre (not generic Dance/Pop)
        for t in bp_genres:
            if _is_beatport_electronic(t):
                beatport_is_electronic = True
                bp_w = WEIGHT_BEATPORT_ELECTRONIC_REMIX if is_remix else WEIGHT_BEATPORT_ELECTRONIC
                break
        
        for t in bp_genres:
            _score_tag(t, bp_w, scores, local)
        if local:
            parts.append(("beatport", bp_w, local))
            
            # PHASE 2.2: Early exit for confident Beatport EDM
            # If Beatport returns specific EDM genre with high confidence, skip other sources
            if beatport_is_electronic and len(local) == 1:
                # Single specific EDM genre from Beatport → return immediately
                main = list(local.keys())[0]
                return GenreResolution(main=main, subs=[], confidence=BEATPORT_EARLY_EXIT_CONFIDENCE, breakdown=parts)

    # =========================================================================
    # Process MusicBrainz
    # =========================================================================
    if mb_result:
        mb_w = WEIGHT_MB_REMIX if is_remix else WEIGHT_MB_BASE
        local: Dict[str, float] = {}
        for t in mb_result:
            _score_tag(t, mb_w, scores, local)
        if local:
            parts.append(("musicbrainz", mb_w, local))

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
        local: Dict[str, float] = {}
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
            local[c] = local.get(c, 0.0) + w
        if local:
            parts.append(("lastfm", lfm_w, local))

    # =========================================================================
    # Process SoundCloud (light weight for originals, higher for remixes)
    # =========================================================================
    if sc_result and sc_result.get("tags"):
        sc_w = WEIGHT_SC_REMIX if is_remix else WEIGHT_SC_BASE
        local: Dict[str, float] = {}
        for name in sc_result["tags"]:
            _score_tag(name, sc_w, scores, local)
        if local:
            parts.append(("soundcloud", sc_w, local))

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
