from __future__ import annotations
import functools
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

import yaml

from djlib.genre_utils import normalize_genre
from djlib.genre_canonical import resolve_genre as _resolve_genre_label

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


@functools.lru_cache(maxsize=1)
def _get_genres_data() -> Dict:
    """Load and cache genres.yml.  Lazy — first call reads the file, subsequent
    calls return the cached dict.  Tests can call ``_get_genres_data.cache_clear()``
    to inject different data.
    """
    try:
        with _GENRES_FILE.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        log.warning("Failed to load genres.yml: %s", e)
        return {}


@functools.lru_cache(maxsize=1)
def _get_electronic_genres() -> FrozenSet[str]:
    """Build set of electronic genre synonyms from genres.yml ``category=electronic``.

    Uses :func:`normalize_genre` for consistent normalization with all other modules.
    """
    result: set[str] = set()
    for _key, info in _get_genres_data().items():
        if not isinstance(info, dict):
            continue
        if info.get("category") != "electronic":
            continue
        for syn in info.get("synonyms", []):
            n = normalize_genre(syn)
            if n:
                result.add(n)
    return frozenset(result)


@functools.lru_cache(maxsize=1)
def _get_specificity_boost() -> Dict[str, float]:
    """Build specificity boost map from genres.yml ``boost`` field.

    For each genre with ``boost != 1.0``, maps every synonym (normalized)
    to that boost value.  This lets subgenres beat their parent genres
    in weighted scoring.
    """
    result: Dict[str, float] = {}
    for _key, info in _get_genres_data().items():
        if not isinstance(info, dict):
            continue
        boost = info.get("boost", 1.0)
        if not isinstance(boost, (int, float)) or boost == 1.0:
            continue
        for syn in info.get("synonyms", []):
            n = normalize_genre(syn)
            if n:
                result[n] = float(boost)
        label = info.get("label", "")
        if label:
            result[normalize_genre(label)] = float(boost)
    return result


# ---------------------------------------------------------------------------
# Aliases: additional mappings not captured by genres.yml synonyms.
# Keys MUST be in normalize_genre() form (lowercase, all punctuation→spaces).
# These handle compound / shorthand tags from Beatport/MB/LFM that don't
# warrant a full genres.yml synonym entry (too ambiguous or cross-genre).
# ---------------------------------------------------------------------------
ALIASES: Dict[str, str] = {
    "edm": "electronic",
    "tech house": "tech house",
    "techno house": "tech house",
    "d n b": "drum and bass",
    "d b": "drum and bass",       # from "d&b" after normalization
}


def canonical(tag: str) -> str:
    """Normalize and resolve aliases for a genre tag."""
    t = normalize_genre(tag)
    return ALIASES.get(t, t)


# ---------------------------------------------------------------------------
# Noise detection
# ---------------------------------------------------------------------------

# Non-genre noise patterns occasionally present in MB/LFM tags
_NOISE_TERMS: frozenset[str] = frozenset({
    "offizielle charts",  # german charts label
    "offizielle",
    "charts",
    "chart",
    "ph temp checken",
    "favourite", "favorite", "favorites",
    "seen live",
    "plattentests de",    # normalized (dot removed)
    "germany", "deutschland",
    # buzz / generic popularity tokens and year/season fluff
    "viral", "trending", "new", "new music", "summer mix", "summer", "remixes", "mix",
    # project-specific: do not use 'folk indie' at all
    "folk indie",
    # geographic locations (not genres)
    "puerto rico", "puerto rican", "jamaica", "jamaica dancehall",
    "united states", "usa", "uk", "united kingdom", "brazil", "brasil",
    # garbage/noise tokens from MB/LFM
    "merge", "various", "compilation", "soundtrack", "ost",
})

# Pre-compiled regex patterns for noise detection
_RE_WOCHEN = re.compile(r"\b\d+\s*[–-]?\s*\d*\s*wochen\b")
_RE_YEAR_ONLY = re.compile(r"20[0-3][0-9]$")
_RE_CONTAINS_YEAR = re.compile(r"20[0-3][0-9]")


def _validate_noise_terms() -> None:
    """Warn at import time if any _NOISE_TERMS entry collides with a genres.yml synonym."""
    data = _get_genres_data()
    genre_syns: set[str] = set()
    for info in data.values():
        if not isinstance(info, dict):
            continue
        for syn in info.get("synonyms", []):
            genre_syns.add(normalize_genre(syn))
    collisions = _NOISE_TERMS & genre_syns
    if collisions:
        log.warning(
            "_NOISE_TERMS collides with genres.yml synonyms — these tags will "
            "be silently dropped: %s", sorted(collisions)
        )


# Run validation once at import time (cheap, catches config drift)
_validate_noise_terms()


def _is_noise(tag: str) -> bool:
    """Return True if tag is a non-genre noise term (charts, dates, etc.)."""
    t = normalize_genre(tag)
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
    """Return a multiplicative factor (0..1] to reduce influence of broad tags.

    Only penalize truly problematic broad tags like folk/indie combos.
    Generic parent genres (rock, pop, electronic, dance) keep weight 1.0 —
    they can still win over false positives, but lose to specific subgenres
    via :func:`_specificity_boost`.
    """
    t = normalize_genre(tag)
    if not t:
        return 1.0
    if t == "folk":
        return 0.30
    if t in {"indie folk"}:  # 'folk indie' is fully ignored by _NOISE_TERMS
        return 0.40
    if t in {"alternative", "alternative rock"}:
        return 0.60
    return 1.0


def _specificity_boost(tag: str) -> float:
    """Return boost factor for specific subgenres over generic parents."""
    t = normalize_genre(tag)
    return _get_specificity_boost().get(t, 1.0)


def _is_beatport_electronic(genre: str) -> bool:
    """Check if Beatport genre is a specific electronic genre (not generic Dance/Pop).

    Uses word-boundary matching for compound genres like
    ``"Techno (Peak Time / Driving)"`` to avoid false positives
    (e.g. ``"warehouse"`` matching ``"house"``).
    """
    g = normalize_genre(genre)
    electronic = _get_electronic_genres()
    if g in electronic:
        return True
    for eg in electronic:
        if re.search(rf"\b{re.escape(eg)}\b", g):
            return True
    return False


# ============================================================================
# Weight constants for genre scoring
# ============================================================================
# These values are tuned to balance different sources' reliability.

WEIGHT_BEATPORT_BASE = 10.0
WEIGHT_BEATPORT_REMIX = 8.0
WEIGHT_BEATPORT_ELECTRONIC = 25.0       # When BP returns specific EDM genre
WEIGHT_BEATPORT_ELECTRONIC_REMIX = 15.0
WEIGHT_LASTFM_BASE = 6.0
WEIGHT_LASTFM_WHEN_BP_ELECTRONIC = 2.0  # Reduced to let Beatport dominate for EDM
WEIGHT_LASTFM_REMIX = 0.5
WEIGHT_MB_BASE = 3.0
WEIGHT_MB_REMIX = 1.5
WEIGHT_SC_BASE = 2.0
WEIGHT_SC_REMIX = 20.0                  # High for remixes: SC often has remix-specific tags
WEIGHT_VERSION_HINT = 40.0              # Version text IS the remixer's declared genre
WEIGHT_TAG_GENRE_HINT = 3.0             # File ID3 tag genre — weak fallback signal

# Confidence threshold for Beatport early exit
BEATPORT_EARLY_EXIT_CONFIDENCE = 0.8


# ============================================================================
# VERSION / TAG GENRE HINTS
# ============================================================================
# Remixers often name their work "X Afro House Remix" — this is the single
# strongest genre signal available.  We also fall back to
# ID3/FLAC tag_genre_original when no online source returns useful data.

@functools.lru_cache(maxsize=1)
def _get_genre_synonyms_for_hint() -> List[Tuple[str, str]]:
    """Build (normalized_synonym, genre_key) pairs sorted longest-first.

    Used by :func:`_genre_hints_from_version` to match genre names inside
    version/remix strings like "DJ Davy Afro House Remix".
    Only includes electronic, caribbean, and world categories — the kinds of
    genres that appear in remix names.
    """
    pairs: List[Tuple[str, str]] = []
    keep_categories = {"electronic", "caribbean", "world"}
    for key, info in _get_genres_data().items():
        if not isinstance(info, dict):
            continue
        if info.get("category") not in keep_categories:
            continue
        for syn in info.get("synonyms", []):
            n = normalize_genre(syn)
            if n and len(n) >= 4:          # skip very short synonyms (noise)
                pairs.append((n, key))
    # Sort longest first so "afro house" matches before "house"
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return pairs


@functools.lru_cache(maxsize=1)
def _get_remix_filter_categories() -> FrozenSet[str]:
    """Categories whose tags should be KEPT for remix scoring from LFM/MB.

    Tags from other categories (pop, rock, urban, jazz) are about the
    **original** track and are misleading for the remix genre.
    """
    return frozenset({"electronic", "caribbean", "world"})


@functools.lru_cache(maxsize=1)
def _get_remix_allowed_genres() -> FrozenSet[str]:
    """Normalized genre synonyms that survive the remix filter for LFM/MB.

    Built from genres.yml entries whose category is in
    :func:`_get_remix_filter_categories`.  Also includes broad umbrella
    terms (dance, electronic, club, etc.) that could legitimately describe
    a remix.
    """
    allowed: Set[str] = set()
    keep = _get_remix_filter_categories()
    for _key, info in _get_genres_data().items():
        if not isinstance(info, dict):
            continue
        if info.get("category") in keep:
            for syn in info.get("synonyms", []):
                n = normalize_genre(syn)
                if n:
                    allowed.add(n)
    # Add broad umbrella terms not in genres.yml but valid for remixes
    allowed |= {"dance", "club", "electronic", "electronica", "dance music",
                 "edm", "dance electronic", "dj"}
    return frozenset(allowed)


def _genre_hints_from_version(version: str, title: str = "") -> List[str]:
    """Extract genre hints from a version/remix string and title parenthetical.

    Scans the version text (and title's parenthetical info) for known genre
    synonyms.  Matches longest first to prefer "afro house" over "house".

    Also handles "afro" as a standalone hint when it appears in a remix
    context — in DJ music this almost always means Afro House.

    Examples::

        "DJ Davy Afro House Remix"  → ["afro house"]
        "ModeFlick Afro Tech Mix"   → ["afro tech"]
        "Silvio Luz Deep Afro Mix"  → ["deep house"]  (via "deep" + context)
        "GOTTI Afro Boot" (in title) → ["afro house"]  (afro in remix name)

    Returns list of normalized genre strings (ready for :func:`canonical`).
    """
    # Combine version + title parenthetical for scanning
    text_parts = []
    if version:
        text_parts.append(version)
    if not version and title:
        # Extract parenthetical from title
        m = re.search(r'\(([^)]+)\)', title)
        if m:
            text_parts.append(m.group(1))

    if not text_parts:
        return []
    text = normalize_genre(" ".join(text_parts))
    if not text:
        return []

    hints: List[str] = []
    used: Set[str] = set()                      # avoid overlapping matches
    for synonym, _key in _get_genre_synonyms_for_hint():
        # Word-boundary check: synonym must appear as whole words
        pattern = rf"\b{re.escape(synonym)}\b"
        if re.search(pattern, text):
            # Check no overlap with already-matched text
            if not any(synonym in u or u in synonym for u in used):
                hints.append(synonym)
                used.add(synonym)

    # Special handling: standalone "afro" in remix/edit/mix context
    # → almost always means "Afro House" in DJ music
    if not hints and re.search(r"\bafro\b", text):
        hints.append("afro house")

    return hints


# ============================================================================
# FETCH HELPERS
# ============================================================================

def _fetch_beatport(artist: str, title: str, duration_s: Optional[int],
                    version: str = "") -> Optional[Dict[str, object]]:
    """Fetch genre data from Beatport. Returns dict with 'genre' key or None."""
    try:
        from djlib.metadata.beatport import search_track as bp_search
        result = bp_search(artist, title, duration_s, version=version)
        log.debug("Beatport: %s - %s (%s) → %s", artist, title,
                  version or "original", result.get('genre') if result else None)
        return result
    except Exception as e:
        log.warning("Beatport fetch failed for %s - %s: %s", artist, title, e)
        return None


def _fetch_lastfm(artist: str, title: str) -> Optional[List[Tuple[str, int]]]:
    """Fetch top tags from Last.fm. Returns list of (tag_name, count) or None."""
    try:
        result = lastfm.top_tags(artist, title)
        log.debug("Last.fm: %s - %s → %d tags", artist, title,
                  len(result) if result else 0)
        return result
    except Exception as e:
        log.warning("Last.fm fetch failed for %s - %s: %s", artist, title, e)
        return None


def _fetch_soundcloud(artist: str, title: str,
                      version: str) -> Optional[Dict[str, object]]:
    """Fetch tags from SoundCloud. Returns dict with 'tags' key or None."""
    try:
        result = sc_track_tags(artist, title, version)
        tags = result.get('tags', []) if result else []
        log.debug("SoundCloud: %s - %s (%s) → %d tags", artist, title,
                  version, len(tags))
        return result
    except Exception as e:
        log.warning("SoundCloud fetch failed for %s - %s: %s", artist, title, e)
        return None


def _fetch_musicbrainz(
    artist: str, title: str, duration_s: Optional[int],
    mb_recording: Optional["mb_client.RecordingMatch"],
) -> Optional[List[str]]:
    """Fetch genres from MusicBrainz. Returns list of genre strings or None."""
    try:
        rec = (mb_recording if mb_recording
               else mb_client.search_recording(artist, title, duration=duration_s))
        if rec:
            genres = mb_client.get_recording_genres(
                rec.recording_id,
                release_group_id=rec.release_group_id,
                artist_id=rec.artist_id,
            )
            log.debug("MusicBrainz: %s - %s → %s", artist, title,
                      genres[:3] if genres else None)
            return genres
    except Exception as e:
        log.warning("MusicBrainz fetch failed for %s - %s: %s", artist, title, e)
    return None


# ============================================================================
# SCORING HELPERS
# ============================================================================

def _score_tag(
    tag: str,
    base_weight: float,
    scores: Dict[str, float],
    local: Dict[str, float],
    *,
    count: int = 0,
) -> None:
    """Score a single tag and update *scores* and *local* dicts.

    Applies :func:`canonical`, noise filter, downweight, and specificity boost.

    Args:
        tag:         Raw tag string from source.
        base_weight: Base weight for this source (e.g. ``WEIGHT_BEATPORT_BASE``).
        scores:      Global scores dict to update.
        local:       Local scores dict for this source to update.
        count:       Tag popularity count (e.g. Last.fm tag count). When > 0,
                     *base_weight* is scaled by ``log(count)`` for
                     popularity-weighted scoring.
    """
    c = canonical(tag)
    if _is_noise(c):
        return
    w = base_weight
    if count > 0:
        w *= math.log(max(count, 1))
    f = _downweight_factor(c) * _specificity_boost(c)
    w *= f
    if w <= 0:
        return
    scores[c] = scores.get(c, 0.0) + w
    local[c] = local.get(c, 0.0) + w


# ============================================================================
# REMIX DETECTION
# ============================================================================

# Keywords that indicate a TRUE remix (another artist reworked the track)
_REMIX_KEYWORDS = frozenset([
    "remix", "rework", "bootleg", "boot", "mashup", "dub mix", "vip mix",
    "vip edit", "flip",
])
# Edit types that are NOT remixes (same artist, different version)
_NON_REMIX_EDITS = frozenset([
    "radio edit", "original edit", "extended edit", "club edit",
    "single edit", "album edit", "short edit",
])
# Mix types that are NOT remixes (same artist, different version)
_NON_REMIX_MIXES = frozenset([
    "original mix", "extended mix", "instrumental mix", "club mix",
    "radio mix", "short mix", "album mix", "single mix",
    "vocal mix", "main mix", "12 mix", "12 inch mix",
    "live mix",
])


def _detect_remix(version: str, title: str, artist: str) -> bool:
    """Return True if the track is a TRUE remix (another artist reworked it).

    Default is **not** a remix.  Only explicit remix keywords make it one.
    This prevents "(Hear Me Tonight)", "(Remastered)", "(Version 1)" from
    being treated as remixes.
    """
    version_lower = (version or "").strip().lower()
    if version_lower:
        if any(kw in version_lower for kw in _REMIX_KEYWORDS):
            return True
        # Producer edits: "X edit" where X is not radio/original/extended/…
        # e.g. "City Boys Edit", "Merchant Edit" → treat as remix
        if "edit" in version_lower:
            if not any(ne in version_lower for ne in _NON_REMIX_EDITS):
                return True
        # Producer mixes: "X Mix" where X is not Original/Extended/Club/…
        # e.g. "Afro Tech Mix", "Deep Afro Mix" → treat as remix
        if "mix" in version_lower:
            if not any(nm in version_lower for nm in _NON_REMIX_MIXES):
                return True

    # Fallback: if version is empty, check title+artist for remix keywords
    if not version_lower:
        title_lower = (title or "").lower()
        artist_lower = (artist or "").lower()
        if any(kw in title_lower or kw in artist_lower for kw in _REMIX_KEYWORDS):
            return True
        # Also check for producer mixes in title parenthetical
        # e.g. "Love (GOTTI Afro Boot)" — "boot" already caught above;
        # also catch "Song (Producer Mix)" patterns
        paren_match = re.search(r'\(([^)]+)\)', title_lower)
        if paren_match:
            paren = paren_match.group(1)
            if "mix" in paren and not any(nm in paren for nm in _NON_REMIX_MIXES):
                return True
            if "edit" in paren and not any(ne in paren for ne in _NON_REMIX_EDITS):
                return True

    return False


# ============================================================================
# SOURCE SCORING - each function returns (local_dict, base_weight) or None
# ============================================================================

@dataclass
class SourceScore:
    """One source's contribution to the genre resolution."""
    source: str
    weight: float
    tags: Dict[str, float]


def _score_beatport(
    bp_result: Optional[Dict[str, object]],
    is_remix: bool,
    scores: Dict[str, float],
) -> Tuple[Optional[SourceScore], bool]:
    """Process Beatport result.  Returns ``(SourceScore, is_electronic)``."""
    if not bp_result or not bp_result.get("genre"):
        return None, False

    bp_w = WEIGHT_BEATPORT_REMIX if is_remix else WEIGHT_BEATPORT_BASE
    bp_local: Dict[str, float] = {}
    bp_genres = [g.strip() for g in bp_result["genre"].split(",")]

    is_electronic = False
    for t in bp_genres:
        if _is_beatport_electronic(t):
            is_electronic = True
            bp_w = (WEIGHT_BEATPORT_ELECTRONIC_REMIX if is_remix
                    else WEIGHT_BEATPORT_ELECTRONIC)
            break

    for t in bp_genres:
        _score_tag(t, bp_w, scores, bp_local)

    if bp_local:
        return SourceScore("beatport", bp_w, bp_local), is_electronic
    return None, is_electronic


def _score_musicbrainz(
    mb_result: Optional[List[str]],
    is_remix: bool,
    scores: Dict[str, float],
) -> Optional[SourceScore]:
    """Process MusicBrainz genres.

    For remixes: filters tags to electronic/dance/world only.  MB always
    returns the original recording's genres (hip-hop for a Kanye track even
    if the remix is Afro House).  Non-electronic tags are misleading.
    """
    if not mb_result:
        return None
    mb_w = WEIGHT_MB_REMIX if is_remix else WEIGHT_MB_BASE
    mb_local: Dict[str, float] = {}
    allowed = _get_remix_allowed_genres() if is_remix else None
    for t in mb_result:
        c = canonical(t)
        if allowed is not None and c not in allowed:
            log.debug("MB remix filter: dropping %r (not in allowed set)", t)
            continue
        _score_tag(t, mb_w, scores, mb_local)
    return SourceScore("musicbrainz", mb_w, mb_local) if mb_local else None


def _score_lastfm(
    lfm_result: Optional[List[Tuple[str, int]]],
    is_remix: bool,
    beatport_is_electronic: bool,
    scores: Dict[str, float],
) -> Optional[SourceScore]:
    """Process Last.fm top tags with log-weighted popularity scoring.

    For remixes: filters tags to electronic/dance/world only.  Last.fm always
    returns tags for the original artist+title ("hip hop" for Kanye, "pop"
    for Ed Sheeran) regardless of the remix — these are misleading.
    The reduced weight (WEIGHT_LASTFM_REMIX) combined with the filter
    means only dance/electronic LFM tags survive for remixes.
    """
    if not lfm_result:
        return None
    if is_remix:
        lfm_w = WEIGHT_LASTFM_REMIX
    elif beatport_is_electronic:
        lfm_w = WEIGHT_LASTFM_WHEN_BP_ELECTRONIC
    else:
        lfm_w = WEIGHT_LASTFM_BASE

    lfm_local: Dict[str, float] = {}
    allowed = _get_remix_allowed_genres() if is_remix else None
    for name, cnt in lfm_result:
        c = canonical(name)
        if allowed is not None and c not in allowed:
            log.debug("LFM remix filter: dropping %r (count=%d)", name, cnt)
            continue
        _score_tag(name, lfm_w, scores, lfm_local, count=cnt)
    return SourceScore("lastfm", lfm_w, lfm_local) if lfm_local else None


def _score_soundcloud(
    sc_result: Optional[Dict[str, object]],
    is_remix: bool,
    scores: Dict[str, float],
) -> Optional[SourceScore]:
    """Process SoundCloud tags.

    For remixes: electronic subgenre tags get a 1.5× boost.  SC is the only
    source that can find remix-specific genre info (LFM/MB always return the
    original-track genre).  Without this boost, non-electronic tags from the
    original (e.g. hip-hop for an Akon deep-house remix) can dominate.
    """
    if not sc_result or not sc_result.get("tags"):
        return None
    sc_w = WEIGHT_SC_REMIX if is_remix else WEIGHT_SC_BASE
    sc_local: Dict[str, float] = {}
    electronic = _get_electronic_genres()
    for name in sc_result["tags"]:
        tag_w = sc_w
        # For remixes: boost electronic/dance subgenre tags — these are much
        # more likely to reflect the actual remix rather than the original.
        if is_remix and canonical(name) in electronic:
            tag_w *= 1.5
        _score_tag(name, tag_w, scores, sc_local)
    return SourceScore("soundcloud", sc_w, sc_local) if sc_local else None


def _to_label(tag: str) -> str:
    """Map a normalized genre tag to its canonical label from genres.yml.

    Uses genre_canonical synonym resolution: "afrohouse" → "Afro House",
    "tech house" → "Tech House", etc.  Falls back to the raw tag
    (title-cased) if no canonical match is found.
    """
    result = _resolve_genre_label(tag)
    if result:
        return result[1]  # (canonical_key, label) → label
    # Fallback: title-case the raw tag for display
    return tag.title()


def _rank(
    scores: Dict[str, float],
    parts: List[SourceScore],
) -> GenreResolution:
    """Rank aggregated scores and return final resolution.

    Returns main genre + up to 2 sub-genres, with a confidence metric.

    .. note::
        Confidence is currently the main genre's share of total score (0..1).
        This is a rough heuristic — a track tagged ``["house": 25, "tech house": 24]``
        shows ~51 % confidence while ``["house": 25]`` shows 100 %.  The former
        is arguably *less* certain, not more.  A future improvement could weight
        by cross-source agreement instead of score dominance.
    """
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    main_raw = ranked[0][0]
    subs_raw = [k for k, _ in ranked[1:3]]

    # Map raw normalized tags to canonical genre labels from genres.yml.
    # e.g. "afrohouse" → "Afro House", "electronic" → "Electronic".
    # Falls back to the raw tag if no canonical match is found.
    main = _to_label(main_raw)
    subs = [_to_label(s) for s in subs_raw]

    # TODO(#8): Replace with cross-source agreement metric
    total_w = sum(scores.values()) or 1.0
    conf = ranked[0][1] / total_w
    return GenreResolution(main=main, subs=subs, confidence=conf, breakdown=parts)


# ============================================================================
# PUBLIC API
# ============================================================================

# All available source names for the ``sources`` parameter of :func:`resolve`.
ALL_SOURCES: frozenset[str] = frozenset({"beatport", "lastfm", "mb", "soundcloud"})


@dataclass
class GenreResolution:
    """Result of multi-source genre resolution."""
    main: str
    subs: List[str]
    confidence: float
    breakdown: List[SourceScore] = field(default_factory=list)


def resolve(
    artist: str,
    title: str,
    version: str = "",
    *,
    duration_s: int | None = None,
    sources: Set[str] | None = None,
    mb_recording: "mb_client.RecordingMatch | None" = None,
    tag_genre: str = "",
) -> GenreResolution | None:
    """Resolve genres using Beatport → Last.fm → MB → SoundCloud with scoring.

    Fetches genre data from multiple sources and aggregates scores.
    Early exit when Beatport returns confident EDM match (skips scoring
    other sources).

    New in v2: version-hint scoring + remix LFM/MB filtering.

    For **remixes**, two key improvements:

    1. **Version hints** — if the version string contains a known genre
       (e.g. "DJ Davy **Afro House** Remix"), that genre gets the highest
       weight (``WEIGHT_VERSION_HINT = 40``).  The remixer literally
       declared the genre in the track name.

    2. **LFM/MB filtering** — for remixes, Last.fm and MusicBrainz always
       return the **original** track's genres (hip-hop for Kanye, pop for
       Ed Sheeran).  These are filtered to only electronic/dance/world tags
       to avoid polluting the remix's genre.

    Args:
        artist:       Artist name.
        title:        Track title.
        version:      Version / remix info string (e.g. ``"Solardo Remix"``).
        duration_s:   Track duration in seconds (helps Beatport/MB matching).
        sources:      Which sources to query.  Defaults to all four.
                      Pass e.g. ``{"beatport", "lastfm"}`` to skip the rest.
        mb_recording: Pre-fetched MusicBrainz ``RecordingMatch`` to avoid
                      redundant API calls.  If provided, skips
                      ``search_recording`` call.
        tag_genre:    Original file tag genre (ID3/FLAC).  Used as a weak
                      fallback signal when no better data is available.

    Returns:
        :class:`GenreResolution` with main + up to 2 subs, or ``None`` if
        no sources returned usable data.
    """
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist and not title:
        return None

    enabled: frozenset[str] = frozenset(sources) if sources else ALL_SOURCES
    is_remix = _detect_remix(version, title, artist)

    scores: Dict[str, float] = {}
    parts: List[SourceScore] = []

    # -----------------------------------------------------------------
    # Sequential API calls (parallelization causes segfaults)
    #
    # Attempted parallelization with ThreadPoolExecutor resulted in
    # segfaults (Playwright not thread-safe, requests_cache filesystem
    # backend has internal threading issues).
    #
    # Current approach: sequential with early exit optimization.
    # 1. Call Beatport first (gold standard for EDM)
    # 2. If confident EDM match → return immediately
    # 3. Otherwise fetch remaining sources sequentially
    # -----------------------------------------------------------------

    # Step 1: Beatport (gold standard for EDM, enables early exit)
    bp_result = None
    if "beatport" in enabled:
        bp_result = _fetch_beatport(artist, title, duration_s, version=version)

    bp_score, beatport_is_electronic = _score_beatport(bp_result, is_remix, scores)
    if bp_score:
        parts.append(bp_score)
        # Early exit: single specific EDM genre → trust Beatport completely
        if beatport_is_electronic and len(bp_score.tags) == 1:
            main = _to_label(next(iter(bp_score.tags)))
            return GenreResolution(
                main=main, subs=[],
                confidence=BEATPORT_EARLY_EXIT_CONFIDENCE,
                breakdown=parts,
            )

    # Step 2: For remixes, try SoundCloud first (it has remix-specific tags)
    sc_result = None
    if "soundcloud" in enabled and is_remix:
        sc_result = _fetch_soundcloud(artist, title, version)

    # Step 3: Last.fm + MusicBrainz — always query even for remixes.
    # SC search can return tags from *wrong* remixes (generic "artist title
    # remix" query hits unrelated tracks).  LFM/MB provide a safety net with
    # original-track genre info which the scoring weights will deprioritise
    # for remixes anyway (WEIGHT_LASTFM_REMIX=0.5, WEIGHT_MB_REMIX=1.5).
    lfm_result = None
    if "lastfm" in enabled:
        lfm_result = _fetch_lastfm(artist, title)

    mb_result = None
    if "mb" in enabled:
        mb_result = _fetch_musicbrainz(artist, title, duration_s, mb_recording)

    # Step 4: Score all remaining sources
    mb_score = _score_musicbrainz(mb_result, is_remix, scores)
    if mb_score:
        parts.append(mb_score)

    lfm_score = _score_lastfm(lfm_result, is_remix, beatport_is_electronic, scores)
    if lfm_score:
        parts.append(lfm_score)

    # SoundCloud: fetch for non-remixes too (light weight, harmless)
    if "soundcloud" in enabled and not sc_result:
        sc_result = _fetch_soundcloud(artist, title, version)
    sc_score = _score_soundcloud(sc_result, is_remix, scores)
    if sc_score:
        parts.append(sc_score)

    # -----------------------------------------------------------------
    # Step 5: Version genre hints (strongest signal for remixes)
    #
    # The remixer literally named the track "X Afro House Remix" —
    # this is more reliable than any online API.
    # -----------------------------------------------------------------
    version_hints = _genre_hints_from_version(version, title)
    if version_hints:
        vh_local: Dict[str, float] = {}
        for hint in version_hints:
            _score_tag(hint, WEIGHT_VERSION_HINT, scores, vh_local)
        if vh_local:
            parts.append(SourceScore("version_hint", WEIGHT_VERSION_HINT, vh_local))
            log.info("Version hint for '%s': %s (weight=%.1f)",
                     version, version_hints, WEIGHT_VERSION_HINT)

    # -----------------------------------------------------------------
    # Step 6: File tag genre fallback (weak signal)
    #
    # ID3/FLAC tag_genre_original: used only when we have no scores yet
    # or as a tie-breaker.  Typical values: "Dance", "House", "Electronic".
    # -----------------------------------------------------------------
    if tag_genre and (not scores or max(scores.values(), default=0) < 5.0):
        tg_local: Dict[str, float] = {}
        _score_tag(tag_genre, WEIGHT_TAG_GENRE_HINT, scores, tg_local)
        if tg_local:
            parts.append(SourceScore("tag_genre", WEIGHT_TAG_GENRE_HINT, tg_local))

    if not scores:
        return None

    return _rank(scores, parts)
