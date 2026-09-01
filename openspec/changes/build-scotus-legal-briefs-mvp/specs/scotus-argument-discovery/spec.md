## ADDED Requirements

### Requirement: Discovery uses only reviewed Court sources
The system SHALL discover cases and argument material only from enabled, reviewed `www.supremecourt.gov` paths and SHALL fail closed on host, redirect, path-pattern, access-method, or review-status changes.

#### Scenario: Reviewed argument index is polled
- **WHEN** the configured official term index is due for polling
- **THEN** the adapter uses conditional bounded retrieval and processes only reviewed same-host links

#### Scenario: Transcript redirects to another host
- **WHEN** an official transcript URL redirects outside the approved contract
- **THEN** the system creates no download job and marks the source for review

### Requirement: Cases and argument sessions have deterministic identities
The system SHALL identify a case by normalized term/docket information and SHALL represent each argument, consolidated argument, or reargument as a distinct session linked to the durable case.

#### Scenario: Same argument is discovered repeatedly
- **WHEN** repeated polls return the same term, docket, argument date, and official media identity
- **THEN** the system updates one argument session without creating a duplicate

#### Scenario: Case is reargued
- **WHEN** the Court publishes a different official argument session for an existing docket
- **THEN** the system links a new session to the case and preserves the earlier argument

### Requirement: Official metadata changes are append-only
The system SHALL version captions, consolidated dockets, argument dates, URLs, and availability states without deleting prior official metadata.

#### Scenario: Caption or consolidation changes
- **WHEN** the official detail or docket page changes case identity metadata
- **THEN** the system appends a revision and retains the prior value and observation time

### Requirement: Full transcript availability creates one collection candidate
The system SHALL create a document collection candidate only for an explicitly linked Court-hosted official transcript PDF and SHALL identify it by case, argument session, official URL, and source revision.

#### Scenario: New official transcript appears
- **WHEN** an approved argument detail page first links the full transcript PDF
- **THEN** exactly one idempotent transcript-download job is queued

#### Scenario: Detail page has audio but no transcript
- **WHEN** a case has an MP3 link but no full official transcript
- **THEN** the argument remains transcript-pending and no audio or transcript job is created

### Requirement: Related official documents remain distinct evidence
The system SHALL discover official transcripts, docket pages, opinions, and orders as typed document revisions and SHALL not treat one evidence type as another.

#### Scenario: Transcript appears after the argument
- **WHEN** an official transcript PDF is newly linked
- **THEN** it is stored as a transcript document revision and queues deterministic parsing and analysis

#### Scenario: Opinion is later published
- **WHEN** an official opinion index row supports a docket association
- **THEN** the opinion is linked to the durable case without converting oral-argument observations into holdings

### Requirement: Backfill is bounded and lower priority
The system SHALL limit historical discovery by configured terms/case count, enforce Court crawl delay, and queue backfill below newly posted argument work.

#### Scenario: Backfill cap is reached
- **WHEN** the configured historical case limit has been queued
- **THEN** discovery checkpoints progress and creates no additional backfill jobs until the limit changes
