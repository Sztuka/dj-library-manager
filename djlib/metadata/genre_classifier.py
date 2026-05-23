"""Production genre classifier: nano+WS+LF recipe.

Winning AB-test variant (75.5% exact / 90.5% family on 200-track gold set,
vs 44% / 55% for the old weighted-voting resolver).  See
``docs/DECISION_HISTORY.md`` phase 7.

Pipeline per track:
    1. SearXNG web search (→ Beatport, Discogs, DJ sites, etc.)
    2. Last.fm count-weighted tags
    3. gpt-5-nano classification call with canonical genre list

Returns ``genre``, ``confidence``, ``reasoning``, ``year``, ``year_evidence``,
``source`` — or raises ``ClassifierError`` on hard failure (after one
automatic retry on timeout).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import yaml

from djlib.config import get_openai_api_key, get_gemini_api_key, get_genre_classifier_provider
from djlib.metadata import lastfm
from djlib.metadata.web_search import create_searcher, search_track_genre

log = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parents[2]
_GENRES_FILE = _REPO / "genres.yml"

MODEL = "gpt-5-nano"
GEMINI_MODEL = "gemini-2.5-flash"
OPENAI_TIMEOUT = 120
OPENAI_URL = "https://api.openai.com/v1/responses"
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2}|2100)\b")


class ClassifierError(Exception):
    """Raised when classification fails after retries (network/model error)."""


@lru_cache(maxsize=1)
def _genre_labels() -> List[str]:
    with _GENRES_FILE.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return [v["label"] for v in data.values() if isinstance(v, dict) and v.get("label")]


# Module-level SearXNG searcher: created lazily, reused across calls.
_searcher = None
_searcher_lock = threading.Lock()


def _get_searcher():
    global _searcher
    with _searcher_lock:
        if _searcher is None:
            try:
                s = create_searcher("searxng")
                _searcher = s if s.is_available() else False
            except Exception as e:
                log.warning("SearXNG unavailable: %s", e)
                _searcher = False
        return _searcher or None


def _sc_track_url_from_result(r: Any) -> str:
    """Return r.url if it looks like a SC track page (2 path segments), else ''."""
    if "soundcloud.com" not in r.url.lower():
        return ""
    path = r.url.split("soundcloud.com", 1)[-1].rstrip("/")
    parts = [p for p in path.split("/") if p]
    return r.url if len(parts) >= 2 else ""


def _fetch_sc_metadata(
    results: "List",
    artist: str = "",
    title: str = "",
    version: str = "",
) -> Dict[str, str]:
    """Return SoundCloud metadata (year, genre, tags) for this track.

    Strategy:
    1. Scan ``results`` for a SC track URL and scrape ``window.__sc_hydration``.
    2. If not found or empty, do a dedicated ``site:soundcloud.com`` SearXNG fallback.

    Returns dict with ``year``, ``genre``, ``tags`` (strings, may be empty).
    """
    from djlib.metadata.url_scraper import _sc_html_metadata

    def _try_url(url: str) -> Dict[str, str]:
        try:
            meta = _sc_html_metadata(url)
            if any(meta.values()):
                log.debug("SC metadata via HTML: %s → %s", url, meta)
            return meta
        except Exception as exc:
            log.debug("SC HTML metadata failed for %s: %s", url, exc)
            return {"year": "", "genre": "", "tags": ""}

    empty = {"year": "", "genre": "", "tags": ""}

    # Pass 1: SC URL already in main search results
    for r in results:
        url = _sc_track_url_from_result(r)
        if url:
            meta = _try_url(url)
            if any(meta.values()):
                return meta
            break

    # Pass 2: dedicated SC fallback — try query variations to escape cache / profile bias.
    # SearXNG site:soundcloud.com often returns artist profile pages instead of track pages.
    # Different query shapes surface different indexed pages from Google.
    s = _get_searcher()
    if s and (title or version):
        # Build query candidates — progressively drop artist to avoid artist profile pages
        candidates: List[str] = []
        base = f"{title} {version}".strip()
        # site: queries — fast when indexed, but unofficial edits often not in Google's SC index
        if artist:
            candidates.append(f"{artist} {base} site:soundcloud.com")
        candidates.append(f"{base} site:soundcloud.com")
        # Broad queries with "soundcloud" keyword — finds track pages that site: misses
        # because Google doesn't index SC track pages for unofficial edits
        if artist:
            candidates.append(f"{artist} {base} soundcloud")
        candidates.append(f"{base} soundcloud")

        for q in candidates:
            try:
                sc_results = s.search(q, max_results=3)
                for r in sc_results:
                    url = _sc_track_url_from_result(r)
                    if url:
                        meta = _try_url(url)
                        if any(meta.values()):
                            return meta
                        break
            except Exception as exc:
                log.debug("SC fallback query '%s' failed: %s", q, exc)

    return empty


def _fetch_web_search(artist: str, title: str, version: str, filename: str = "") -> str:
    """Return web-search prompt context, or empty string on failure.

    When a SoundCloud URL is found among results, also scrapes the SC page for
    the actual upload year and prepends it as a ``[SOUNDCLOUD UPLOAD YEAR: YYYY]``
    header — giving the classifier an authoritative year signal without relying
    on model training knowledge.
    """
    s = _get_searcher()
    if not s:
        return ""
    try:
        sr = search_track_genre(s, artist=artist, title=title, version=version, filename=filename)
        ctx = sr.to_prompt_context()

        sc = _fetch_sc_metadata(sr.results, artist=artist, title=title, version=version)
        sc_lines = []
        if sc.get("year"):
            sc_lines.append(f"Upload year: {sc['year']}")
        if sc.get("genre"):
            sc_lines.append(f"Genre tag: {sc['genre']}")
        if sc.get("tags"):
            sc_lines.append(f"Tags: {sc['tags']}")
        if sc_lines:
            sc_block = "[SOUNDCLOUD PAGE METADATA — extracted directly, highest priority]\n" + "\n".join(sc_lines)
            ctx = sc_block + "\n\n" + ctx

        return ctx
    except Exception as e:
        log.warning("Web search failed for %s - %s: %s", artist, title, e)
        return ""


def _fetch_lastfm_tags(artist: str, title: str) -> str:
    """Return Last.fm count-weighted tags as 'name (count), ...' or empty."""
    try:
        tags = lastfm.top_tags(artist, title, min_count=10, max_tags=10)
    except Exception as e:
        log.warning("Last.fm failed for %s - %s: %s", artist, title, e)
        return ""
    if not tags:
        return ""
    return ", ".join(f"{name} ({cnt})" for name, cnt in tags)


def _normalize_release_year(value: Any) -> str:
    """Normalize a model-returned year/date into ``YYYY`` or empty string."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() in {"none", "null"}:
        return ""
    match = _YEAR_RE.search(text)
    if not match:
        return ""
    year = match.group(1)
    try:
        year_int = int(year)
    except ValueError:
        return ""
    if 1900 <= year_int <= (time.gmtime().tm_year + 1):
        return year
    return ""


def _year_supported_by_web_context(year: str, web_search_context: str) -> bool:
    """Accept a year from the model.

    Validation is intentionally lax: we let the model decide based on its
    instructions in the prompt (use WS evidence first, fall back to training
    knowledge). Confidence calibration happens at the call site (e.g. batch
    enrichment caps confidence to 0.6 for years not backed by WS or MB).

    The only hard rule kept here is: empty year string → False.
    """
    return bool(year)


def _build_prompt(
    artist: str,
    title: str,
    version: str,
    bpm: str,
    key: str,
    web_search_context: str,
    lastfm_tags: str,
    genre_labels: List[str],
) -> str:
    """Build the classification prompt. Ported from scripts/ab_test_genre.py
    variant ``nano+WS+LF`` (commits 470fee6, b5b95d3).
    """
    genre_list = ", ".join(genre_labels)

    parts: List[str] = []
    if artist:
        parts.append(f"Artist: {artist}")
    if title:
        parts.append(f"Title: {title}")
    if version:
        parts.append(f"Version/Remix: {version}")
    if bpm:
        parts.append(f"BPM: {bpm}")
    if key:
        parts.append(f"Key: {key}")

    if web_search_context and not web_search_context.startswith("(No web search"):
        parts.append("")
        parts.append(f"Web search results:\n{web_search_context}")

    if lastfm_tags:
        parts.append("")
        parts.append(
            "Last.fm community tags (sorted by count, may include non-genre descriptors):\n"
            + lastfm_tags
        )

    track_info = "\n".join(parts)

    _REMIX_KW = re.compile(r'\b(?:remix|edit|bootleg|rework|refix|mashup|mash-?up|flip)\b', re.IGNORECASE)
    is_remix = bool(_REMIX_KW.search(version) or _REMIX_KW.search(title))

    # Detect "Artist A x Artist B" mashup credit in the artist field.
    # Both A and B are SOURCE MATERIAL — the actual mashup creator is often unnamed.
    # Neither artist's genre reliably predicts the mashup's genre; web search is primary.
    _MASHUP_CREDIT = re.compile(r'^(.+?)\s+(?:[xX×]|vs\.?|versus)\s+(.+)$')
    _mc = _MASHUP_CREDIT.match(artist)
    is_multi_source_mashup = bool(_mc) and is_remix  # x/vs credit + mashup keyword in title/version

    remix_instruction = ""
    if is_multi_source_mashup:
        artists_listed = artist
        remix_instruction = (
            f"\n\nCRITICAL — MULTI-SOURCE MASHUP CLASSIFICATION RULE:\n"
            f'This is a mashup combining elements from: {artists_listed}.\n'
            f"NONE of the listed artists necessarily determines the genre — they are source material only. "
            f"The actual mashup creator is often unlisted.\n"
            f"For mashups, you MUST prioritize: (1) web search results showing genre tags on DJ platforms "
            f"(SoundCloud, Beatport), then (2) BPM range. "
            f"Ignore the listed artists' known genres — a mashup of a pop song into an Afro House "
            f"production is Afro House, not Pop."
        )
    elif is_remix:
        remix_instruction = (
            "\n\nCRITICAL — REMIX/EDIT CLASSIFICATION RULE:\n"
            "This track is a REMIX or EDIT. You MUST classify it by the REMIX STYLE, "
            "NOT by the original track's genre. The remixer transforms the track into a new genre.\n"
            "The REMIXER/EDITOR identity is the strongest signal for determining the specific "
            "dance subgenre. Use your knowledge of the remixer's scene and production style.\n"
            "The original artist's genre is almost always WRONG for the remix."
        )

    bpm_guide = (
        "\n\nBPM genre ranges (approximate — ONLY for electronic/dance productions, NOT for rock/pop/hip-hop/etc.):\n"
        "70-100: Hip-Hop, R&B, Reggaeton, Dancehall\n"
        "100-115: UK Garage, Afrobeats\n"
        "115-126: Deep House\n"
        "116-128: Afro House\n"
        "120-128: House, Tech House\n"
        "124-132: Melodic Techno, Progressive House\n"
        "128-140: Techno, Hard Techno, Trance\n"
        "140-150: Psytrance\n"
        "150-180: Drum & Bass\n"
        "\n"
        "WARNING: BPM is UNRELIABLE for non-electronic genres. A rock song at 153 BPM is still Rock, "
        "NOT Drum & Bass. When the artist is clearly non-electronic, IGNORE the BPM ranges above.\n"
        "BPM values >140 may be double-time detection errors (actual BPM = value/2).\n"
        "\n"
        "House subgenre hints (when BPM 118-130 and genre family is House):\n"
        "- Deep House: melodic, warm, often vocal, jazzy/soulful chords, slower groove\n"
        "- Tech House: groovy, minimal, repetitive, percussive, functional DJ tool\n"
        "- Afro House: African percussion patterns, tribal rhythms, organic textures\n"
        "- Disco House: disco samples, funky basslines, filtered disco loops\n"
        "- House (generic): only when no subgenre signal is strong enough"
    )

    remix_leak_warning = ""
    if not is_remix and not is_multi_source_mashup:
        remix_leak_warning = (
            " IMPORTANT: This track is NOT a remix. If web results mention remixes or "
            "alternative versions, classify based on the ORIGINAL track's genre, not the remix's."
        )
    web_search_signal = ""
    if web_search_context and not web_search_context.startswith("(No web search"):
        web_search_signal = (
            "\n3. WEB SEARCH RESULTS — includes a [SOUNDCLOUD PAGE METADATA] block (if present) "
            "scraped directly from the SC track page, followed by search snippets from DJ sites.\n"
            "   [SOUNDCLOUD PAGE METADATA] Genre tag / Tags — set by the uploader for THIS exact "
            "track. When present, treat as ground truth for genre (overrides artist knowledge).\n"
            "   EXACT MATCH RULE: If a result from Beatport, Traxsource, or Discogs closely matches "
            "THIS track, its genre tag is also authoritative.\n"
            "   If results are ambiguous or match a different version/artist, fall back to artist knowledge."
            f"{remix_leak_warning}\n"
        )

    bpm_signal_num = "4" if web_search_signal else "3"

    system_prompt = (
        f"You are an expert DJ music classifier. Classify tracks into exactly ONE of these genres:\n"
        f"{genre_list}\n\n"
        f"Use the following signals IN PRIORITY ORDER to determine the MOST SPECIFIC matching genre "
        f"(always prefer a specific subgenre over a broad parent, e.g. Tech House over House):\n"
        f"\n"
        f"1. ARTIST/TITLE KNOWLEDGE — your world knowledge of the artist's known genre is the "
        f"STRONGEST signal. If you recognize the artist, their genre almost always determines "
        f"the classification. A track by a known rock band is Rock regardless of BPM.\n"
        + (
            f"   EXCEPTION for this track: the listed artists ({artist}) are SOURCE MATERIAL for a mashup "
            f"— their genres do NOT apply. Use web search and BPM instead.\n"
            if is_multi_source_mashup else ""
        ) +
        f"2. REMIXER/EDITOR identity — for remixes, the remixer's scene/style overrides the original artist's genre.\n"
        f"{web_search_signal}"
        f"{bpm_signal_num}. BPM — supplementary range indicator, mainly useful for ELECTRONIC genres only."
        f"{remix_instruction}"
        f"{bpm_guide}\n\n"
        f"CRITICAL: Many tracks are non-electronic (Rock, Pop, Hip-Hop, R&B, Soul, Funk, "
        f"Reggae, Punk, Indie Pop, Synthpop, etc.). For these genres, artist identity is the "
        f"primary signal. Do NOT let BPM override artist knowledge — a hip-hop track at 170 BPM "
        f"is still Hip-Hop (BPM detector may have measured double-time).\n\n"
        f"Also extract the release year for THIS exact track/version, in this priority order:\n"
        f"  (a) [SOUNDCLOUD PAGE METADATA] block in web search context — scraped directly from "
        f"the SoundCloud track page. 'Upload year' is the EXACT year for THIS version — use it "
        f"unconditionally, overrides training knowledge. 'Genre tag' and 'Tags' are set by the "
        f"uploader and are AUTHORITATIVE for genre classification of this specific edit/remix.\n"
        f"  (b) EXACT MATCH on DJ platform (Beatport, Traxsource, Discogs, SoundCloud): if a web "
        f"search result closely matches THIS track (same artist + title) and shows a year or upload "
        f"date — use that year. This is very reliable for remixes and edits.\n"
        f"  (c) Other web search snippets that mention a year in context of THIS track → use it.\n"
        f"  (d) Fall back to training knowledge ONLY when web search found nothing useful. "
        f"For remixes/edits, training knowledge of the original song's year is almost always WRONG "
        f"— prefer null over guessing the original release year.\n"
        f"  (e) Return null if you have no reliable estimate.\n"
        f"CRITICAL: For remixes, edits, and mashups — the year is the REMIX release year, "
        f"NOT the original song's year. If the original was from 1983 but the remix was uploaded "
        f"to SoundCloud in 2022, return 2022.\n\n"
        f"If signals are weak or conflicting, choose the broadest matching genre family "
        f"and set confidence below 0.5.\n\n"
        f"Respond with JSON: {{\"genre\": \"...\", \"confidence\": 0.0-1.0, "
        f"\"reasoning\": \"...\", \"year\": \"YYYY or null\", "
        f"\"year_evidence\": \"source/snippet or null\"}}\n"
        f"Confidence guide: 0.9+ only when multiple signals agree, 0.7-0.9 single strong signal, "
        f"<0.7 uncertain or conflicting. Confidence MUST be below 0.5 if classification relies solely on BPM.\n"
        f"In reasoning, briefly list each signal used and what it indicated."
    )

    user_prompt = f"Classify this track:\n\n{track_info}"
    return json.dumps([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ])


def _call_openai(api_key: str, prompt_json: str, model: str = MODEL) -> Dict[str, Any]:
    messages = json.loads(prompt_json)
    input_msgs = [{"role": m["role"], "content": m["content"]} for m in messages]

    resp = requests.post(
        OPENAI_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "input": input_msgs,
            "text": {"format": {"type": "json_object"}},
            "max_output_tokens": 4096,
        },
        timeout=OPENAI_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()

    text = ""
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    text = c.get("text", "")

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if not m:
            raise ClassifierError(f"OpenAI returned non-JSON: {text[:200]}")
        result = json.loads(m.group(1))

    if "genre" not in result:
        raise ClassifierError(f"OpenAI response missing 'genre' field: {text[:200]}")
    return result


def _call_gemini(api_key: str, prompt_json: str, model: str = GEMINI_MODEL) -> Dict[str, Any]:
    messages = json.loads(prompt_json)
    system_text = next((m["content"] for m in messages if m["role"] == "system"), "")
    user_text = next((m["content"] for m in messages if m["role"] == "user"), "")

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise ClassifierError("google-genai not installed: pip install google-genai")

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=user_text,
        config=types.GenerateContentConfig(
            system_instruction=system_text,
            response_mime_type="application/json",
            max_output_tokens=4096,
        ),
    )
    text = response.text or ""
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if not m:
            raise ClassifierError(f"Gemini returned non-JSON: {text[:200]}")
        result = json.loads(m.group(1))

    if "genre" not in result:
        raise ClassifierError(f"Gemini response missing 'genre' field: {text[:200]}")
    return result


def classify_genre(
    artist: str,
    title: str,
    *,
    version: str = "",
    bpm: str = "",
    key: str = "",
    filename: str = "",
    max_retries: int = 1,
    on_step: Optional[Any] = None,
) -> Dict[str, Any]:
    """Classify a track into a canonical genre label.

    Signal sources: SearXNG web search + Last.fm tags + artist/title knowledge.
    Model: ``gpt-5-nano``. AB-tested at 75.5% exact / 90.5% family accuracy.

    Args:
        on_step: optional callable(step_name: str, progress: float) called before
            each pipeline phase. Useful for progress reporting in batch enrichment.
            Steps: "web_search" (0.05), "lastfm" (0.55), "classifying" (0.65).

    Returns:
        ``{"genre": str, "confidence": float, "reasoning": str, "year": str,
           "year_evidence": str, "source": str, "lastfm_tags": str}``

    Raises:
        ClassifierError: on OpenAI timeout/error after ``max_retries + 1``
            attempts, or missing OpenAI key.
    """
    def _step(name: str, progress: float) -> None:
        if on_step:
            try:
                on_step(name, progress)
            except Exception:
                pass

    provider = get_genre_classifier_provider()
    if provider == "gemini":
        api_key = get_gemini_api_key()
        if not api_key:
            raise ClassifierError("Gemini API key not configured (set gemini_api_key in config.local.yml)")
    else:
        api_key = get_openai_api_key()
        if not api_key:
            raise ClassifierError("OpenAI API key not configured (set openai_api_key in config.local.yml)")

    artist = (artist or "").strip()
    title = (title or "").strip()
    if not (artist or title):
        raise ClassifierError("Cannot classify: empty artist and title")

    _step("web_search", 0.05)
    ws_ctx = _fetch_web_search(artist, title, version, filename)
    _step("lastfm", 0.55)
    lf_tags = _fetch_lastfm_tags(artist, title)
    _step("classifying", 0.65)

    prompt = _build_prompt(
        artist=artist, title=title, version=version, bpm=bpm, key=key,
        web_search_context=ws_ctx, lastfm_tags=lf_tags,
        genre_labels=_genre_labels(),
    )

    _caller = _call_gemini if provider == "gemini" else _call_openai

    last_err: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            result = _caller(api_key, prompt)
            release_year = _normalize_release_year(
                result.get("year") or result.get("release_year")
            )
            year_evidence = str(result.get("year_evidence", "") or "").strip()
            if release_year and not _year_supported_by_web_context(release_year, ws_ctx):
                log.warning(
                    "Discarding classifier year %s for %s - %s: not found in WS context",
                    release_year, artist, title,
                )
                release_year = ""
                year_evidence = ""
            if not release_year:
                year_evidence = ""
            return {
                "genre": str(result.get("genre", "")).strip(),
                "confidence": float(result.get("confidence", 0.0) or 0.0),
                "reasoning": str(result.get("reasoning", "")).strip(),
                "year": release_year,
                "year_evidence": year_evidence,
                "source": f"{GEMINI_MODEL}+WS+LF" if provider == "gemini" else "nano+WS+LF",
                "lastfm_tags": lf_tags,
            }
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = e
            log.warning("%s timeout (attempt %d/%d): %s", provider, attempt + 1, max_retries + 1, e)
            if attempt < max_retries:
                time.sleep(1.5 * (attempt + 1))
        except Exception as e:
            last_err = e
            log.warning("%s call failed (attempt %d/%d): %s", provider, attempt + 1, max_retries + 1, e)
            if attempt < max_retries:
                time.sleep(1.0)

    raise ClassifierError(f"Classification failed after {max_retries + 1} attempts: {last_err}")
