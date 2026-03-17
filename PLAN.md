# Plan: Essentia Interpreter v3 — Diagnostic Questions

## Goal
Replace the current 6-descriptor EI prompt with 5 targeted diagnostic questions.
Current EI adds no value (v2: nano+EI 51.0% vs nano 51.5%). Hypothesis: descriptors
are too abstract. Diagnostic questions force GPT to answer concrete, genre-relevant
characteristics.

## Why v3?
- EI v2 prompt asks for "rhythm character", "timbre brightness" — too vague
- v3 asks "what kind of kick pattern?", "what production style?" — directly maps to genres
- Zero code change needed (prompt-only), cheapest experiment

## Variants
- `nano+EI3` — filename metadata + Essentia features → v3 interpreter

## Steps
1. Write new prompt: `data/ab_test/prompts/interpreter_essentia_v3.md`
   - 5 diagnostic questions:
     1. Kick/drum pattern type (4-on-floor, breakbeat, halftime, none)
     2. Production style (clean/polished, raw/distorted, organic/live, lo-fi)
     3. Frequency character (sub-bass heavy, mid-focused, bright/airy, full-range)
     4. Texture/atmosphere (minimal, layered, atmospheric, aggressive)
     5. Harmonic content (melodic, atonal, chord-driven, sample-based)
   - Output: 5 one-line answers, no genre names
2. Add v3 interpreter call in pre-computation phase
3. Register `nano+EI3` variant with new prefix detection
4. Tests

## Success criteria
- `nano+EI3` > `nano+EI` (51.0%) — any improvement validates diagnostic approach
- Ideally `nano+EI3` ≥ `nano` (51.5%)
