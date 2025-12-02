# Taxonomy Retirement - COMPLETED ✅

**Completed December 2025**

All tasks completed:
- ✅ Deleted legacy modules: `djlib/legacy/taxonomy.py`, `djlib/legacy/genre.py`, `djlib/legacy/buckets.py`
- ✅ Deleted YAML files: `taxonomy.yml`, `taxonomy.local*.yml`, `taxonomy_map.yml`, `taxonomy_suggestions.yml`
- ✅ Removed all production imports and references to taxonomy system
- ✅ Simplified `djlib/mover.py` to logistics-only (no fallback code)
- ✅ Updated tests: removed `tests/test_taxonomy.py`, updated integration tests
- ✅ Updated documentation: ARCHITECTURE_EN.md, ROADMAP_EN.md marked legacy features
- ✅ Git commit 44f2724: "Complete taxonomy system removal"

**Migration Notes:**
- No user migration needed (confirmed no READY TO PLAY folders existed)
- CSV columns `target_subfolder`, `bucket_suggest`, `target_bucket` marked as REMOVED in docs
- Current system: Simple logistics (library/reject/archive/mixes) with genre classification
- Future: Smart playlists/bucketing will be built as separate feature on top of clean structure
