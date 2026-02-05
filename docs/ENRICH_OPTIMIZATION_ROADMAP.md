# 🚀 ENRICH OPTIMIZATION ROADMAP

**Status:** Draft  
**Created:** 2026-02-05  
**Author:** CTO/Product Owner Analysis  
**Priority:** High — enrich is the most time-consuming workflow

---

## Executive Summary

System `enrich-online` to obecnie najbardziej czasochłonny workflow w DJ Library Manager. Przetworzenie 100 tracków zajmuje **15-40 minut** w zależności od dostępności API i typu utworów (oryginały vs remixy).

### Key Findings

| Metryka | Current State | Target State | Improvement |
|---------|---------------|--------------|-------------|
| Czas per track (average) | 12-18s | 4-6s | **~70%** |
| Czas per track (worst case) | 15-25s | 8-10s | **~60%** |
| MB requests per track | 3-6 | 1-2 | **~70%** |
| 100 tracks processing | 15-40 min | 5-10 min | **~70%** |

---

## 1. Current Architecture Analysis

### 1.1 Data Flow Diagram

```
                              ┌─────────────────────────────────────────────┐
                              │           enrich_online_for_row()           │
                              │           [djlib/enrich.py:1261]            │
                              └─────────────────┬───────────────────────────┘
                                                │
                              ┌─────────────────▼───────────────────────────┐
                              │          suggest_metadata()                  │
                              │          [djlib/enrich.py:459]               │
                              │          (enable_online=True)                │
                              └─────────────────┬───────────────────────────┘
                                                │
          ┌─────────────────────────────────────┼─────────────────────────────────────┐
          │                                     │                                     │
          ▼                                     ▼                                     ▼
┌─────────────────────┐            ┌────────────────────────┐            ┌────────────────────────┐
│  lookup_acoustid()  │            │  lookup_musicbrainz()  │            │   resolve_genres()     │
│  [enrich.py:1004]   │            │  [enrich.py:771]       │            │  [genre_resolver.py]   │
│  ~2-5s per track    │            │  ~5-10s per track!     │            │   ~4-8s per track      │
└────────┬────────────┘            └───────────┬────────────┘            └───────────┬────────────┘
         │                                     │                                     │
         │  ┌──────────────────────────────────┼──────────────────────────────────┐ │
         │  │                                  │                                  │ │
         ▼  ▼                                  ▼                                  ▼ │
    ┌────────────┐                    ┌────────────────┐                ┌───────────▼────────┐
    │ MusicBrainz│                    │ MusicBrainz    │                │     Beatport       │
    │ recording  │                    │ search_record  │                │  search_track()    │
    │ /ws/2 API  │                    │ +RG lookup     │                │  ~1.5s             │
    │ ~3s        │                    │ +artist lookup │                └───────────┬────────┘
    └──────┬─────┘                    │ ~5-7s total    │                            │
           │                          └────────┬───────┘                            │
           │                                   │                         ┌──────────▼────────┐
           │                                   │                         │   MusicBrainz     │
           │                                   │                         │ (in resolve_genres│
           │                                   │                         │  REDUNDANT!)      │
           │                                   │                         │ ~2s               │
           │                                   │                         └──────────┬────────┘
           │                                   │                                    │
           │                                   │                         ┌──────────▼────────┐
           │                                   │                         │    Last.fm        │
           │                                   │                         │   top_tags()      │
           │                                   │                         │   ~0.5s           │
           │                                   │                         └──────────┬────────┘
           │                                   │                                    │
           │                                   │                         ┌──────────▼────────┐
           │                                   │                         │   SoundCloud      │
           │                                   │                         │  (for remixes)    │
           │                                   │                         │   ~2-4s           │
           └───────────────────────────────────┴────────────────────────┴───────────────────┘
```

### 1.2 API Rate Limits

| API | Rate Limit | Current Usage | Notes |
|-----|------------|---------------|-------|
| **MusicBrainz** | 1 req/s | 3-6 req/track | 🔴 CRITICAL bottleneck |
| **Beatport** | 1 req/s | 1-2 req/track | 🟡 OK |
| **Last.fm** | 5 req/s | 1-2 req/track | 🟢 Fast |
| **SoundCloud** | ~1 req/s | 3-5 req/track | 🟠 Too many queries |
| **AcoustID** | 3 req/s | 1 req/track | 🟢 Fast |

### 1.3 MusicBrainz Call Analysis (CRITICAL)

**Problem:** MusicBrainz is called **multiple times** for the same track in different code paths.

| Location | Function | MB Calls | Time |
|----------|----------|----------|------|
| `enrich.py:1004` | `lookup_acoustid()` | 1 (recording by ID) | ~1.05s |
| `enrich.py:771` | `lookup_musicbrainz()` | 2-4 (search + RG + artist) | ~2-4s |
| `genre_resolver.py:241` | `resolve()` | 2-3 (search + genres) | ~2-3s |
| `enrich.py:591` | `get_original_release_info()` | 1-2 | ~1-2s |

**TOTAL: 6-12 MB calls per track!** With 1 req/s limit = **6-12 seconds just waiting for MB**

### 1.4 Redundant Calls Identified

```python
# LOCATION 1: enrich.py - lookup_musicbrainz()
match = mb_client.search_recording(artist, title)  # ← Call #1
genres = mb_client.get_recording_genres(match.recording_id, ...)  # ← Calls #2-4

# LOCATION 2: genre_resolver.py - resolve() 
# Called AFTER lookup_musicbrainz() already fetched data!
rec = mb_client.search_recording(artist, title, duration=duration_s)  # ← DUPLICATE!
tags = mb_client.get_recording_genres(rec.recording_id, ...)  # ← DUPLICATE!
```

---

## 2. Pain Points (Prioritized)

### 🔴 P0 — Critical (Must Fix)

#### 2.1 Redundant MusicBrainz Queries
- **File:** `djlib/metadata/genre_resolver.py` lines 241-256
- **Issue:** `resolve_genres()` calls `mb_client.search_recording()` even when `lookup_musicbrainz()` already did
- **Impact:** 2-4 extra seconds per track
- **Solution:** Pass MB data from `lookup_musicbrainz()` to `resolve_genres()` or cache results

#### 2.2 No In-Memory Caching for MB Results
- **File:** `djlib/metadata/mb_client.py`
- **Issue:** Same recording queried multiple times in single enrich run
- **Impact:** Wasted API calls, rate limit delays
- **Solution:** Add `@lru_cache` to `search_recording()` and `get_recording_genres()`

### 🟠 P1 — High Priority

#### 2.3 Sequential API Calls in genre_resolver
- **File:** `djlib/metadata/genre_resolver.py` lines 214-305
- **Issue:** Beatport → MB → Last.fm → SoundCloud called sequentially
- **Impact:** 4-8 seconds of serial waiting
- **Solution:** Use `concurrent.futures.ThreadPoolExecutor` for parallel calls

#### 2.4 No Early-Exit for High-Confidence Results
- **File:** `djlib/metadata/genre_resolver.py`
- **Issue:** Always queries all 4 sources even when Beatport returns exact match
- **Impact:** Unnecessary API calls for EDM tracks
- **Solution:** Early return when Beatport confidence > 0.9

#### 2.5 Double Beatport Calls
- **File:** `djlib/enrich.py` lines 609-669
- **Issue:** Beatport searched twice (once for remix, once for original)
- **Impact:** Extra 1.5s per remix track
- **Solution:** Consolidate search logic

### 🟡 P2 — Medium Priority

#### 2.6 SoundCloud Query Explosion
- **File:** `djlib/metadata/soundcloud.py` lines 82-105
- **Issue:** Generates 3-5 different query strings per track
- **Impact:** 2.4-4s per track with 0.8s delay between queries
- **Solution:** Limit to 2 most effective query patterns

#### 2.7 Last.fm Called Multiple Times
- **File:** `djlib/enrich.py` lines 647-660, 684-697
- **Issue:** `track_info()` and `top_tags()` called separately
- **Impact:** Extra 0.5-1s
- **Solution:** Batch Last.fm calls or cache results

### 🟢 P3 — Nice to Have

#### 2.8 Progress Reporting
- **File:** `djlib/cli.py` lines 628-631
- **Issue:** Minimal feedback during long enrich runs
- **Solution:** Add ETA, per-source timing stats

#### 2.9 Batch Processing Mode
- **Issue:** No way to process multiple tracks in optimized batch
- **Solution:** Pre-fetch MB data for batch, then process

---

## 3. Proposed Solutions

### 3.1 Phase 1: Quick Wins (1 day effort)

#### 3.1.1 Add LRU Cache to MB Client

```python
# djlib/metadata/mb_client.py

from functools import lru_cache

# Cache search results for current session
@lru_cache(maxsize=500)
def search_recording_cached(artist: str, title: str, duration: int | None = None) -> RecordingMatch | None:
    """Cached version of search_recording for batch operations."""
    return search_recording(artist, title, duration)

@lru_cache(maxsize=500)  
def get_recording_genres_cached(recording_id: str, release_group_id: str | None = None, artist_id: str | None = None) -> List[str]:
    """Cached version of get_recording_genres."""
    return get_recording_genres(recording_id, release_group_id=release_group_id, artist_id=artist_id)
```

**Expected savings:** 2-3s per track (50% of MB calls eliminated)

#### 3.1.2 Skip genre_resolver When MB Has Genres

```python
# djlib/enrich.py - in suggest_metadata()

online = lookup_musicbrainz(artist, title)
if online:
    # If MB already provided genres, skip genre_resolver
    if online.get("genre_suggest") and not force_full_resolution:
        return online
    
    # Only call genre_resolver if MB didn't find genres
    # ... existing genre_resolver code ...
```

**Expected savings:** 3-4s per track for ~60% of tracks

### 3.2 Phase 2: Parallel Processing (2-3 days effort)

#### 3.2.1 Parallel API Calls in genre_resolver

```python
# djlib/metadata/genre_resolver.py

import concurrent.futures
from typing import Callable, Any

def resolve(artist: str, title: str, version: str = "", *, 
            duration_s: int | None = None,
            disable_soundcloud: bool = False, 
            disable_beatport: bool = False,
            mb_data: dict | None = None) -> GenreResolution | None:  # NEW: accept pre-fetched MB data
    """
    Resolve genres using parallel API calls.
    
    Args:
        mb_data: Pre-fetched MusicBrainz data to avoid redundant calls
    """
    scores: Dict[str, float] = {}
    parts: List[Tuple[str, float, Dict[str, float]]] = []
    
    # Define tasks for parallel execution
    tasks = {}
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        # Submit all API calls in parallel
        if not disable_beatport:
            tasks['beatport'] = executor.submit(_fetch_beatport, artist, title, duration_s)
        
        if mb_data is None:  # Only fetch if not provided
            tasks['musicbrainz'] = executor.submit(_fetch_musicbrainz, artist, title, duration_s)
        
        tasks['lastfm'] = executor.submit(_fetch_lastfm, artist, title)
        
        if not disable_soundcloud:
            tasks['soundcloud'] = executor.submit(_fetch_soundcloud, artist, title, version)
        
        # Collect results with timeout
        results = {}
        for name, future in tasks.items():
            try:
                results[name] = future.result(timeout=10)
            except Exception:
                results[name] = None
    
    # Process results (existing scoring logic)
    # ...
```

**Expected savings:** 3-5s per track (parallel vs sequential)

#### 3.2.2 Early Exit for Beatport EDM Matches

```python
# djlib/metadata/genre_resolver.py

# EDM genres where Beatport is authoritative
BEATPORT_AUTHORITATIVE = {
    'house', 'tech house', 'deep house', 'progressive house', 'afro house',
    'techno', 'melodic techno', 'minimal techno', 'hard techno',
    'trance', 'psytrance', 'progressive trance',
    'drum and bass', 'dnb', 'jungle',
    'dubstep', 'bass', 'future bass',
    'electro', 'electro house',
}

def resolve(...):
    # Beatport first (gold standard for EDM)
    if not disable_beatport:
        bp_result = _fetch_beatport(artist, title, duration_s)
        if bp_result:
            bp_genre = bp_result.get('genre', '').lower()
            # Check if any word matches authoritative genres
            for auth_genre in BEATPORT_AUTHORITATIVE:
                if auth_genre in bp_genre:
                    # HIGH CONFIDENCE - skip other sources
                    return GenreResolution(
                        main=canonical(bp_result['genre']),
                        subs=[],
                        confidence=0.95,
                        breakdown=[('beatport', 10.0, {canonical(bp_result['genre']): 10.0})]
                    )
    
    # Continue with full resolution for non-EDM...
```

**Expected savings:** 2-4s for ~40% of tracks (EDM library)

### 3.3 Phase 3: Polish (1 day effort)

#### 3.3.1 Reduce SoundCloud Queries

```python
# djlib/metadata/soundcloud.py

def _candidate_queries(artist: str, title: str, version: str) -> List[str]:
    """Generate optimized query list (max 2 queries)."""
    base = f"{artist} {title}".strip()
    if not base:
        return []
    
    primary_remixer = _extract_primary_remixer(version)
    
    # Only 2 most effective queries
    if primary_remixer:
        return [
            f"{artist} {primary_remixer}",  # Most specific
            base,  # Fallback
        ]
    return [base]
```

**Expected savings:** 1-2s per track

#### 3.3.2 Enhanced Progress Reporting

```python
# djlib/cli.py - cmd_enrich_online()

import time

class EnrichStats:
    def __init__(self):
        self.start_time = time.time()
        self.tracks_processed = 0
        self.total_tracks = 0
        self.source_times = {'beatport': 0, 'musicbrainz': 0, 'lastfm': 0, 'soundcloud': 0}
    
    def report_progress(self, current_track: str):
        elapsed = time.time() - self.start_time
        avg_per_track = elapsed / max(self.tracks_processed, 1)
        remaining = (self.total_tracks - self.tracks_processed) * avg_per_track
        
        print(f"\r⏳ {self.tracks_processed}/{self.total_tracks} "
              f"| ETA: {int(remaining)}s "
              f"| Avg: {avg_per_track:.1f}s/track "
              f"| {current_track[:40]}", end='', flush=True)
```

---

## 4. Implementation Plan

### Week 1: Foundation

| Day | Task | Owner | Status |
|-----|------|-------|--------|
| 1 | Add LRU cache to mb_client.py | Dev | ⬜ TODO |
| 1 | Add `mb_data` parameter to resolve_genres() | Dev | ⬜ TODO |
| 2 | Skip genre_resolver when MB has genres | Dev | ⬜ TODO |
| 2 | Unit tests for caching | Dev | ⬜ TODO |
| 3 | Integration test with real tracks | QA | ⬜ TODO |

### Week 2: Parallelization

| Day | Task | Owner | Status |
|-----|------|-------|--------|
| 1-2 | Implement ThreadPoolExecutor in genre_resolver | Dev | ⬜ TODO |
| 2 | Beatport early-exit for EDM | Dev | ⬜ TODO |
| 3 | Reduce SoundCloud queries | Dev | ⬜ TODO |
| 3 | Performance benchmarks | QA | ⬜ TODO |

### Week 3: Polish & Release

| Day | Task | Owner | Status |
|-----|------|-------|--------|
| 1 | Enhanced progress reporting | Dev | ⬜ TODO |
| 2 | Documentation update | Dev | ⬜ TODO |
| 2-3 | Full regression testing | QA | ⬜ TODO |
| 3 | Release | Team | ⬜ TODO |

---

## 5. Success Metrics

### Performance KPIs

| Metric | Current | Target | Measurement |
|--------|---------|--------|-------------|
| Avg time per track | 12-18s | 4-6s | `enrich_status.json` timestamps |
| MB API calls per track | 3-6 | 1-2 | Counter in mb_client.py |
| 100 tracks batch time | 15-40 min | 5-10 min | End-to-end test |
| Cache hit rate | 0% | >50% | LRU cache stats |

### Quality KPIs

| Metric | Current | Target | Measurement |
|--------|---------|--------|-------------|
| Genre accuracy | Baseline | Same or better | Manual review sample |
| API errors | Baseline | Same or lower | Error logs |
| User satisfaction | - | Positive | Feedback |

---

## 6. Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Parallel calls cause rate limit issues | High | Medium | Add per-API semaphores |
| Cache invalidation issues | Medium | Low | Clear cache between runs |
| Beatport early-exit misses non-EDM | Medium | Low | Keep full resolution as fallback |
| SoundCloud fewer queries = worse results | Low | Medium | A/B test before full rollout |

---

## 7. Appendix

### A. Files to Modify

1. `djlib/metadata/mb_client.py` — Add LRU caching
2. `djlib/metadata/genre_resolver.py` — Parallel calls, early exit, accept MB data
3. `djlib/enrich.py` — Pass MB data to genre_resolver, skip when genres exist
4. `djlib/metadata/soundcloud.py` — Reduce query count
5. `djlib/cli.py` — Enhanced progress reporting

### B. Testing Commands

```bash
# Run enrich on small batch with timing
time python -m djlib.cli enrich-online

# Profile specific functions
python -m cProfile -s cumtime -m djlib.cli enrich-online 2>&1 | head -50

# Check MB call count (add logging)
DJLIB_DEBUG_MB=1 python -m djlib.cli enrich-online
```

### C. Related Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — System overview
- [MVP_v1.md](MVP_v1.md) — Original requirements
- MusicBrainz API docs: https://musicbrainz.org/doc/MusicBrainz_API
- Beatport API (internal): See `djlib/metadata/beatport.py` docstrings

---

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2026-02-05 | CTO Analysis | Initial draft |
