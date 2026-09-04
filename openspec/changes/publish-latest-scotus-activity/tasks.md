## 1. Official Activity Discovery

- [ ] 1.1 Add strict fixtures and typed parsing for active-term slip-opinion rows, emergency `A` dockets, consolidated dockets, original publication dates, revision dates, captions, kinds, and official PDF URLs.
- [ ] 1.2 Add first-class conditional slip-index polling that creates case-level candidates independently of oral-argument discovery and never interprets a missing row as deletion.
- [ ] 1.3 Merge argument and disposition discovery by normalized docket while retaining zero-session cases and eliminating redundant opinion-index fetches.
- [ ] 1.4 Persist allowlisted disposition identities, dates, validators, digests, and pending metadata without source bodies or private fields.
- [ ] 1.5 Add discovery tests for new/revised/unchanged emergency dispositions, malformed rows, consolidated dockets, index failures, and bounded checkpoints.

## 2. Versioned Public and State Contracts

- [ ] 2.1 Add a strict structured public disposition contract with official kind, URL, publication date, and optional revision date.
- [ ] 2.2 Evolve public cases to support zero or more real arguments, dated dispositions, and a validated derived latest Court document date without fabricated argument metadata.
- [ ] 2.3 Evolve generated state and search contracts to retain official activity dates, optional argument dates, and deterministic serialization/order.
- [ ] 2.4 Implement explicit compatibility readers and migration for prior generated-content while preserving immutable V1 revision bytes, stable case keys, paths, and release-parent safety.
- [ ] 2.5 Add exact URL/docket slip-index date backfill without model calls and retain argument-date fallback for unmatched legacy dispositions.
- [ ] 2.6 Add contract/migration tests for argued, disposition-only, revised, migrated-undated, malformed, and digest-conflict cases.

## 3. Disposition-Only Processing

- [ ] 3.1 Refactor case work so opinion/order documents can belong directly to a case rather than only through an argument session.
- [ ] 3.2 Add bounded extraction for disposition-only evidence and retain transcript completeness requirements only for argument sessions that exist.
- [ ] 3.3 Add a strict disposition-only brief schema/prompt with zero argument analyses and validators rejecting invented oral argument, unsupported parties, outcomes, or filler.
- [ ] 3.4 Derive decided/order-issued status deterministically from typed Court action evidence and reject inconsistent observation-type/legal-status combinations.
- [ ] 3.5 Generate complete public disposition-only cases and pages with conditional argument sections and official dated disposition provenance.
- [ ] 3.6 Add focused processing tests for valid emergency opinions, missing docket evidence, invented argument text, status consistency, revisions, per-case failures, and cleanup.

## 4. Unified Newest-First Presentation

- [ ] 4.1 Change the shared `latest_court_document_date()` and `sort_cases()` policy to use maximum official activity date with deterministic tie-breakers and zero-session support.
- [ ] 4.2 Add latest activity date to static search data, make argument date optional, and update dependency-free search rendering/filtering while preserving generated order.
- [ ] 4.3 Update homepage, SCOTUS index, case cards/pages, and term/status/topic/corrections/general listings to display and order by latest official Court activity.
- [ ] 4.4 Keep argument-date archives session-specific while ordering their matching cases by latest overall activity.
- [ ] 4.5 Strengthen static validation to compare exact search and paginated listing order and reject inconsistent activity dates or zero-session markup.
- [ ] 4.6 Add deterministic tests proving every public/search/listing surface is newest-first for mixed argued, later-decided, revised, tied, and disposition-only cases.

## 5. Nightly Prioritization and Freshness

- [ ] 5.1 Rank work by fresh-change class, authoritative activity date descending, persisted pending retry, migration/current/historical class, and stable case-key tie-break before applying limits.
- [ ] 5.2 Explicitly reconsider persisted pending work and preserve every discovered but unselected supported activity in sanitized pending state.
- [ ] 5.3 Continue after case-local failures while shared budget remains and deploy all unrelated complete validated successes automatically on scheduled runs.
- [ ] 5.4 Add sanitized freshness accounting for newest discovered, published, deferred, and failed activity dates/counts and validate that no supported discovery is silently lost.
- [ ] 5.5 Add queue and workflow tests for newest-first caps, failed-newest continuation, backlog non-starvation, no-op checkpointing, scheduled deployment, and safe summaries.

## 6. Migration, Deployment, and Operations

- [ ] 6.1 Run the full Ruff, mypy, pytest, dependency, repository-policy, deterministic export, HTML/link, privacy, integrity, and workflow validation suites.
- [ ] 6.2 Update source, architecture, and Pages operations documentation with supported slip-opinion scope, latest-activity semantics, daily schedule, pending behavior, and freshness monitoring.
- [ ] 6.3 Build and inspect a no-model generated-content date/order migration candidate, then deploy it through the guarded exact-artifact path.
- [ ] 6.4 Run a bounded non-deploying live validation for the newest disposition-only case and inspect all public/state artifacts and sanitized logs.
- [ ] 6.5 Deploy the retained newest-case candidate without reprocessing, verify all newest-first live surfaces and release/state identity, then process the remaining newest activity under bounded nightly runs.
