# Decision History (derived from full git history)

This document captures **why** the project changed over time, based on all commits from project start to current HEAD.

Source of truth:

- Full chronological log: `docs/COMMIT_CHRONOLOGY.md`
- Generated via `git log --reverse --date=short --pretty=format:"%ad | %h | %s"`

---

## How to use this as context for future iterations

When you (or an AI agent) consider a new iteration:

1. Check whether the topic already appears in the timeline below.
2. If yes, inspect the listed commit hashes in `docs/COMMIT_CHRONOLOGY.md`.
3. Reuse prior constraints (data safety, UX speed, deterministic fallbacks) unless there is explicit evidence they no longer hold.
4. Treat repeated fix patterns as architectural signals, not random bugs.

---

## High-level evolution (oldest → newest)

## Phase 1 — Bootstrapping + early UI-driven setup (Oct 2025)

**Key decision:** start with setup wizard and web-first onboarding, then quickly tighten scope.

What happened:

- Initial skeleton introduced core project shape (CLI + web + task model).
- Setup wizard/dashboard and taxonomy-related setup were expanded rapidly.
- Discogs was removed early; source strategy narrowed.

Why this matters now:

- Early experimentation showed that broad setup UX is useful, but too many optional integrations increase maintenance and noise.
- Future onboarding changes should stay minimal and operationally focused.

Representative commits:

- `7e37535`, `3be0e43`, `d311709`, `2648e31`, `c220ee0`, `59ccfce`, `84dfebd`.

## Phase 2 — Backend-first consolidation + enrichment pipeline hardening (Nov 2025)

**Key decision:** de-emphasize UI complexity and invest in deterministic pipeline behavior.

What happened:

- UI components were pruned for backend-first development.
- API/config system stabilized.
- Essentia audio analysis scaffold + CLI commands introduced.
- Genre normalization converged toward canonical resolver with `genres.yml` as single source.
- Full DJ software integration matured.

Why this matters now:

- The repo favors reliability and repeatable workflows over presentation-layer complexity.
- Any feature that bypasses canonical resolver or adds parallel taxonomy logic likely regresses past decisions.

Representative commits:

- `8fd98f5`, `0929f6e`, `c8edb07`, `1f2a71d`, `2d61663`, `e398d2d`, `227efb6`.

## Phase 3 — Taxonomy retirement + logistics/data-safety model (Dec 2025)

**Key decision:** remove legacy taxonomy system and treat folder placement as logistics, not genre identity.

What happened:

- Legacy taxonomy was removed from code and docs.
- Missing-file handling, mapping fixes, and defensive prompts added.
- Cover art support expanded across formats.
- Duplicate-protection and sync correctness got priority fixes.

Why this matters now:

- This is a foundational product choice: metadata genre and filesystem destination are separate concerns.
- Data integrity and sync correctness beat feature novelty.

Representative commits:

- `6ad1796`, `ae8e564`, `5e55c2c`, `626ebec`, `343f86f`, `7afb231`.

## Phase 4 — Safety rails around apply/sync + dedup + edge-case recovery (Jan 2026)

**Key decision:** prioritize irreversible-operation safety (apply/move/sync) over new enrichment breadth.

What happened:

- Genre resolver weighting tuned (specificity boosts).
- Recovery step for lost files added.
- Hard guards for missing `rekordbox_id` and duplicate handling introduced.
- Destination-specific handling clarified for reject/archive.

Why this matters now:

- The project explicitly chose conservative behavior around file moves and DJ IDs.
- Any future "automation" must remain reversible and identifier-safe.

Representative commits:

- `683c955`, `215064d`, `be824ba`, `9df4ef8`, `cb30bce`, `ade1980`.

## Phase 5 — Enrichment speed and quality optimization at scale (Feb 2026)

**Key decision:** optimize enrich-online through staged performance work + targeted bugfix loops.

What happened:

- Performance branches merged in phases (phase1/phase2).
- Genre map and resolver rules iterated quickly (EDM specifics, remix/version parsing).
- Multiple production hotfixes for SoundCloud/Beatport edge cases.
- Filename normalization and Unicode handling hardened.
- Review UI evolved heavily for 2-second decision UX (batch actions, richer columns, processed/library views).

Why this matters now:

- Repeated fix density indicates enrichment inputs are noisy by nature; robust fallbacks are mandatory.
- UI changes in this period optimize operator throughput, not cosmetics.

Representative commits:

- `209c96f`, `dfa73d1`, `12537e4`, `88d9f61`, `5ea215a`, `3b4d0e0`, `ab5baf9`, `c0802ac`.

## Phase 6 — AI-assisted review workflows + prompt/AB discipline (Mar 2026)

**Key decision:** add AI in the review loop, but force it through measurable evaluation and controlled prompts.

What happened:

- AI suggest / identify / chat capabilities landed in Review UI.
- Web search and configurable model support were added.
- Fast follow-up fixes addressed hallucination/leak risks and UX edge cases.
- AB-test infrastructure and `gold_labels.json` became evaluation anchor.
- Taxonomy was rewritten to 48-genre family structure, then prompt hierarchy tuned.

Why this matters now:

- AI features are accepted only when paired with auditability (gold labels, AB tests, explicit prompt constraints).
- Prompt changes without regression evaluation conflict with established decision direction.

Representative commits:

- `1fc1a03`, `bfa49d5`, `1872b8d`, `fed8090`, `499cefb`, `516356e`, `5f04c1c`, `0811ad0`, `f7cc2fd`.

## Phase 7 — Genre classifier signal search + production winner confirmed (Apr 2026)

**Key decision:** systematically test every enrichment signal (Last.fm, MusicBrainz, AcoustID, web search variants, model upgrades, prompt architectures) against a 200-track gold set to find the production-ready classification method.

What happened:

- Last.fm count-weighted tags (+LF) added +4pp over web-search baseline → new ceiling at **75.5% exact / 90.5% family**. Only signal that consistently helped.
- MusicBrainz (+MB / +MBL filtered to taxonomy) regressed -3pp. Multi-style artist tag aggregation is noisy even when constrained to canonical labels.
- AcoustID fingerprinting (+FP) gave 5 wins on orphan filenames but 4 losses on clean filenames where FP matched wrong recording. Net: 0.
- Conditional FP gate (+FPC): `filename_is_orphan()` detects tracks with no parseable artist/title (artist==title, leading track number, VA). Gate correctly isolated 31 orphans; +3 wins / -1 loss on orphans, but nano non-determinism (~2pp noise) masked the signal at the aggregate level.
- Prompt confusion hints (+P56), structured web search extraction (+WSX), model upgrade to mini: all regressed or gave no benefit.
- Two-step family→subgenre (+T2): -16pp catastrophic regression. Splitting into family-first call strips the rich artist→subgenre vocabulary the model uses to reason, and locks errors from step 1 into step 2.
- Majority vote 3× (+V3): 75.0% — statistically tied with baseline but 3× token cost and 12 timeout errors from sequential triple-calls per thread.

**Confirmed production winner: `nano+WS+LF`** (gpt-5-nano + SearXNG web search + Last.fm tags). All code and variant infrastructure lives in `scripts/ab_test_genre.py`.

Why this matters now:

- The ceiling for filename+web search+Last.fm is ~75.5%. Extracting more requires either better gold labels, fundamentally different signal sources, or a production-scale approach (e.g. Beatport direct API, real audio analysis).
- Two-step decomposition (family→subgenre) is a dead end for this model/taxonomy combination — do not retry without strong evidence the failure mode has changed.
- nano gpt-5-nano has ~2pp run-to-run non-determinism. Effects smaller than 3pp cannot be reliably measured in a single run; majority vote does not help enough to justify the cost.
- Already-tried and confirmed ineffective: MusicBrainz tag aggregation, always-on AcoustID, larger model (gpt-5-mini), two-step prompting, confusion-hint lists.

Representative commits:

- `470fee6` (LF/MB/FP/FPC/P56 variants), `589b816` (T2 negative result), `8ddf1a1` (V3 majority vote).

---

## Cross-cutting decisions that repeated across the entire history

1. **Data integrity over convenience**
   - Recurring fixes around duplicates, IDs, move safety, and sync correctness.
   - Practical implication: never trade `track_id`/DJ ID stability for short-term UX convenience.

2. **Canonicalization over free-form metadata**
   - Repeated convergence toward canonical genre resolver and single-source genre definitions.
   - Practical implication: avoid introducing alternate parallel normalization paths.

3. **Fast operator workflow over decorative UI**
   - Review UI changes repeatedly focused on batch operations, reduced clicks, and immediate editability.
   - Practical implication: UI proposals should prove reduced decision time.

4. **Measured AI over "magic" AI**
   - AI scope expanded, but paired with AB tests, gold labels, and prompt hardening.
   - Practical implication: new AI behavior should include evaluation hooks from day one.

5. **Incremental merges over big-bang rewrites**
   - Many feature/fix branches merged with tightly scoped commits and fast stabilization.
   - Practical implication: prefer narrow slices + regression tests over monolithic refactors.

---

## Already-tried directions (avoid re-learning the same lesson)

- **Large taxonomy-centric architecture as primary organizing model** was tried and then retired.
- **Overly broad source/integration surface** (high-maintenance metadata sources) was narrowed.
- **Unconstrained AI behavior in review workflows** required subsequent fixes; controlled prompts + evaluation won.
- **Assuming clean filenames/ASCII-only paths** repeatedly failed; Unicode/normalization handling became required.
- **MusicBrainz tag aggregation for genre classification** — regressed -3pp. Artist-level tags aggregate across all artist styles and confuse single-track classification even when filtered to canonical taxonomy labels.
- **Two-step family→subgenre prompting (T2)** — regressed -16pp. Splitting the classification into a family call then a subgenre call removes the model's ability to use artist→subgenre associations. Step-1 family accuracy was only 71.8%; hard locks downstream.
- **Majority vote (3× nano)** — no measurable gain over single-call at 3× cost. Nano's non-determinism distributes randomly; the correct answer wins the vote at roughly the same rate it wins a single call. Also causes timeout cascade on the sequential-calls-per-thread architecture.
- **Model upgrade (gpt-5-mini)** — -2pp vs nano at ~5× token cost. Signal quality is the bottleneck, not model capability.
- **AcoustID fingerprinting as unconditional signal** — mixed: helps orphan filenames (no parseable artist/title) but hurts clean filenames by overriding correct parser output. Use only when `filename_is_orphan()` fires.

---

## Suggested protocol before future architectural changes

Use this checklist before implementing major changes:

- Is this proposal conflicting with one of the cross-cutting decisions above?
- Which past commits already attempted something similar?
- What failed then: data integrity, UX speed, API quality, or evaluation rigor?
- What measurable guardrail (test/benchmark/AB) will prevent regression this time?

If unclear, start from:

- `docs/COMMIT_CHRONOLOGY.md` (full history)
- `docs/ARCHITECTURE.md` (current target architecture)
- tests around touched area in `tests/`
