# Taxonomy Retirement To-Do

- [ ] Inventory all runtime/tests/docs that reference `djlib.legacy.taxonomy`, `taxonomy*.yml`, or `taxonomy_map.yml`; record findings.
- [ ] Specify logistics-based replacements (paths, genres, workflows) for each legacy capability.
- [ ] Implement a `djlib.cli migrate-taxonomy` helper that converts taxonomy YAML into the logistics configuration, creating backups.
- [ ] Remove production imports of `djlib.legacy.taxonomy` and adjust or drop the dependent CLI commands/tests.
- [ ] Update docs/release notes to announce the removal and describe migration steps/timeline.
- [ ] Delete `taxonomy.yml`, `taxonomy.local*.yml`, and `taxonomy_map.yml` once migration completes and tests pass.
