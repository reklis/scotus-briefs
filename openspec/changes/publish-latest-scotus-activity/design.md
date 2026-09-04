## Context

The current site has a correct nightly cron and automatic deployment path, but both discovery and public presentation are argument-centric. Slip-opinion indexes are consulted only to attach documents to dockets already found in argument indexes; index dates are discarded; cases without transcripts are dropped; brief/public contracts require an argument; and `latest_court_document_date()` actually returns the latest argument date. Consequently, recent emergency-docket slip opinions are absent and later merits dispositions remain buried under older argument dates.

Production must stay static-only. Court source files, extracted text, prompts, observations, and model responses remain ephemeral on Spark. Public state may retain only typed official metadata, digests, validators, revisions, pending metadata, and sanitized projections. Every publication gate remains fail closed, while one bad case must not prevent a later valid case from publishing.

## Goals / Non-Goals

**Goals:**

- Discover every supported row and revision on the active-term official slip-opinion index independently of argument discovery.
- Publish grounded briefs for disposition-only cases without inventing an argument session or transcript.
- Preserve official publication/revision dates and expose one authoritative latest Court activity date.
- Apply one deterministic newest-first order to every case list and search result.
- Attempt fresh activity in newest-first order every night, carry failures as explicit pending work, and deploy unrelated successes automatically.
- Backfill dates for existing official disposition URLs without re-running the model where exact official index matching is possible.
- Detect operational staleness without exposing source bodies or private processing state.

**Non-Goals:**

- Republishing Court PDFs, complete source pages, transcripts, or bulk order lists.
- Creating briefs for every certiorari denial or every line in an omnibus order list.
- Treating processing timestamps, HTTP headers, or article revision times as Court publication dates.
- Weakening grounding, privacy, legal-status, completeness, budget, or release-integrity checks.
- Replacing the static GitHub Pages architecture or local Ollama model.

## Decisions

### 1. Treat slip-opinion rows as first-class case discovery

Add a strict parser for the active term's official slip-opinion table. Each entry records normalized docket(s), official caption, official PDF URL, original publication date, optional latest revision date/reference, and disposition kind. A stable logical identity derives from term, normalized primary docket, and official row/document identity rather than mutable bytes.

Argument discovery and slip-opinion discovery feed a case-level candidate with zero or more argument sessions and one or more case-level documents. Exact normalized docket joins the streams. A slip entry may create a case even when no argument row exists. Missing rows never imply deletion.

Only individually published slip-opinion rows are in initial scope. Omnibus order-list contents and lower-court documents are not expanded into cases. This captures the recent emergency dispositions visible on the Court's slip-opinion page while retaining a bounded, reviewed source surface.

Alternatives rejected: fabricating argument sessions would corrupt provenance; scraping docket pages for all emergency applications would be broader, more expensive, and less authoritative than starting from the Court's own published opinion index.

### 2. Preserve dated dispositions in state and public contracts

Introduce a structured official disposition containing kind, URL, publication date, and optional revision date. Public cases have zero or more arguments and one or more dispositions; a newly generated zero-argument case must have a dated disposition. Existing URL-only legacy dispositions remain readable during explicit migration and use the latest real argument date as fallback until matched.

`latest_court_document_date` is computed, not model-authored: maximum of all argument dates, disposition publication dates, and disposition revision dates. Processing/check timestamps are excluded. Contract validation recomputes and verifies it.

Use a versioned schema migration rather than silently inserting fields into immutable V1 revision bytes. The migration preserves stable case keys/URLs and prior revision payloads, records a new active sanitized revision only where the public case contract changes, and rebuilds release/search artifacts under compare-and-swap protection.

### 3. Add a disposition-only analysis path

Documents belong to the case, not exclusively to an argument session. Existing transcript sessions still require complete transcripts and retain current argument-analysis validation. For zero-session cases, extraction runs over the official disposition and required docket metadata, and brief generation emits no argument analysis. The public page renders issue, background, positions only when evidence supports them, Court action/holding, provenance, and revision history without oral-argument claims or links.

Legal status is derived deterministically from opinion/order evidence and cannot remain `argued` when accepted Court-held/Court-ordered disposition evidence is present. Observation type/legal-status combinations are validated so model labels cannot create inconsistent status. The minimum evidence/section policy is specialized for disposition-only cases but does not permit unsupported filler.

Alternatives rejected: posting title-only placeholders would violate the existing complete-brief boundary; requiring transcripts permanently excludes emergency decisions.

### 4. Make Court activity the sole case-list ordering key

Keep `sort_cases()` as the shared policy. Its primary key is `latest_court_document_date` descending, followed by deterministic term/docket/slug tie-breakers. It must not call `max()` on an empty argument collection.

The exporter supplies this exact order to the homepage, root search interface, SCOTUS index, term/status/topic/corrections listings, and pagination. Argument-date archives remain session-specific but order matching cases by latest overall activity. The search index adds latest activity date and keeps argument date optional; JavaScript preserves the validated index order after filtering. Validators compare exact ordered paths rather than sets and reject a reordered search index or archive.

Arguments and revision history within a case remain chronological; newest-first applies to case lists.

### 5. Schedule newest fresh activity before backlog work

Selection uses a stable priority tuple: new or changed supported Court activity first; authoritative activity date descending; persisted pending retries; processor migrations/current rechecks; rotating historical work; normalized case key tie-break. Case limits are applied after this ordering. Persisted pending work is explicitly reconsidered rather than relying only on rediscovery.

A case-local download, parse, extraction, model, or candidate-validation failure records a sanitized pending reason and processing continues. Only a global safety failure or genuinely exhausted shared budget stops the remaining queue. Any complete successful cases produce a publishable candidate and scheduled runs deploy it automatically through the existing validated Pages/CAS path.

### 6. Track freshness explicitly

The build records a sanitized freshness summary: newest supported official activity discovered, newest activity published, newest activity pending, and counts by outcome. A validator fails promotion if discovered supported activity is neither published nor represented as pending, or if ordering metadata disagrees with official index metadata. Staleness itself may remain pending under finite budgets; it is visible rather than silently omitted.

No caption text, source body, prompt, or model payload is added to operational logs. Public summaries use dates, counts, dockets/case keys, fixed result codes, and official URLs already allowed by state policy.

## Risks / Trade-offs

- **[Slip-index markup or docket formats change]** → Use strict bounded parsing, fixtures for hyphenated and `A` dockets, reject ambiguous rows, preserve the prior release, and record a fixed source-parser failure.
- **[Consolidated cases map one opinion to multiple dockets]** → Preserve all normalized docket references, choose the Court row's primary docket deterministically, and merge only on exact normalized identities.
- **[Disposition-only model output invents oral argument]** → Use a separate schema/prompt, zero argument-analysis allowance, prohibited transcript language checks, and final contract validation.
- **[Schema migration rewrites accepted content]** → Preserve V1 revision bytes, use explicit compatibility readers/migration receipts, append only deterministic sanitized contract updates, and validate every digest chain.
- **[Date backfill is incomplete]** → Use only exact official URL/docket matches; retain deterministic argument-date fallback and pending backfill metadata rather than guessing.
- **[Newest failing case starves later work]** → Continue per-case and maintain bounded retry ordering so later fresh and pending cases receive attempts.
- **[Court publishes more work than nightly model capacity]** → Always discover and persist sanitized pending identities first, process newest-first within finite limits, and permit guarded retained-candidate deployment/manual bounded drains.

## Migration Plan

1. Add parser fixtures and first-class slip-disposition discovery with official dates while leaving publication disabled for zero-session cases.
2. Add versioned state/public/search contracts and compatibility readers; migrate exact legacy disposition dates from reviewed official indexes without model calls.
3. Update ordering/export/search/templates/validators and deploy a static-only migration release; verify all list surfaces against one expected order.
4. Add disposition-only extraction/generation/status validation and focused synthetic/live dry runs.
5. Enable newest-first nightly selection, freshness summaries, and automatic deployment; process the newest missing slip opinions in a small retained candidate before widening the queue.
6. Drain remaining fresh and pending work under configured limits, preserving failures for later cycles.

Rollback redeploys the prior release and resets the generated-content active pointer. Old revision files remain immutable and readable; disabling zero-session processing does not require deleting their public pages.

## Open Questions

- None blocking. Initial source scope is the official active-term slip-opinion index, including its listed revisions, plus the existing argument/docket sources. Bulk order-list expansion remains a separate future decision.
