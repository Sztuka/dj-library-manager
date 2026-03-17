# Plan: CLAP Zero-Shot Genre Classification

## Goal
Add CLAP (Contrastive Language-Audio Pretraining) as a new audio signal for AB test.
CLAP matches audio embeddings against text descriptions of our 48 genres — zero-shot, no fine-tuning.

## Why CLAP?
- Essentia 20 features can't distinguish House subgenres (centroid/MFCC too coarse)
- CLAP "understands" audio↔text similarity: "warm filtered bassline, offbeat hi-hats" → Deep House
- Local, free, cacheable (512-dim embedding per track)
- No fine-tuning needed — we write genre descriptions, CLAP does the matching

## Variants
- `nano+CLAP` — filename metadata + CLAP audio similarity scores
- `nano+CLAP+WS` — filename + CLAP + web search (hybrid)

## Steps

### 1. Dependencies
- [ ] Add `msclap` to requirements.txt (optional dep, ~2GB model download)
- [ ] Add try/except import in ab_test_genre.py (graceful fallback if not installed)

### 2. Genre descriptions file
- [ ] Create `data/ab_test/clap_genre_descriptions.json`
- [ ] Write 1-2 sentence SONIC description per genre (48 entries)
  - Focus on: bass type, rhythm pattern, hi-hat style, energy, production aesthetics
  - Do NOT mention artist names, labels, or scenes — only sonic character

### 3. CLAP analysis functions (in ab_test_genre.py)
- [ ] `_load_clap_model()` — singleton loader
- [ ] `run_clap_analysis(file_path, genre_descriptions)` → top-10 similarities
- [ ] `describe_clap_features(analysis)` → formatted text with prefix

### 4. Register variants + routing + prompt framing

### 5. Tests + smoke test

## Success criteria
- `nano+CLAP` accuracy > `nano+EI` (51.0% in v2)
- `nano+CLAP+WS` accuracy ≥ `nano+WS` (59.5% in v2)
