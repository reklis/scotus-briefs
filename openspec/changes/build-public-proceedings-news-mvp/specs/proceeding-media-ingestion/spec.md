## ADDED Requirements

### Requirement: Authorized proceeding media is ingested durably
The system SHALL ingest approved live or archived media into private object storage as immutable assets or bounded chunks with source identity, timestamps, sequence, content type, size, and digest.

#### Scenario: Live proceeding begins
- **WHEN** an enabled source exposes an approved live media method
- **THEN** the system creates ordered immutable chunks and durable analysis jobs without placing media bytes in PostgreSQL

#### Scenario: Archive-only source completes
- **WHEN** an approved official archive appears after a proceeding
- **THEN** the system ingests the archive as a media revision and queues analysis

### Requirement: Live capture survives interruption without hiding gaps
The system SHALL checkpoint live chunk progress, retry transient failures, deduplicate repeated segments, and record discontinuities that cannot be recovered.

#### Scenario: Network connection drops mid-proceeding
- **WHEN** live retrieval is interrupted
- **THEN** collection resumes from a durable checkpoint without deleting acknowledged chunks

#### Scenario: Missing interval cannot be recovered
- **WHEN** neither the live endpoint nor archive supplies a captured interval
- **THEN** the proceeding retains an explicit media gap that analysis and publication must respect

### Requirement: Media delivery is idempotent and integrity checked
The system SHALL validate media schema, source scope, content type, byte count, digest, and sequence identity before making an asset ready.

#### Scenario: Same chunk is delivered twice
- **WHEN** a matching source, proceeding, sequence, and digest is submitted again
- **THEN** the system acknowledges the existing chunk and creates no duplicate analysis job

#### Scenario: Sequence identity conflicts
- **WHEN** the same chunk identity arrives with different content
- **THEN** the system rejects the conflict and preserves both diagnostic provenance and the accepted original

### Requirement: Official documents are versioned evidence assets
The system SHALL ingest approved agendas, dockets, bills, amendments, roll calls, orders, releases, participant rosters, and official transcripts with document type, authority, identifier, revision, publication time, URL, and digest.

#### Scenario: Agenda is revised
- **WHEN** an official source publishes a changed agenda under the same proceeding
- **THEN** the system stores a new immutable document revision and retains the prior one

#### Scenario: Unofficial attachment is linked
- **WHEN** a proceeding page links a document outside the approved authority or host policy
- **THEN** the system does not ingest it as official evidence

### Requirement: Official archives reconcile but do not overwrite live evidence
The system SHALL retain live and archive media revisions separately and SHALL designate canonical analysis input only after timing and integrity reconciliation.

#### Scenario: Archive fills a live gap
- **WHEN** an official archive contains media absent from the live capture
- **THEN** the system records the archive as a stronger complete revision and permits explicit reprocessing

#### Scenario: Archive differs materially
- **WHEN** archive duration or content cannot be reconciled with live chunks
- **THEN** the system flags the discrepancy and does not silently replace published provenance

### Requirement: Source media remains private and follows retention policy
The system SHALL deny source media and raw document extraction to public workloads and SHALL delete private copies according to configured retention while preserving digests and public provenance.

#### Scenario: Public client guesses an asset identifier
- **WHEN** a public client requests a private media chunk or object location
- **THEN** access is denied without revealing storage credentials or object keys
