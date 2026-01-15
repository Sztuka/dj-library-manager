# Możliwe usprawnienia jakości i prędkości (MVP v1)

**Data analizy:** 2026-01-15  
**Obecny stan:** Workflow działa poprawnie. Cover art fetch online jest wyłączony (stała okładka lokalna). Poniżej tylko realne, aktualne kierunki poprawy jakości i szybkości.

---

## 🔍 Przegląd obecnej architektury (enrich-online)

### Przepływ danych (sekwencyjny, per-track)

```text
dla każdego tracka:
  1. derive_local_metadata() → (artist, title, version)
  2. lookup_acoustid()       → fingerprint → MB recording → metadata
     lub lookup_musicbrainz() → text search → recording → metadata
  3. genre_resolver.resolve() → Beatport + MB + Last.fm + SoundCloud
  4. Archive.org search (jeśli live recording)
```

### Obecne źródła API (per track)

| Źródło      | Zapytań/track | Rate limit | Cache                   |
| ----------- | ------------- | ---------- | ----------------------- |
| AcoustID    | 1             | brak info  | nie                     |
| MusicBrainz | 2-6           | 1 req/s    | requests-cache (14 dni) |
| Last.fm     | 1-2           | 5 req/s    | requests-cache (14 dni) |
| Beatport    | 1-2           | 1 req/s    | nie                     |
| SoundCloud  | 1-3           | ~2 req/s   | @lru_cache(1000)        |
| Archive.org | 0-2           | brak limit | nie                     |

---

## ✅ PRIORYTET: JAKOŚĆ METADANYCH

### 1. Stricter match gating (artist/title/version)

**Cel:** ograniczyć false-positive z Beatport/Last.fm/SC.

**Propozycja:**

- Wspólny matcher (fuzzy + normalizacja): artist/title/version.
- Minimalny próg dopasowania (np. 0.85) per źródło.
- Jeśli niedopasowanie → źródło ignorowane i oznaczenie `meta_quality = "low"`.

**Zysk:** mniej błędnych genre/roków dla non‑EDM.

---

### 2. Consensus requirement dla gatunków non‑EDM

**Cel:** podbić jakość genre poza EDM.

**Propozycja:**

- Jeśli Beatport nie pasuje → wymagać zgodności co najmniej 2 źródeł (MB + Last.fm albo MB + SC).
- Gdy brak konsensusu → `genre_suggest` = puste + `status = review`.

**Zysk:** mniej mylnych gatunków (rock/pop vs EDM).

---

### 3. Źródło roku: preferencja canonical

**Cel:** stabilny rok wydania bez kompilacji.

**Propozycja:**

- Jeśli canonical MB jest dostępny → zawsze nadrzędny dla `original_release_year`.
- W przeciwnym wypadku: MB → Last.fm → fallback.

**Zysk:** mniej „losowych” lat z kompilacji.

---

## ⚡ PRIORYTET: SZYBKOŚĆ

### 4. Skip redundant API calls (cache-aware)

Potencjalne przyspieszenie: 20-30%

**Propozycja:**

```python
if r.get("genre_suggest") and r.get("meta_source") != "filename|tags_fallback":
    continue
```

---

### 5. Dedupe genre_resolver calls

**Cel:** uniknąć podwójnych wywołań na ten sam track.

**Propozycja:**

- Flaga `genres_resolved=True` w rezultacie `suggest_metadata`.

---

### 6. Pre-flight canonical lookup (MusicBrainz)

Potencjalne przyspieszenie: 10-20%

**Propozycja:**

```python
if not version:
    canonical = lookup_canonical_release(artist, title)
    if canonical and canonical.get("release_year"):
        return build_result_from_canonical(canonical)
```

---

### 7. Lazy Beatport token refresh

Potencjalne przyspieszenie: 5-10%

Token odświeżany dopiero przy pierwszym realnym użyciu Beatport.

---

### 8. Archive.org caching

Potencjalne przyspieszenie: 5-10%

**Propozycja:**

```python
@lru_cache(maxsize=500)
def search_by_artist_title_duration(...):
    ...
```

---

### 9. Batching MusicBrainz requests

Potencjalne przyspieszenie: 30-50%

**Propozycja:**

- Batch prefetch (artist, title) → cache w pamięci.
- Respektować rate limit 1 req/s.

---

## 📊 PRIORYTETY I RYZYKO

| #   | Optymalizacja             | Wpływ       | Trudność | Ryzyko |
| --- | ------------------------- | ----------- | -------- | ------ |
| 1   | Stricter match gating     | Jakość ↑↑   | Średnia  | Niskie |
| 2   | Consensus non‑EDM         | Jakość ↑↑   | Średnia  | Niskie |
| 3   | Canonical year preference | Jakość ↑    | Niska    | Niskie |
| 4   | Skip cached               | Szybkość ↑  | Niska    | Niskie |
| 5   | Dedupe resolve            | Szybkość ↑  | Niska    | Niskie |
| 6   | Pre-flight canonical      | Szybkość ↑  | Średnia  | Niskie |
| 7   | Lazy Beatport token       | Szybkość ↑  | Niska    | Niskie |
| 8   | Archive.org cache         | Szybkość ↑  | Niska    | Niskie |
| 9   | MB batching               | Szybkość ↑↑ | Średnia  | Niskie |

---

## ✅ Rekomendacja (kolejność)

**Faza 1 — jakość i szybkie zyski:**

- #1 Stricter match gating
- #2 Consensus non‑EDM
- #4 Skip cached

**Faza 2 — szybkość bez ryzyka:**

- #5 Dedupe resolve
- #7 Lazy Beatport token
- #8 Archive.org cache

**Faza 3 — większe zyski:**

- #6 Pre-flight canonical
- #9 MB batching

---

## 🔬 Metryki do monitorowania

1. **Czas per track** (średni, P95)
2. **Liczba API calls per track**
3. **Cache hit ratio** (requests-cache, lru_cache)
4. **% rekordów z `status=review`** po zmianach jakościowych
