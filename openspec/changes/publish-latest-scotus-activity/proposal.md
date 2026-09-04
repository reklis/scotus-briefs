## Why

The live site can hide newly issued opinions and orders because every listing is sorted only by oral-argument date, and the nightly collector cannot publish a disposition unless the case has an argument transcript. The Court is already publishing emergency-docket slip opinions newer than the site's visible content, so discovery, public contracts, ordering, and the nightly operating path must represent and prioritize all supported current Court activity.

## What Changes

- Discover the active term's official slip-opinion index nightly, including revisions and per-curiam or emergency-docket dispositions that have no oral-argument transcript.
- Represent a public case with zero or more argument sessions and one or more dated official dispositions while retaining strict evidence, grounding, privacy, and completeness validation.
- Add an authoritative `latest_court_document_date` derived from dated official dispositions and arguments, with deterministic fallback behavior for migrated records whose disposition date is not yet known.
- Use one newest-first ordering implementation for homepage results, free-text search, term/status/topic archives, general case listings, pagination, and generated search data.
- Prioritize newly published Court activity ahead of older pending updates while retaining finite per-run Court/model/runtime limits and per-case failure isolation.
- Make nightly runs deploy successful changed cases automatically and preserve failed cases as pending without blocking unrelated valid cases.
- Backfill known disposition dates without model calls where they can be matched deterministically to reviewed official indexes, then process the newest missing non-argued cases through the normal ephemeral validation pipeline.
- Add freshness validation and operational reporting that compare the newest discovered supported Court activity with the newest published or explicitly pending activity.

## Capabilities

### New Capabilities
- `current-scotus-activity`: Discover and publish supported current-term argued and non-argued Court dispositions nightly, expose authoritative activity dates, and order every public case surface newest-first.

### Modified Capabilities

None. The prior static-site and nightly-publication capabilities remain in an unarchived change and are refined here through a focused follow-up capability.

## Impact

- Affects SCOTUS public/static contracts, source discovery, live static processing, correlation/status derivation, static ordering/export, templates, search index and JavaScript, generated-content migration, validation, tests, configuration, nightly workflow summaries, and operations documentation.
- Adds official slip-opinion index requests but no new host, runtime service, credential, external model provider, or public backend.
- Existing URLs and accepted case revisions remain stable. Contract evolution and generated-state migration must be explicit and fail closed.
