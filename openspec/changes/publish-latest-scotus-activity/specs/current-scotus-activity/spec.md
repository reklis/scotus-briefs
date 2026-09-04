## ADDED Requirements

### Requirement: Independent current slip-opinion discovery
The nightly pipeline SHALL poll the active term's reviewed official slip-opinion index independently of oral-argument discovery and SHALL represent each supported individual opinion, per-curiam disposition, or emergency-docket disposition with normalized docket identity, official caption and URL, original publication date, and any listed revision date.

#### Scenario: New emergency-docket opinion has no argument
- **WHEN** the active slip-opinion index adds a supported docket with no oral-argument row or transcript
- **THEN** discovery SHALL create bounded case-level work for that disposition without fabricating an argument session

#### Scenario: Slip opinion is revised
- **WHEN** the official index lists a revision or the accepted official document bytes change
- **THEN** discovery SHALL retain the logical document identity, record the authoritative revision activity date, and select the case for an immutable recomputation revision

#### Scenario: Bulk order list is encountered
- **WHEN** an omnibus order-list document does not expose one supported individual slip-opinion row
- **THEN** this capability SHALL NOT create synthetic cases from its contents

#### Scenario: Previously published row disappears
- **WHEN** one poll does not contain a previously observed slip-opinion row
- **THEN** the pipeline SHALL preserve the active case and SHALL NOT infer deletion or retraction

### Requirement: Argued and disposition-only public cases
The pipeline MUST support a case with zero or more real oral-argument sessions and one or more official dispositions, while requiring complete transcript processing for every argument session that actually exists and a dated official disposition for every newly published zero-session case.

#### Scenario: Argued case receives an opinion
- **WHEN** an official disposition matches an existing argued case by normalized docket
- **THEN** the accepted public revision SHALL preserve its real argument sessions and add the dated official disposition

#### Scenario: Disposition-only case passes validation
- **WHEN** a zero-session case is grounded in a complete supported official disposition and required docket metadata and passes all legal-status, completeness, sensitivity, privacy, and integrity validators
- **THEN** the site SHALL publish a complete case page without an oral-argument date, analysis, or transcript link

#### Scenario: Disposition-only draft invents an argument
- **WHEN** generated content for a zero-session case claims an oral argument, advocate exchange, or justice question not present in its evidence
- **THEN** publication SHALL fail for that case and its rejected prose SHALL not be reused

#### Scenario: Disposition evidence establishes Court action
- **WHEN** accepted opinion or order evidence has Court-held or Court-ordered legal status
- **THEN** deterministic correlation SHALL derive `decided` or `order-issued` status and SHALL NOT leave the case labeled merely `argued`

### Requirement: Authoritative latest Court activity date
Every public case SHALL expose a deterministically derived latest Court document date equal to the maximum known official argument date, disposition publication date, and disposition revision date, excluding retrieval, processing, build, and article timestamps.

#### Scenario: Opinion follows argument
- **WHEN** a case argued in January receives an opinion in June
- **THEN** its latest Court document date SHALL be the June opinion date

#### Scenario: Revised opinion follows publication
- **WHEN** the official index lists a revision after the original opinion date
- **THEN** the revision date SHALL become the case's latest Court document date

#### Scenario: Migrated disposition has no established date
- **WHEN** a legacy public case has an official disposition URL but no exact reviewed index match
- **THEN** migration SHALL preserve the URL and use the latest real argument date as fallback without inferring a disposition date from processing metadata

#### Scenario: Client supplies inconsistent latest date
- **WHEN** a public or generated-state payload's latest Court document date differs from its dated arguments and dispositions
- **THEN** contract or release validation SHALL reject the payload

### Requirement: Consistent newest-first public ordering
The system MUST use one deterministic case ordering policy, primarily latest Court document date descending, for the root homepage, SCOTUS homepage, free-text search, unfiltered search results, term archives, status archives, topic archives, corrections listings, general case listings, and their pagination.

#### Scenario: Older argument has newer opinion
- **WHEN** case A was argued before case B but case A has a later official opinion or revision date
- **THEN** every shared case-list surface SHALL place case A before case B

#### Scenario: Search filter is applied
- **WHEN** a reader filters or searches the static index
- **THEN** matching results SHALL retain the same relative newest-first order as the generated site

#### Scenario: Case has no argument session
- **WHEN** a disposition-only case is included in a listing
- **THEN** ordering SHALL use its disposition activity date without an empty-collection failure or fabricated argument date

#### Scenario: Generated listing order is altered
- **WHEN** search data or paginated archive links do not exactly match the shared deterministic order
- **THEN** static release validation SHALL fail before deployment

### Requirement: Newest-first bounded nightly processing
Each nightly run SHALL discover supported current activity before applying case limits, prioritize new and changed official activity by authoritative date descending, explicitly reconsider persisted pending work, and then process lower-priority migrations and rotating historical rechecks under finite shared budgets.

#### Scenario: More fresh cases exist than the case limit
- **WHEN** newly discovered supported cases exceed the configured nightly case limit
- **THEN** the newest official activity SHALL be selected first and every unselected case SHALL remain represented in sanitized pending state

#### Scenario: Newest case fails locally
- **WHEN** processing the newest selected case fails during its case-local source, parse, extraction, generation, or validation stage
- **THEN** the failure SHALL remain pending and processing SHALL continue with later selected cases while budget remains

#### Scenario: One later case succeeds
- **WHEN** one or more selected cases fail but a later case produces a complete validated public revision
- **THEN** the nightly workflow SHALL deploy the successful complete candidate and SHALL preserve prior versions of failed cases

#### Scenario: No public content changes
- **WHEN** discovery and pending reconsideration find no accepted public change
- **THEN** the workflow SHALL avoid a Pages deployment while safely persisting only allowed successful checkpoints and pending metadata

### Requirement: Nightly freshness accounting
The pipeline MUST produce and validate sanitized freshness metadata sufficient to distinguish the newest supported activity discovered, published, deferred, and failed without retaining source bodies or private model data.

#### Scenario: Supported discovery exceeds model capacity
- **WHEN** a nightly run discovers more supported activity than it can process
- **THEN** each deferred case SHALL be represented by allowlisted pending metadata and the freshness summary SHALL report the newest pending activity date

#### Scenario: Discovered activity is lost
- **WHEN** a supported discovered case is neither present in the candidate projection nor represented in candidate pending state
- **THEN** validation SHALL fail before deployment or checkpoint promotion

#### Scenario: Operator reviews nightly outcome
- **WHEN** a scheduled run completes
- **THEN** its safe summary SHALL report counts and newest dates for discovered, published, and pending supported activity without source text, prompts, model output, credentials, or rejected prose

### Requirement: Explicit compatibility migration
The system SHALL migrate prior public/state/search contracts explicitly while preserving stable case identities, canonical paths, immutable accepted revision bytes, release-parent compare-and-swap safety, and deterministic fallback for unknown disposition dates.

#### Scenario: Existing generated-content is loaded
- **WHEN** the new pipeline reads the active prior schema
- **THEN** a reviewed compatibility path SHALL either validate and migrate it deterministically or stop before source/model processing with an explicit migration requirement

#### Scenario: Exact official date match exists
- **WHEN** a legacy disposition URL and docket exactly match a reviewed official index entry
- **THEN** migration SHALL backfill its publication/revision dates without a model call

#### Scenario: Migration candidate fails integrity validation
- **WHEN** any case pointer, immutable revision digest, search entry, manifest file, or release parent is inconsistent
- **THEN** migration SHALL fail closed and leave the current Pages release and generated-content branch unchanged
