# Plan: V3 AB Test Run — Integration & Execution

## Goal
Merge all new audio signal variants into one branch and execute V3 AB test.

## V3 Variant Matrix (8 variants × 200 tracks = 1600 GPT calls)
| Variant        | Filename | WS | EI (v2) | EI3 (v3) | CLAP | Gemini |
|----------------|----------|----|---------|----------|------|--------|
| nano           | ✓        |    |         |          |      |        |
| nano+WS        | ✓        | ✓  |         |          |      |        |
| nano+EI        | ✓        |    | ✓       |          |      |        |
| nano+EI3       | ✓        |    |         | ✓        |      |        |
| nano+CLAP      | ✓        |    |         |          | ✓    |        |
| nano+CLAP+WS   | ✓        |    |         |          | ✓    | ✓      |
| nano+GA        | ✓        |    |         |          |      | ✓      |
| nano+GA+WS     | ✓        |    |         |          |      | ✓      |

## Steps
1. Cherry-pick / merge from feature/ab-clap, feature/ab-gemini-audio, feature/ab-essentia-v3
2. Resolve any conflicts in ab_test_genre.py
3. Verify all 8 variants register + routing works
4. Dry-run: `python scripts/ab_test_genre.py --variants nano --max-tracks 5`
5. Pre-compute all caches (CLAP ~15min, Gemini ~15min with rate limiting, EI3 ~5min)
6. Full run: `python scripts/ab_test_genre.py --max-tracks 200 --concurrency 16`
7. Analyze results: per-variant accuracy, per-family breakdown, confusion matrices
8. Tag: `v3-results`

## Success criteria
- At least one new signal beats `nano+WS` (59.5%)
- Clear signal hierarchy emerges (which audio perception adds most value)

## Budget
- GPT-5-nano: 1600 calls × ~$0.001 = ~$1.60
- Gemini Flash: 400 calls × ~$0.005 = ~$2.00
- CLAP: free (local CPU)
- Total: ~$4
