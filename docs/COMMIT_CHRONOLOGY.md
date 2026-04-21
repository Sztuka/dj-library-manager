# Full Commit Chronology

Total commits: 306

Generated from `git log --reverse --date=short --pretty=format:"%ad | %h | %s"`.

## Commits (oldest → newest)

1. 2025-10-28 | 7e37535 | chore: initial import (project skeleton, tasks, web UI, CLI)
2. 2025-10-29 | 3be0e43 | feat(web): 3-step setup wizard + STEP tasks; normalize bucket labels; templates/CSS
3. 2025-10-29 | 8728dd0 | test changes + readme
4. 2025-10-29 | d311709 | feat(web): add FastAPI app and wizard routes; fix taxonomy builder; tidy .gitignore
5. 2025-10-29 | f672a4b | chore: ignore coverage artifacts and untrack .coverage
6. 2025-10-29 | 4881ad6 | chore(wizard): open /wizard by default instead of /taxonomy
7. 2025-10-29 | 2648e31 | feat(web): add Dashboard with actions and align config with CLI
8. 2025-10-30 | ac47022 | feat(taxonomy): allow user-defined bucket casing and style
9. 2025-10-30 | 7ea5196 | Web UI: taxonomy suggestions + preferences
10. 2025-10-31 | c220ee0 | Enrichment: add external genre sources + mapping and integrate into enrich flow
11. 2025-10-31 | 59ccfce | Remove Discogs integration and references; keep Last.fm/Spotify only. Clean up UI and docs.
12. 2025-10-31 | 5a6e71d | Fix genre display and enrichment
13. 2025-10-31 | 3615eff | Remove unused spotify_fallback.py file
14. 2025-10-31 | 84dfebd | Clean up unused files and fix genre resolution
15. 2025-11-01 | 8fd98f5 | refactor: remove UI components and clean up for backend-only development
16. 2025-11-01 | 0929f6e | feat: complete API configuration system
17. 2025-11-01 | 4c213d6 | feat: improve genre enrichment and file naming
18. 2025-11-03 | 2eec35d | docs: finalize Essentia roadmap and architecture; enrich/genres/key fixes; taxonomy detection updates
19. 2025-11-03 | c8edb07 | audio: scaffold Essentia backend, cache, and features; extend preview with detected metrics
20. 2025-11-03 | 1f2a71d | cli: add analyze-audio command with --check-env, --recompute, --target-bpm, and path selection; writes progress to LOGS/audio_status.json
21. 2025-11-03 | 011b3bc | tools: add scripts/install_essentia.py (macOS Homebrew installer)
22. 2025-11-03 | 15a98bd | docs+tasks: add Essentia install paths and audio analysis workflow
23. 2025-11-03 | 121eb51 | audio: add Essentia CLI fallback + richer env check; docs/tasks updated
24. 2025-11-04 | 130c31b | audio(cli): repo-local CLI extractor support + installer helper
25. 2025-11-04 | 9ac7ebc | audio: parse energy metrics from Essentia CLI JSON
26. 2025-11-06 | 13c199a | Add local audio analysis with tag writing
27. 2025-11-12 | ba78221 | docs: update README, docs/README, ARCHITECTURE (v2.1), ROADMAP; add docs index, CLI cheat-sheet; document SoundCloud health flow and planned enrich_status logging; note multi-parentheses version parsing and multi-source genre columns
28. 2025-11-13 | 31b2a2b | feat(enrich): add structured enrich_status.json logging with SoundCloud health/decision and request counting
29. 2025-11-13 | 7877ade | feat(soundcloud): multi-query SC genre extraction + noise filtering; auto-fill artist/title
30. 2025-11-13 | 4e53c62 | feat(scan): populate artist/title/version_info from audio tags at scan time
31. 2025-11-13 | eada3ed | feat(genres): downweight folk/indie/alternative tags across sources to reduce over-dominance
32. 2025-11-13 | a2ea749 | tune(genres): blacklist 'folk indie' as noise; keep 'indie'/'indie rock' intact; only downweight 'indie folk'
33. 2025-11-13 | e17a6e5 | feat(import/export): add BPM+key to XLSX export, round BPM; implement acceptance mapping (artist/title/version/genre) and review_status update in import_xlsx
34. 2025-11-13 | 3216ef0 | feat(apply): write accepted metadata tags (artist/title/version/genre/BPM/key) after moving files; report tag write stats
35. 2025-11-13 | ea03596 | fix(soundcloud): filter out artist/title words and noisy single-word tags; keep multi-word genre phrases; preserve filename-derived version info when online lookups return empty
36. 2025-11-13 | 9a77873 | feat(filename+apply): render multi-version tokens as separate parentheses in final filename and title tags
37. 2025-11-13 | e2f89f7 | fix(filename+enrich): strip leading track numbers in filename parsing; prefer audio tags over filename for artist/title/version in suggest_metadata
38. 2025-11-13 | 7a98d4b | fix(enrich): heuristic split of combined 'Artist - Title' when artist tag missing/invalid; extract version from trailing parentheses
39. 2025-11-13 | 9b47a55 | feat(enrich): validate AcoustID match against filename tokens; fallback to MB search if mismatch; override wrong gospel/christian genres for classic rock (Led Zeppelin)
40. 2025-11-21 | 1c98e4d | feat: remove spotify paths and harden round-1
41. 2025-11-22 | 44eeb85 | refactor: clean ml pipeline and unsorted typing
42. 2025-11-22 | e7f2ed0 | docs: refresh unsorted workflow guidance
43. 2025-11-23 | a18f214 | feat(enrich): add special artist normalization (AC/DC, ABBA, INXS, etc.)
44. 2025-11-23 | d5d783a | feat: comprehensive unsorted.xlsx workflow improvements
45. 2025-11-24 | 5c57313 | docs: Comprehensive English documentation overhaul with Rekordbox integration
46. 2025-11-24 | 025afa3 | feat: Add automatic spam tag cleaning to workflow 4 (apply)
47. 2025-11-24 | 693018e | feat: Add album artwork fetching + Beatport integration with auto-refresh
48. 2025-11-24 | b544cd7 | feat: Integrate Beatport into genre resolution and cover art workflows
49. 2025-11-24 | 4a7f13e | feat: Add SoundCloud auto-refresh with Playwright
50. 2025-11-24 | 258f3f5 | docs: Update documentation with Beatport and SoundCloud auto-refresh features
51. 2025-11-24 | acbded4 | fix: Unicode normalization (NFC/NFD) for Rekordbox path matching on macOS
52. 2025-11-24 | c0a2b44 | fix: Replace deprecated datetime.utcnow() with timezone-aware datetime.now(timezone.utc)
53. 2025-11-24 | eaa752c | feat: Add interactive prompt for Beatport credentials
54. 2025-11-24 | a7e9500 | feat: Add setup-beatport CLI command for credential management
55. 2025-11-24 | 3d9a852 | feat: Integrate Beatport setup into configure workflow
56. 2025-11-24 | 6b697b8 | feat: Add VS Code tasks for configure and Beatport setup
57. 2025-11-24 | 453cbe0 | feat: Smart config detection - prevent accidental structure overwrite
58. 2025-11-24 | 358de44 | docs: Update INSTALL.md with smart config detection explanation
59. 2025-11-24 | 3562ea1 | fix: Use username instead of email for Beatport login
60. 2025-11-24 | e82837d | fix: Improve Beatport login robustness
61. 2025-11-24 | 80a8abe | fix: Handle Beatport cookie consent and login modal
62. 2025-11-24 | a61dfcb | fix: Suppress audioread Python 3.13 deprecation warnings
63. 2025-11-25 | c25cb47 | fix: Don't add '(Original Mix)' when version_info is empty
64. 2025-11-25 | e3d9d56 | fix: Disable online metadata lookup in scan (speed optimization)
65. 2025-11-25 | f125ad6 | feat: Comprehensive Rekordbox & Beatport integration improvements
66. 2025-11-29 | e398d2d | Complete Phase 3: Full DJ software integration with auto-sync
67. 2025-11-30 | 4754e45 | Update documentation for canonical genre normalization, genres.yml as single source of truth, and deprecation of legacy genre lists.
68. 2025-11-30 | 2d61663 | Refactor genre normalization: use canonical resolver from genres.yml, update placement.py and tests
69. 2025-11-30 | d1a4a5c | Fix type checker warnings using best practices
70. 2025-11-30 | 227efb6 | Implement complete WORKFLOW 0 with WAV support and Apple Music filtering
71. 2025-12-02 | 6ad1796 | Remove legacy taxonomy system - switch to logistics-only model
72. 2025-12-03 | ae8e564 | docs: remove all taxonomy references from documentation
73. 2025-12-03 | ded29cf | docs: fix all markdownlint style issues
74. 2025-12-03 | 5e55c2c | fix: add interactive prompt when library.csv is missing
75. 2025-12-03 | d0abf1c | fix: handle None result in tag_file error unpacking
76. 2025-12-03 | 6c64d4f | feat: filter out sample loops and short tracks
77. 2025-12-03 | e9c6261 | docs: add DJ software configuration requirements
78. 2025-12-03 | 626ebec | fix: correct Rekordbox and Traktor field mappings for metadata import
79. 2025-12-03 | d87b898 | feat: add rating conversion and smart merge between Rekordbox and Traktor
80. 2025-12-03 | a72ee9b | feat: implement rating synchronization to Rekordbox and Traktor
81. 2025-12-03 | 8ef7be1 | feat: enhance title/version parsing to recognize dash separators
82. 2025-12-03 | 44d20de | fix: add 'edit' to SoundCloud remix keywords for better version matching
83. 2025-12-03 | 9d36a53 | fix: improve SoundCloud query strategy for complex remix names
84. 2025-12-03 | 62faf00 | fix: adjust genre resolver weights for remixes to prioritize remix-specific sources
85. 2025-12-03 | e406032 | fix: respect --force-genres flag for genre_suggest field
86. 2025-12-03 | fa8e0b4 | fix(genre): reduce Last.fm weight for remixes to 1.5
87. 2025-12-04 | 6106d1d | fix(enrich): pass version_suggest to avoid reparsing filename
88. 2025-12-04 | 77c715a | feat(genre): auto-map genre_suggest to canonical genres
89. 2025-12-04 | 3ec9163 | Improve year detection: use MusicBrainz release-groups for originals, SoundCloud for remixes
90. 2025-12-04 | ea95fb3 | Auto-copy year_suggest to year column during enrich-online
91. 2025-12-05 | 585cb50 | Fix: Automatic Rekordbox tag refresh after sync
92. 2025-12-05 | 6e22879 | Improve cover art fetching for remixes
93. 2025-12-05 | 2810f1d | Add cover art thumbnails and URLs to Excel with MusicBrainz earliest album support
94. 2025-12-05 | ca21e72 | Enable --fetch-covers by default in WORKFLOW 2 Enrich online task
95. 2025-12-06 | 0bcc66f | Fix cover art URL for existing covers and add SoundCloud URL support
96. 2025-12-06 | a48853f | Add cover_art_action column for flexible cover replacement control
97. 2025-12-09 | 1abf94d | Fix cover art metadata: MusicBrainz canonical + release-group covers
98. 2025-12-09 | 3b5123f | Filter out compilation albums (Greatest Hits, Best Of, etc.)
99. 2025-12-09 | 68a4447 | Add compilation filter to AcoustID lookup path
100. 2025-12-19 | a3f10e9 | feat(enrich): Add Archive.org integration for live concert recordings
101. 2025-12-19 | c435132 | chore: Add LOGS/ and cache to .gitignore, remove from tracking
102. 2025-12-19 | a3b15c2 | chore: Add MusicBrainz Live Data Feed token to config
103. 2025-12-19 | 4796794 | feat(genre): Prioritize 'rockabilly' in genre mapping for better Rock 'n' Roll detection
104. 2025-12-20 | a4107c3 | feat(enrich): Auto-copy version_suggest to version_info for live recordings
105. 2025-12-20 | 9b87609 | fix(genre): Restore rockabilly synonyms for Rock 'n' Roll
106. 2025-12-20 | 44824ba | fix(beatport): Cooldown only after FAILED refresh, not on token expiry
107. 2025-12-20 | 862aadc | docs: Add possible_upgrades.md with enrich-online optimization analysis
108. 2025-12-27 | 343f86f | feat(coverart): add FLAC, M4A and AIFF cover art support
109. 2025-12-27 | f98aa1f | feat(scripts): add fix_covers.py utility for library cover art repair
110. 2025-12-28 | 7afb231 | fix: save album tag and prevent DJ software sync duplicates
111. 2026-01-14 | 683c955 | feat(genre-resolver): enhance genre weighting with specificity boost for subgenres
112. 2026-01-14 | 7dbaacb | feat: implement cover art embedding from local files and update genre resolution logging
113. 2026-01-14 | 96812d1 | fix: correct BPM value storage for Rekordbox by scaling BPM to integer format
114. 2026-01-15 | 00af703 | Refactor and clean up documentation and codebase by removing legacy taxonomy system and related files. Deleted ROADMAP_essentia_plan.md.backup, auto_bucket_module_plan.md, auto_bucket_todo_list.md, and taxonomy_retirement_todo.md. Updated possible_upgrades.md to focus on quality and speed improvements for metadata enrichment. Removed unnecessary code and streamlined processes for genre classification and metadata fetching. Enhanced caching strategies and improved API call efficiency.
115. 2026-01-15 | 215064d | fix: add STEP 1.5 to recover lost files by filename match
116. 2026-01-15 | be824ba | fix: block apply/export for files without rekordbox_id
117. 2026-01-15 | bebb5ee | fix: upgrade traktor-nml-utils to 3.3.0 for Traktor 3.11+ support
118. 2026-01-15 | 7459d39 | fix: correct import name get_config -> load_config in STEP 1.5
119. 2026-01-15 | 5f0be1f | fix: traktor_id fallback to track_id + move library.csv to data/
120. 2026-01-15 | e14c3b1 | feat: add AIFF support + detailed error logging in Workflow 0
121. 2026-01-15 | 85df372 | feat: STEP 1.5 updates library.csv paths for recovered files
122. 2026-01-15 | 742a7ef | fix: merge by track_id instead of path - prevents duplicates when files move
123. 2026-01-16 | 486b1b6 | fix: don't set audio_id for new Traktor entries
124. 2026-01-16 | 4024fab | test: add Traktor entry format test script
125. 2026-01-16 | 3f512b1 | feat(traktor): add repair, cleanup, dedup commands for collection maintenance
126. 2026-01-16 | 9df4ef8 | feat(dedup): add library-dedup command and duplicate detection in apply
127. 2026-01-16 | cb30bce | fix(scan): prevent duplicates in unsorted.xlsx
128. 2026-01-17 | 34f66be | feat(coverart): add Traktor cache support, simplify to local cover only
129. 2026-01-17 | abded29 | fix(enrich): Radio Edit is NOT a remix + strip track number prefix from artist
130. 2026-01-17 | ade1980 | fix: apply command - separate handling for reject/archive destinations
131. 2026-02-05 | 2687ff5 | fix(genre): Add Indie Pop as separate genre, improve library.csv dedup
132. 2026-02-05 | 28c2d03 | docs: add ENRICH_OPTIMIZATION_ROADMAP with performance analysis
133. 2026-02-05 | 2b7445f | chore(tasks): add task to clean empty folders in UNSORTED
134. 2026-02-05 | 0e002e6 | fix(genre_resolver): expand specific genre boosts and improve remix detection
135. 2026-02-05 | 9a18324 | feat(genres): add EURODANCE as separate top-level genre
136. 2026-02-05 | 08777e7 | fix: improve genre resolution and title parsing
137. 2026-02-06 | 6753981 | fix(genres): merge SWING into ROCK_N_ROLL, add mezcla as version keyword, restore clean folders task
138. 2026-02-06 | 7bfd07a | Merge perf/enrich-phase1-caching: EURODANCE genre, genre boosts, clean folders task
139. 2026-02-06 | b666fa0 | docs(roadmap): add Progress Tracking section, update status after branch cleanup
140. 2026-02-06 | dbea2b8 | perf(mb_client): add LRU cache for API calls
141. 2026-02-06 | 481efe7 | perf(genre_resolver): accept pre-fetched MB data, add release-group cache
142. 2026-02-06 | 6c77907 | perf(mb_client): complete LRU cache coverage for all API calls
143. 2026-02-06 | 8f3b7e6 | feat(benchmark): add detailed in-process benchmarking with cache stats
144. 2026-02-06 | 9d3e613 | perf(enrich): skip MB in genre_resolver for remixes
145. 2026-02-06 | 391ef5a | docs(roadmap): mark Phase 1 complete with benchmark results
146. 2026-02-06 | d59fb10 | fix(mb_client): return deepcopy from cached wrappers to prevent mutation
147. 2026-02-06 | 209c96f | Merge perf/enrich-phase1: Phase 1 optimizations (35% faster)
148. 2026-02-06 | 11cc17a | refactor(genre_resolver): add fetch helpers and early exit for EDM
149. 2026-02-06 | 8928557 | docs(roadmap): add Phase 2 post-mortem on threading issues
150. 2026-02-06 | ce62e30 | refactor(genre_resolver): CTO code quality improvements
151. 2026-02-06 | 8e52935 | fix(beatport): improve remix matching for version lookups
152. 2026-02-06 | 7f54eac | fix(genres): map Beatport 'Nu Disco / Disco' to 'nu disco'
153. 2026-02-06 | 8d4e8db | test: add comprehensive enrich regression tests
154. 2026-02-07 | 5360a53 | refactor(tests): DRY enrich regression tests with parametrize
155. 2026-02-07 | dfa73d1 | Merge perf/enrich-phase2: Beatport fixes, regression tests, helper refactoring
156. 2026-02-07 | b95a88a | chore: remove debug script
157. 2026-02-07 | 568b818 | perf(enrich): early-exit optimization skips secondary APIs
158. 2026-02-07 | 188989a | refactor(genre_resolver): CTO code quality review
159. 2026-02-07 | 984bdea | perf(enrich): skip SoundCloud for non-remixes, limit queries
160. 2026-02-07 | 97df5fc | refactor(soundcloud): remove dead code after query optimization
161. 2026-02-07 | 02c50be | perf(apply): lazy-load library index + single-pass extension scan
162. 2026-02-07 | db0dacb | refactor(cli): optimize scan + sync workflows
163. 2026-02-07 | 9c392fd | fix(enrich): filter garbage tags from genre resolution
164. 2026-02-07 | 2f66c2a | fix(beatport): validate token before returning from memory cache
165. 2026-02-07 | 25131c1 | fix(beatport): reject results when remix not found but original is
166. 2026-02-07 | 9a69d69 | fix(soundcloud): improve query strategy and filter DJ mixes
167. 2026-02-07 | 229e150 | refactor(soundcloud): extract constants, fix docstrings, add duration filter to all functions
168. 2026-02-07 | 2bd017c | fix(enrich): handle nested parens in version parsing & noise-only genres
169. 2026-02-07 | e56b559 | fix(soundcloud): use shorter queries for originals to avoid 403
170. 2026-02-07 | e276ff4 | fix(filename): detect reversed title-artist order in filenames
171. 2026-02-07 | 081aeef | fix(genre_resolver): skip MB/LFM for remixes without Beatport match
172. 2026-02-07 | 3b48c63 | fix(filename): handle dirty/clean/feat in version extraction
173. 2026-02-07 | b78eeaf | fix(soundcloud): shorter/cleaner queries for remixes/mashups
174. 2026-02-07 | 8b31de1 | chore: remove debug script
175. 2026-02-07 | 3b13e2b | fix(genre_resolver): add 'mashup' to remix keywords
176. 2026-02-07 | 1828345 | fix(soundcloud): limit remixer name to 2 words
177. 2026-02-07 | d9b0236 | refactor(soundcloud): use full remixer names, rely on retry for 403
178. 2026-02-07 | 6bcebd8 | fix(soundcloud): remove genre names from search queries
179. 2026-02-07 | b9f1152 | fix(filename): extract version from parentheses after artist/title swap
180. 2026-02-07 | 986cf4e | fix(soundcloud): use first remixer for better SC search precision
181. 2026-02-08 | fa8df1d | fix(beatport): capture fresh token after login, not stale cached one
182. 2026-02-08 | 3aea9ac | fix(beatport): only capture fresh tokens (>5min remaining)
183. 2026-02-08 | 8b4b3d5 | perf(enrich): skip rate-limit sleep on cached responses, add in-process cache, dedup Beatport calls\n\n3 optimizations for enrich-online speed:\n\n1. Smart rate limiting (Beatport + SoundCloud): check response.from_cache\n after the request — if served from requests_cache disk cache, reset the\n rate-limit timer so the next request fires immediately instead of\n sleeping 0.8-1.0s unnecessarily.\n\n2. In-process LRU cache on beatport.search_track(): dictionary keyed by\n normalized (artist|title|duration|version). Prevents duplicate API calls\n within the same enrich-online run — genre_resolver and enrich.py both\n call search_track for the same track.\n\n3. Deduplicate Beatport call in enrich.py for remixes: changed from\n search_track(artist, full_title) to search_track(artist, title, version=version)\n so it matches the genre_resolver call signature and hits the in-process cache.\n\nExpected impact: re-runs ~85% faster (15-40min → 2-4min), first runs ~25% faster."
184. 2026-02-09 | dc707b5 | refactor: overhaul genres.yml taxonomy + derive resolver data from YAML\n\nP0: Fix cross-genre conflict (happy hardcore in HARDCORE & EURODANCE)\nP1: Fix 14 mis-classified synonyms (hardstyle, swing, synth pop, etc.)\nP2: Remove 97 case-only dupes, add 13 new genres (50 total)\n Added category + boost metadata to all genres\nP3: genre_resolver.py derives BEATPORT_ELECTRONIC_GENRES (190) and\n \_SPECIFIC_GENRE_BOOST (259) from genres.yml at import time\n\nOther changes:\n- genre_canonical.py: resolve() handles comma-separated inputs,\n uses longest-match + boost tiebreaking for specificity\n- genre_mapper.py: boost-aware matching, removed rockabilly hack\n- placement.py: expanded CLUB_GENRE_KEYS and VIBE_MAP with new genres"
185. 2026-02-09 | ea96822 | fix: unify genre normalization, fix electronic detection & LFM scoring (P0)\n\n1. Extract shared normalize_genre() into djlib/genre_utils.py\n - Single source of truth: lowercase, all non-alnum→spaces, collapse whitespace\n - Replaces divergent \_norm() (kept &/.) and \_normalize() (stripped all)\n - Both genre_canonical.py and genre_resolver.py now import from here\n\n2. Fix \_is_beatport_electronic() false-positive risk\n - Was: substring containment (\"warehouse\" matched \"house\")\n - Now: word-boundary regex matching via \\b\n\n3. Unify Last.fm scoring through \_score_tag()\n - Was: 12 lines of inline code duplicating canonical/noise/boost logic\n - Now: \_score_tag() accepts optional count= param for log-weighted scoring\n - All 4 sources (BP, LFM, MB, SC) use the same scoring path\n\n4. Fix ALIASES keys for new normalization\n - \"d&b\" → \"d b\" (after normalize), \"tech-house\" → \"tech house\"\n - Removed \"nu disco / disco\" alias (already a genres.yml synonym)\n - Added \"d&b\" and \"d n b\" as genres.yml synonyms for DNB"
186. 2026-02-09 | a32fb7d | refactor(genre*resolver): P1-P3 code review — decompose, lazy-load, SourceScore, tests\n\nP1 Architecture:\n- Decompose 170-line resolve() into focused helpers: \_detect_remix(),\n \_score_beatport(), \_score_musicbrainz(), \_score_lastfm(), \_score_soundcloud(),\n \_rank(). resolve() now ~70 lines of orchestration.\n- Replace 3 disable*\* boolean params with sources: Set[str] parameter\n- Add 54-test suite: pure function unit tests + mocked integration tests\n\nP2 Maintainability:\n- Lazy-load genres.yml via @lru_cache (testable with .cache_clear())\n- Remove cli.py top-level import of genre_resolver (lazy import in handlers)\n- Add TODO(#8) for cross-source confidence metric\n- Document ALIASES dict rationale\n\nP3 Nice-to-haves:\n- SourceScore dataclass replaces Tuple[str,float,Dict] in breakdown\n- SoundCloud fetched for ALL tracks (not just remixes)\n- Runtime \_NOISE_TERMS validation against genres.yml at import\n- \_NOISE_TERMS as frozenset, fixed normalized form\n\nAll callers updated: cli.py, enrich.py, scripts/debug_soundcloud.py\n54 new tests pass. Zero regressions in existing suite."} </invoke>
187. 2026-02-09 | d7ba5d4 | docs: add ML genre classification roadmap, update ARCHITECTURE.md\n\n- New docs/ML_GENRE_CLASSIFICATION_ROADMAP.md: full plan for replacing\n API-based genre resolution with Essentia audio ML pipeline\n- Updated ARCHITECTURE.md: genre_resolver section reflects P1-P3 refactor\n (decomposed functions, SourceScore, sources= param, lazy loading)\n- Updated ARCHITECTURE.md: added ML pipeline section with module inventory"
188. 2026-02-10 | 3c56984 | refactor: P0-P3 enrich.py overhaul — decompose, DRY, flatten, test\n\nP0 (dead code / dupes):\n- Remove 30 lines unreachable code after return in enrich_online_for_row\n- Remove duplicate \_join_artist_credit → use mb_client.\_join_artist_credit\n- Remove hardcoded genre heuristic (song title → genre mapping)\n- Remove redundant import re inside \_clean_title\n\nP1 (decomposition / DRY):\n- Decompose 309-line suggest_metadata into 4 phases + 3 helpers:\n \_acoustid_artist_matches, \_resolve_via_genre_sources, \_offline_fallback\n- Extract shared helpers: \_is_live (was 5 inline copies),\n \_is_compilation_album (was 3 copies), \_enrich_archive_org (was 3 copies),\n \_resolve_first_release (was 2 copies), \_parse_duration_tag\n- Wire helpers into lookup_musicbrainz and lookup_acoustid\n- lookup_acoustid: use \_resolve_first_release, \_enrich_archive_org,\n duration_sec param directly (no re-parsing m:ss→seconds)\n\nP2 (quality):\n- Fix shadowed `sources` variable → genre_sources\n- Replace import sys; print(stderr) → logger.warning()\n- Extract magic constants: MIN_GENRE_CONFIDENCE, ARCHIVE_TOLERANCE_S,\n MB_HTTP_TIMEOUT, \_MIN_WORD_LEN_SIMILARITY, \_COMPILATION_KEYWORDS,\n \_LIVE_KEYWORDS\n\nP3 (structure):\n- Flatten 8 nested functions from derive_local_metadata to module level\n (\_sanitize_artist, \_sanitize_title, \_sanitize_version, \_canonical,\n \_clean_stem, \_normalize_ascii, \_strip_artist_prefix, \_strip_version_suffix)\n- Simplify enrich_online_for_row: use read_tags() instead of raw mutagen\n\nBug fix:\n- \_is_live: use word-boundary matching (\\b) instead of substring `in`,\n fixes false positive on \"alive\" matching \"live\"\n\nTests: 64 new tests in test_enrich_helpers.py covering all extracted helpers,\nconstants, flattened sanitizers, and offline suggest_metadata path.\n\n1453 → 1350 lines (-103), 130 tests passing (64 new + 66 existing)"} </invoke>
189. 2026-02-10 | 5365d4e | fix(SC+resolver): full-query strategy, vs-splitting, always fetch LFM/MB\n\nFixes wrong genre for remix chains like \"Bastille - Pompeii (Merchant vs\nVidojean & Oliver Loenn City Boys Edit)\" which was getting synthpop/trance\ninstead of afro house.\n\nsoundcloud.py — \_candidate_queries:\n- Strategy 1: artist + title + full version (highest precision first)\n- Split first_remixer on 'vs'/'vs.' in addition to '&'/'and'\n → \"Merchant vs Vidojean\" now extracts \"Merchant\" → \"Merchant Pompeii\"\n finds the actual track on SC (genre: Afro House, 14 tags)\n- max_queries 4→5 to accommodate new strategy\n\ngenre_resolver.py — resolve():\n- Remove skip_mb_lfm_for_remix gate: always query Last.fm and MusicBrainz\n even when SC returns remix tags (SC can return tags from _wrong_ remixes;\n LFM/MB provide safety net; scoring weights already deprioritise them\n for remixes via WEIGHT_LASTFM_REMIX=0.5, WEIGHT_MB_REMIX=1.5)\n\nVerified: Pompeii now resolves to main=afro house, subs=[afro house edit,\nafro house merchant edit]. 65/65 genre resolver tests pass."
190. 2026-02-10 | ae05ab8 | refactor(SC queries): review fixes — \_light_clean, compiled regex, tests\n\nAddresses code review findings from commit 5365d4e:\n\nP0 — Strategy 1 now uses \_light_clean() (preserves & and vs) instead of\n \_clean_for_query() (which stripped &). Consistent cleaning across\n all three parts of the full-version query.\n\nP1 — Delete dead \_extract_primary_remixer() (duplicated inline logic,\n only referenced in docs).\n — Compile keyword/genre removal into \_RE_VERSION_KEYWORDS and\n \_RE_VERSION_GENRES (single-pass alternation vs O(k) loop).\n — Add NOTE comment on vs\\.? regex operating on raw remixer string.\n — Add 20 unit tests for \_candidate_queries covering: Strategy 1\n fidelity, vs/&/and splitting, word cap, genre stripping, mashups,\n dedup, originals, edge cases.\n\nP2 — Cap Strategy 1 at \_MAX_QUERY_WORDS=10 to prevent noisy long queries.\n\nP3 — TODO comment on deriving genre list from genres.yml.\n — Fix stale get_soundcloud_genres docstring (said \"2-query\").\n\n85/85 tests pass (65 genre + 20 new SC query tests)."
191. 2026-02-10 | 400e306 | fix(soundcloud): handle X separator + filter multi-word artist/title fragments\n\nTwo bugs caused 'Alesso X Depeche Mode - Enjoy The Silence X If I Lose\nMyself (Vidojean X Oliver Loenn Mashup)' to resolve as UNMAPPED:\n\n1. 'X' (common mashup credit separator) not recognised in first_remixer\n split regex — 'Vidojean X Oliver Loenn' stayed together, skipping\n Strategy 2 (solo remixer query). Added [xX] to the split pattern.\n\n2. \_keep_token() only checked exact membership (t in at_words) for\n filtering artist/title noise. Multi-word SC tags like 'depeche mode'\n or 'enjoy the silence' slipped through because the full phrase wasn't\n in the single-word set. Added subset check: if ALL words of a tag\n come from artist/title words, reject it.\n\nResult: resolver now returns 'afro house' for this track.\n\nTests: +5 new (X split upper/lower, \_keep_token artist/genre filtering)\n 25 SC query tests, 90 total genre tests — all pass."
192. 2026-02-14 | fc96ebe | fix: remove override system, fix SC remixer validation, fix HTTP cache (no error caching)
193. 2026-02-15 | f52aae4 | fix(soundcloud): don't cache None results from transient SC failures
194. 2026-02-15 | 9afb70c | fix(enrich): remove website watermarks from title and version fields
195. 2026-02-15 | 48c34bf | fix(beatport): skip search when artist is missing
196. 2026-02-15 | 12537e4 | Merge fix/sc-lru-cache-none: don't cache None SC results
197. 2026-02-15 | 77fa5b3 | Merge fix/tag-cleaner-watermarks: remove website watermarks from title/version
198. 2026-02-15 | 55d8c72 | Merge fix/beatport-empty-artist: skip Beatport when artist is missing
199. 2026-02-15 | 9659992 | fix(tests): add title/artist to mock SC responses for remixer validation
200. 2026-02-15 | ae83feb | fix(enrich+sc): strip trailing commas from titles + clean SC remixer extraction
201. 2026-02-15 | ad21fa2 | fix(sc): strip orphaned numbers from remixer after keyword removal
202. 2026-02-16 | c2c064f | fix(sc): split on commas when extracting first remixer name
203. 2026-02-16 | 8b5b68d | fix(filename+xlsx): static final_filename + strip trailing comma from stem\n\n1. unsorted.py: Replace Excel formula for final_filename with static\n computed value. The formula used & as concatenation operator which\n clashed with literal & in cell values, causing &+AP19 rendering\n artifacts in Apple Numbers/Excel.\n\n2. filename.py: Strip trailing commas/semicolons from cleaned filename\n stem before parsing. Fixes version extraction failure for files like\n \"Dancin (Faul & Wad, Samaha, Loxivice Remix),.wav\" where the\n trailing comma prevented the regex from matching the closing paren,\n causing version to appear in both title and version_info columns.
204. 2026-02-16 | 16ade3c | strip junk 'Version N' tokens from version_info in \_sanitize_version\n\nDownload sites often embed 'Version 4', 'Ver. 2', 'V3' etc. in ID3 tags.\nThese are meaningless numbering artifacts, not remix/edit descriptors.\n\nThe regex strips Version/Ver/V + digit patterns while preserving\nlegitimate uses like 'Acoustic Version' (no trailing number).\nAlso cleans leftover commas/semicolons after stripping."]
205. 2026-02-18 | fb77c1d | fix: normalize paths to NFC for Rekordbox ID lookup\n\nmacOS filesystem & openpyxl store paths in NFD (decomposed Unicode),\nbut Rekordbox master.db uses NFC (precomposed). Characters like é/á\n(Ninguém, Zárate) have different byte representations, causing\ndict lookup to fail silently.\n\nFix: unicodedata.normalize('NFC', ...) on both mapping keys\n(external_sync.py) and lookup paths (cli.py cmd_apply + cmd_scan).">
206. 2026-02-18 | 88d9f61 | merge: filename encoding, version strip, NFC path normalization fixes
207. 2026-02-18 | 0d283a7 | fix(apply): block silent (2) rename duplicates, add intra-batch dedup, verbose skip logging\n\n- NEVER silently create (2) copies in library/archive/mixes destinations\n- When dest exists: hash-check → skip identical / prompt user for conflict\n- Add intra-batch duplicate detection via match_key tracking\n- Update library_index after each successful move (prevents same-batch dupes)\n- Extend dedup check to archive destination (was library-only)\n- Add xlsx pre-flight validation for duplicate entries\n- Structured skip reasons: FILE_MISSING, NO_REKORDBOX_ID, ALREADY_IN_LIBRARY,\n DUPLICATE_IN_BATCH, IDENTICAL_FILE_EXISTS, FILE_CONFLICT\n- Skip summary at end of apply output
208. 2026-02-18 | 83c699c | feat(reject-registry): persistent library-rejected.csv to block re-scanning rejected files\n\n- New REJECTED_CSV_PATH in config.py (data/library-rejected.csv)\n- New REJECTED_FIELDNAMES, load_rejected(), save_rejected() in csvdb.py\n- cmd_apply: writes rejected records (hash, fp, artist, title, date, reason)\n to library-rejected.csv when destination=reject\n- cmd_scan: checks rejected registry by file_hash AND fingerprint;\n skips with [REJECTED] log if match found\n- Prevents re-scanning of files that user already explicitly rejected
209. 2026-02-18 | 1c76f0e | feat(cleanup): add scripts/cleanup_library_dupes.py for existing (2) duplicates\n\n- Scans library for files with (2), (3), etc. in names\n- Compares hash (identical?) and quality (which is better?)\n- Categories: remove_dupe, original_is_better, dupe_is_better,\n same_quality_different_content, orphan_dupe\n- Dry-run by default, --fix to act, --keep-better to auto-swap\n- Found 2 existing dupes: Bastille + Benson Boone (same quality, different hash)
210. 2026-02-18 | ae265c9 | feat(cleanup): add DJ software removal + --force-same-quality to cleanup script\n\n- After moving dupes to reject, lookup Rekordbox/Traktor IDs\n- Traktor: auto-remove from collection.nml\n- Rekordbox: print IDs + instructions (pyrekordbox can't delete)\n- Remove orphaned entries from library.csv\n- --force-same-quality: also handle (2) dupes with identical quality\n (the most common case from Phase 1 bug)\n- Refactored \_move_to_reject() helper for consistent handling
211. 2026-02-18 | 738685a | chore: add cleanup_library_dupes to tasks.json as TOOLS (not workflow)\n\n- TOOLS — Audit library duplicates (dry-run)\n- TOOLS — Fix library duplicates (move to reject + DJ software cleanup)\n- Recovery tool for Phase 1 bug damage, not a regular workflow step
212. 2026-02-18 | 09747d3 | fix(traktor): auto-detect collection.nml in remove_tracks_from_traktor\n\nWas falling back to Path('').expanduser() → '.' when TRAKTOR_COLLECTION\nnot in config, causing [Errno 21] Is a directory. Now uses same\nauto-detection logic as get_traktor_track_ids().
213. 2026-02-18 | 554862e | add Rekordbox collection analysis script
214. 2026-02-18 | 5ea215a | Merge fix/dedup-and-skip-bugs: block silent (2) dupes, rejected registry, cleanup tools
215. 2026-02-20 | c7bbc2a | fix(genre-resolver): remix scoring — version hints, LFM/MB filtering, expanded detection
216. 2026-02-27 | 0c600e8 | feat: add interactive review UI with audio preview
217. 2026-02-27 | 09adcf2 | refactor: migrate staging from XLSX to CSV
218. 2026-02-27 | 4dfbf84 | review-ui: UX fixes — destination column, year, inline editing, clickable genre suggestions
219. 2026-02-28 | 3b4d0e0 | review-ui: 9 UX speed features — auto-play, batch actions, undo, waveform
220. 2026-02-28 | ab5baf9 | feat(review): library views - rich columns, filters, in-library badge, stats, processed tab
221. 2026-02-28 | c0802ac | feat(review): redesign Processed tab — source from LOGS/moves-\*.csv
222. 2026-02-28 | ebb5c43 | chore: add CLAUDE.md and AGENTS.md project instruction files
223. 2026-03-01 | 5d321a9 | chore(agents): add personas, QA tester, and documentation agent
224. 2026-03-01 | 08973fd | style: auto-format JS, CSS, HTML, and markdown files
225. 2026-03-01 | 2217331 | fix(review): processed tab reads from library.csv instead of move logs
226. 2026-03-01 | 40abbad | docs: add unsorted.csv quality analysis (205 tracks, 88 multi-genre, 10 unmapped)
227. 2026-03-01 | 3f2ecaa | merge: feature/review-library-views into main (12 commits)
228. 2026-03-01 | 14db266 | feat(review): context menu + filename display in player footer
229. 2026-03-01 | 5245623 | chore(docs): enforce "never commit directly to main" rule
230. 2026-03-01 | 55eafcf | merge: chore/enforce-branch-workflow into main
231. 2026-03-01 | 2376f83 | feat(review): AI genre suggestion via OpenAI in context menu
232. 2026-03-01 | 3c1a6c8 | merge: feature/ai-genre-suggest into main
233. 2026-03-01 | 099b268 | fix(review): prevent stale cache and add connection error states
234. 2026-03-01 | 1ae0b2e | merge: fix/review-stale-cache into main
235. 2026-03-01 | 8f7539b | fix(config): move all API keys from config.yml to config.local.yml
236. 2026-03-01 | 645885b | merge: fix/move-api-keys-to-local into main
237. 2026-03-01 | a595015 | feat(review): add Re-enrich and Swap artist/title to context menu
238. 2026-03-01 | 1fc1a03 | Merge feature/context-menu-enrich: Re-enrich and Swap in context menu
239. 2026-03-01 | a030be7 | fix(filename,review): paren-aware dash parsing + swap recalculates version
240. 2026-03-01 | b1af65f | Merge fix/swap-version-and-paren-dashes
241. 2026-03-01 | 3746f80 | fix(review): enrich Accept now saves source genres and updates UI
242. 2026-03-01 | fe8bc29 | Merge fix/enrich-accept-saves-sources
243. 2026-03-01 | 3d3a81b | fix(review): include version_info in context menu search queries
244. 2026-03-01 | 44bc1c8 | Merge fix/context-menu-search-version
245. 2026-03-01 | d8bd8db | fix(review): AI genre suggest now remix-aware with BPM guide
246. 2026-03-01 | f90166f | Merge fix/ai-suggest-remix-awareness
247. 2026-03-01 | 96a1115 | fix(scan,enrich): recognise w\_ as feat. + SoundCloud title-only search
248. 2026-03-01 | 6728746 | Merge fix/filename-w-feat-and-sc-no-artist
249. 2026-03-02 | 57cb63e | feat(review): AI Identify Track — LLM-powered metadata identification
250. 2026-03-02 | bfa49d5 | Merge feature/ai-identify-track: AI track identification
251. 2026-03-02 | 0e7800f | style: apply stashed CSS changes
252. 2026-03-02 | d308d1c | feat(review): add AI Chat panel for conversational metadata refinement
253. 2026-03-02 | 1872b8d | Merge feature/ai-chat: AI Chat panel for conversational metadata refinement
254. 2026-03-02 | d2514bb | fix(review): AI Chat review fixes - version mapping, safety, docs
255. 2026-03-02 | 7e8a0e7 | Merge fix/ai-chat-review-fixes: review team fixes
256. 2026-03-02 | 08321f4 | feat(review): AI Chat improvements — TTL/LRU, drag, minimize, quick prompts, diff
257. 2026-03-02 | fed8090 | Merge feature/ai-chat-improvements: TTL/LRU, drag, minimize, quick prompts, diff
258. 2026-03-02 | 8bbee30 | fix(review): chat panel solid background + reduce folder name genre bias
259. 2026-03-02 | 7eca53d | Merge fix/ai-chat-bg-and-folder-bias
260. 2026-03-02 | 17dadc7 | fix(review): correct BPM genre ranges — Deep House to 126, Afro House from 116
261. 2026-03-02 | 379dc9c | Merge fix/bpm-genre-ranges: correct BPM genre ranges for AI chat
262. 2026-03-02 | 62bb14e | fix(enrich): allow Beatport search for remixes without artist
263. 2026-03-02 | f428df2 | Merge fix/beatport-no-artist-remix: allow Beatport search for remixes without artist
264. 2026-03-02 | 2a07997 | fix(enrich): CLI safety net — extract artist from Beatport for no-artist remixes
265. 2026-03-02 | a5d987e | style: auto-format review UI files (prettier line wrapping)
266. 2026-03-02 | bfaa51d | merge: fix/enrich-artist-safety-net — CLI Beatport artist extraction safety net
267. 2026-03-02 | 20793f3 | feat(review): add URL scrape metadata feature
268. 2026-03-02 | 8e59dd2 | Merge feature/url-scrape-metadata: URL scrape metadata for mashups/edits
269. 2026-03-02 | b515a12 | feat(review): add URL scrape metadata feature
270. 2026-03-02 | 7d39183 | Merge fix/soundcloud-scrape-oembed: oEmbed + auto-refresh for SC
271. 2026-03-02 | 34b0964 | feat(ai-chat): add web search capability via OpenAI Responses API
272. 2026-03-02 | 499cefb | Merge feature/ai-chat-web-search: web search via OpenAI Responses API
273. 2026-03-02 | 3c5d3e5 | fix(ai-chat): extract reply text from nested output + strip markdown citations
274. 2026-03-02 | 10c7542 | Merge fix/ai-chat-web-search-text: fix empty AI reply bubbles
275. 2026-03-02 | ea4ad96 | fix(scrape-url): use write_unsorted_rows instead of undefined save_unsorted_rows
276. 2026-03-02 | 83e7c91 | Merge fix/scrape-url-save: fix NameError on URL scrape save
277. 2026-03-02 | ba03ac8 | feat(config): make AI models configurable via config
278. 2026-03-02 | 8840e4d | Merge feature/configurable-ai-model: configurable AI models via config
279. 2026-03-02 | 8996b00 | feat(identify): add web search to identify-track + fix chat suggestion blocks
280. 2026-03-02 | 7a4a31a | Merge feature/identify-web-search: web search for identify-track + chat suggestion blocks
281. 2026-03-02 | d914cbb | fix(soundcloud): increase duration filter from 600s to 720s (10->12 min)
282. 2026-03-02 | 65659b5 | Merge fix/sc-duration-filter: increase SC duration limit to 12min for extended remixes
283. 2026-03-02 | 00f370c | fix(enrich): save genre to dropdown-compatible value + return year
284. 2026-03-02 | 71b1273 | Merge fix/enrich-accept-genre-year: save genre + year from enrich Accept
285. 2026-03-02 | 4405677 | fix(soundcloud): prefer remix upload year over original track year
286. 2026-03-02 | 231a336 | Merge fix/enrich-year-remix-preference: SC year prefers remix match over original
287. 2026-03-02 | f092eff | fix(genre-resolver): return canonical labels from genres.yml instead of raw tags
288. 2026-03-02 | a336ab1 | Merge fix/genre-resolver-labels: canonical genre labels from genres.yml
289. 2026-03-03 | f5d4dc1 | feat(review-ui): add interactive rating column to Unsorted tab
290. 2026-03-03 | b49e9ce | Merge feature/unsorted-rating: interactive star rating in Unsorted tab
291. 2026-03-09 | 55b693f | feat(ai): unified AI classify — naming + genre in one call
292. 2026-03-09 | 0d42d6a | feat(review): add Library Review tab for re-classifying existing library tracks
293. 2026-03-09 | 6b05f1e | fix(ai): support GPT-5 reasoning models in classify API
294. 2026-03-10 | 65d69af | feat(ai): web search classify + batch improvements
295. 2026-03-10 | 83b5518 | feat(review): add Library Fix tab for side-by-side AI comparison
296. 2026-03-15 | 13590ab | feat(ab-test): implement Essentia interpreter (LLM-based feature to semantic)
297. 2026-03-15 | e1f2f17 | feat(ab-test): integrate web search + finalize 4 variant matrix
298. 2026-03-16 | de5836f | fix(ab-test): fix 7 prompt & web-search problems from multichat audit
299. 2026-03-16 | 376e510 | fix(ab-test): symmetric signal framing + prompt comparability fixes
300. 2026-03-16 | 321565d | feat(web-search): refactor to targeted site queries (v2 strategy)
301. 2026-03-16 | 76cd978 | fix(web-search): include version in Traxsource query (same as Beatport)
302. 2026-03-16 | 516356e | refactor(ab-test): use gold_labels.json as sole source of truth
303. 2026-03-17 | 5f04c1c | refactor(taxonomy): rewrite genres.yml to 48-genre taxonomy with families
304. 2026-03-17 | 0811ad0 | fix(ab-test): P0 prompt rewrite — signal hierarchy + BPM caveat + WS remix leak
305. 2026-03-17 | e102b91 | chore: add untracked project files to version control
306. 2026-03-17 | f7cc2fd | Merge feature/essentia-interpreter: AB test infrastructure + P0 prompt rewrite
