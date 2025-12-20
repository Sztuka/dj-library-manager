# Możliwe usprawnienia enrich-online workflow

**Data analizy:** 2024-12-20  
**Obecny stan:** Workflow działa poprawnie, ale jest kilka obszarów do potencjalnej optymalizacji.

---

## 🔍 Przegląd obecnej architektury

### Przepływ danych (sekwencyjny, per-track)
```
dla każdego tracka:
  1. derive_local_metadata() → (artist, title, version)
  2. lookup_acoustid()       → fingerprint → MB recording → metadata
     lub lookup_musicbrainz() → text search → recording → metadata
  3. genre_resolver.resolve() → Beatport + MB + Last.fm + SoundCloud
  4. Archive.org search (jeśli live recording)
  5. Cover art URL (MB CAA → Beatport → Last.fm → SoundCloud)
```

### Obecne źródła API (per track)
| Źródło | Zapytań/track | Rate limit | Cache |
|--------|---------------|------------|-------|
| AcoustID | 1 | brak info | nie |
| MusicBrainz | 2-6 | 1 req/s | requests-cache (14 dni) |
| Last.fm | 1-2 | 5 req/s | requests-cache (14 dni) |
| Beatport | 1-2 | 1 req/s | nie |
| SoundCloud | 1-3 | ~2 req/s | @lru_cache(1000) |
| Archive.org | 0-2 | brak limit | nie |

---

## ⚡ OPTYMALIZACJE WYSOKIEGO PRIORYTETU

### 1. **Batching MusicBrainz requests** 
**Potencjalne przyspieszenie: 30-50%**

**Problem:** Każdy track wykonuje 2-6 zapytań do MusicBrainz (search → recording → release-group → artist).

**Rozwiązanie:**
```python
# Zamiast per-track:
for track in tracks:
    mb_client.search_recording(artist, title)
    mb_client._get_recording_by_id(rid)
    mb_client._get_release_group_by_id(rgid)

# Zrobić batch prefetch:
# 1. Zebrać wszystkie (artist, title) z batch
# 2. Wykonać search_recordings z wieloma query naraz
# 3. Cache wyniki w pamięci
# 4. Przetwarzać tracks używając cache
```

**Implementacja:** Nowa funkcja `batch_prefetch_musicbrainz(tracks)` która:
- Grupuje zapytania
- Respektuje rate limit (1/s) ale wykonuje je "z góry"
- Zapisuje wyniki do dict-cache na czas sesji

---

### 2. **Parallel cover art fetching**
**Potencjalne przyspieszenie: 40-60% (dla --fetch-covers)**

**Problem:** Cover art fetch jest sekwencyjny, każde źródło czeka na poprzednie.

**Obecny flow:**
```python
# Sekwencyjnie (blokuje):
url = get_cover_art_url(...)  # MB → Beatport → Last.fm → SoundCloud
```

**Rozwiązanie:** `concurrent.futures.ThreadPoolExecutor` dla cover art:
```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def get_cover_art_url_parallel(...) -> str:
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(try_musicbrainz_cover, ...): 'mb',
            executor.submit(try_beatport_cover, ...): 'beatport',
            executor.submit(try_lastfm_cover, ...): 'lastfm',
            executor.submit(try_soundcloud_cover, ...): 'soundcloud',
        }
        
        # Zwróć pierwszy sukces według priorytetu
        for future in as_completed(futures):
            result = future.result()
            if result:
                return result
    return None
```

**Uwaga:** Zachować priorytet źródeł (MB > Beatport > Last.fm > SC) ale pobierać równolegle.

---

### 3. **Skip redundant API calls for cached data**
**Potencjalne przyspieszenie: 20-30% przy kolejnych przebiegach**

**Problem:** Nawet gdy `genre_suggest` jest już wypełnione, `genre_resolver.resolve()` jest wywoływany ponownie.

**Rozwiązanie:**
```python
# W cmd_enrich_online():
if r.get("genre_suggest") and r.get("meta_source") != "filename|tags_fallback":
    # Dane już wzbogacone z online - pomiń resolve()
    continue
```

---

## 🔄 OPTYMALIZACJE ŚREDNIEGO PRIORYTETU

### 4. **Dedupe genre_resolver calls**
**Problem:** `genre_resolver.resolve()` może być wywoływany 2x dla tego samego tracka (raz w `suggest_metadata`, raz w `enrich_online_for_row`).

**Rozwiązanie:** Flaga `genres_resolved=True` w zwracanym dict, sprawdzana przed ponownym wywołaniem.

---

### 5. **Lazy Beatport token refresh**
**Problem:** Token sprawdzany przy starcie nawet gdy żaden track nie wymaga Beatport.

**Rozwiązanie:** Sprawdzać token dopiero przy pierwszym użyciu Beatport:
```python
_beatport_token_checked = False

def ensure_beatport_token():
    global _beatport_token_checked
    if not _beatport_token_checked:
        get_valid_token()  # Może triggerować Playwright refresh
        _beatport_token_checked = True
```

---

### 6. **Archive.org search caching**
**Problem:** `archive_org.search_by_artist_title_duration()` nie jest cache'owane.

**Rozwiązanie:**
```python
@lru_cache(maxsize=500)
def search_by_artist_title_duration(artist: str, title: str, duration_seconds: float, tolerance: float = 1.0):
    ...
```

---

## 📊 OPTYMALIZACJE NISKIEGO PRIORYTETU (future work)

### 7. **Async I/O z aiohttp**
**Potencjalne przyspieszenie: 50-70% (wymaga refactor)**

Zamiana `requests` na `aiohttp` pozwoliłaby na:
- Równoległe zapytania do różnych API
- Non-blocking I/O
- Znacznie wyższą przepustowość

**Koszt:** Duży refactor, wszystkie funkcje API muszą być `async def`.

**Przykład:**
```python
async def enrich_batch_async(tracks: List[dict]) -> List[dict]:
    async with aiohttp.ClientSession() as session:
        tasks = [enrich_single_async(session, t) for t in tracks]
        return await asyncio.gather(*tasks)
```

---

### 8. **Pre-flight canonical lookup**
**Problem:** `lookup_canonical_release()` jest szybkie (SQLite), ale wywoływane po AcoustID.

**Rozwiązanie:** Najpierw sprawdzić canonical DB (0ms) → jeśli hit, pomiń AcoustID:
```python
def suggest_metadata(path, tags, enable_online=True):
    artist, title, version = derive_local_metadata(path, tags)
    
    # Fast path: canonical DB hit (no API needed)
    if not version:  # originals only
        canonical = lookup_canonical_release(artist, title)
        if canonical and canonical.get('release_year'):
            return build_result_from_canonical(canonical)
    
    # Slow path: AcoustID + MusicBrainz API
    ...
```

---

### 9. **Incremental processing checkpoint**
**Problem:** Przy dużych bibliotekach (1000+ tracks) crash/przerwanie = restart od zera.

**Rozwiązanie:**
```python
# Zapisuj checkpoint co N tracków
CHECKPOINT_INTERVAL = 50

for i, track in enumerate(tracks):
    process_track(track)
    
    if i % CHECKPOINT_INTERVAL == 0:
        save_checkpoint(i, rows)

# Przy starcie: sprawdź checkpoint i wznów
```

---

## 📈 Podsumowanie priorytetów

| # | Optymalizacja | Przyspieszenie | Trudność | Ryzyko |
|---|---------------|----------------|----------|--------|
| 1 | Batching MB requests | 30-50% | Średnia | Niskie |
| 2 | Parallel cover art | 40-60% | Niska | Niskie |
| 3 | Skip cached data | 20-30% | Niska | Niskie |
| 4 | Dedupe genre_resolver | 10-15% | Niska | Niskie |
| 5 | Lazy Beatport token | 5-10% | Niska | Niskie |
| 6 | Archive.org cache | 5-10% | Niska | Niskie |
| 7 | Async aiohttp | 50-70% | Wysoka | Średnie |
| 8 | Pre-flight canonical | 10-20% | Średnia | Niskie |
| 9 | Incremental checkpoint | N/A | Niska | Niskie |

---

## ✅ Rekomendacja

**Faza 1 (quick wins, bez ryzyka):**
- #3 Skip cached data
- #5 Lazy Beatport token  
- #6 Archive.org cache

**Faza 2 (znaczące przyspieszenie):**
- #2 Parallel cover art
- #4 Dedupe genre_resolver

**Faza 3 (większy refactor):**
- #1 Batching MB requests
- #8 Pre-flight canonical

**Future (major refactor):**
- #7 Async aiohttp (wymaga przepisania całego metadata layer)

---

## 🔬 Metryki do monitorowania

Po wdrożeniu optymalizacji warto mierzyć:
1. **Czas per track** (średni, P95)
2. **Liczba API calls per track**
3. **Cache hit ratio** (requests-cache, lru_cache)
4. **Czas cover art fetch** (z --fetch-covers vs bez)

Można dodać prosty timing:
```python
import time

class EnrichTimer:
    def __init__(self):
        self.timings = {}
    
    def time(self, label: str):
        return self._timer_context(label)
    
    @contextmanager
    def _timer_context(self, label):
        start = time.perf_counter()
        yield
        self.timings[label] = self.timings.get(label, 0) + (time.perf_counter() - start)
```
