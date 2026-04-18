# Plan: Gemini Audio Genre Signal

## Goal
Use Google Gemini's multimodal audio understanding to generate rich sonic descriptions.
Gemini "listens" to audio — perceives bass type, hi-hat patterns, sample aesthetics.
It DESCRIBES the sound (no genre names), then our GPT-5-nano CLASSIFIES.

## Why Gemini Audio?
- Genuine audio perception (trained on audio+text jointly)
- Can hear "type of bass", "hi-hat pattern" — things Essentia can't
- Separating perception (Gemini) from classification (GPT-5-nano) keeps comparison fair

## Variants
- `nano+GA` — filename metadata + Gemini audio description
- `nano+GA+WS` — filename + Gemini + web search (ceiling test)

## Steps
1. Add `google-genai` dependency + API key config
2. `run_gemini_audio_analysis()` — 30s clip → Gemini → sonic description
3. Persistent cache: `data/ab_test/gemini_audio_cache.json`
4. Rate limiting (15 RPM free tier → sleep 4s)
5. Register variants + routing + prompt framing
6. Tests + smoke test

## Key design: Gemini DESCRIBES, GPT-5-nano CLASSIFIES
Fair comparison — same classifier, different audio signals.

## Cost: ~$2 for 200 tracks (Gemini 1.5 Flash)

## Success criteria
- `nano+GA` > `nano+EI` (51.0%)
- `nano+GA+WS` > `nano+WS` (59.5%)
