## ADDED Requirements

### Requirement: Historical document metadata extraction
The system SHALL deterministically extract versioned metadata candidates from archived Supreme Court PDFs, including path-derived document type, canonical docket numbers, title or caption, explicit dates, term, and field-level document provenance.

#### Scenario: Transcript metadata is recoverable
- **WHEN** an archived transcript opening page contains a docket caption and oral-argument date
- **THEN** the candidate contains the canonical docket, caption-derived title, argument date, Court term, transcript document type, source hash, and extraction method

#### Scenario: Metadata is ambiguous
- **WHEN** an archived document contains conflicting docket or caption candidates
- **THEN** the candidate is marked ambiguous and no canonical case metadata is mutated

### Requirement: Historical document type recovery
The system SHALL classify preserved historical documents from their import-path category and SHALL reject unsupported path categories rather than guessing.

#### Scenario: Preserved opinion path
- **WHEN** a manifest source import path has an `opinion` parent directory
- **THEN** the recovery candidate and applied document references use the opinion document type

### Requirement: Historical docket normalization
The system SHALL normalize standard, application, consolidated, Unicode-hyphenated, soft-hyphenated, and original-jurisdiction docket labels into deterministic canonical forms.

#### Scenario: Original-jurisdiction docket
- **WHEN** a source labels a matter `No. 65, Orig.`
- **THEN** the parser returns canonical docket `65O` and retains the source label as provenance or alias metadata

#### Scenario: Soft-hyphenated docket
- **WHEN** a transcript labels a docket with a soft hyphen between its prefix and number
- **THEN** the parser returns the same canonical form as the equivalent ASCII-hyphenated docket

### Requirement: Document-level case clustering
The system SHALL build recovered case components from canonical document dockets and shared hashes rather than assuming each historical source group represents one case.

#### Scenario: Polluted historical group
- **WHEN** one historical group contains documents with unrelated canonical dockets
- **THEN** the recovery plan creates separate case components and reports that the historical group was split

#### Scenario: Shared docket across historical groups
- **WHEN** documents in separate historical groups identify the same canonical docket
- **THEN** the recovery plan produces one case component while retaining every historical-group association

### Requirement: Conservative metadata resolution
The system SHALL resolve each metadata field from explicit primary-source evidence, prefer stronger existing normalized metadata, and retain unresolved status when required identity fields are not supported.

#### Scenario: Opinion has explicit decision date
- **WHEN** an opinion identifies a docket, term, title, and explicit decision date
- **THEN** the recovered case is decided and records the opinion hash as provenance for those fields

#### Scenario: Transcript-only case
- **WHEN** a transcript identifies a docket, title, term, and argument date but no decision source is archived
- **THEN** the recovered case lifecycle is argued and its decision section remains unavailable to later generation

#### Scenario: Existing curated case matches
- **WHEN** a recovered component's docket matches an existing normalized case
- **THEN** documents and provenance are merged without replacing stronger existing metadata or changing the existing case ID

### Requirement: Reviewable atomic recovery
The system SHALL produce a deterministic plan before mutation and SHALL apply only a fully validated plan atomically under repository locking.

#### Scenario: Plan-only execution
- **WHEN** an operator runs historical recovery without apply authorization
- **THEN** the system writes coverage, proposed mappings, splits, merges, and conflicts without changing case or manifest files

#### Scenario: Valid plan application
- **WHEN** an operator applies a plan whose hashes and repository preconditions still match
- **THEN** case files and manifest associations are updated atomically and a durable migration report is written

#### Scenario: Repeated application
- **WHEN** the same recovery is applied to an already recovered repository
- **THEN** no canonical files change and the operation reports an idempotent no-op

### Requirement: Referential integrity after recovery
The system SHALL validate that recovered case IDs and primary dockets are unique, all case document hashes exist, all manifest case associations resolve, and no immutable archive blobs are removed or changed.

#### Scenario: Conflicting primary docket
- **WHEN** two proposed recovered cases claim the same primary docket without a valid merge
- **THEN** plan application fails before canonical files are written

### Requirement: Resumable recovered-case backfill
The publication workflow SHALL provide a bounded recovery operation and SHALL feed only successfully recovered cases into the existing resumable evidence and guide pipeline.

#### Scenario: Bounded recovery followed by generation
- **WHEN** a recovery batch is applied and committed
- **THEN** subsequent bounded backfill runs can select those docketed cases while ambiguous unresolved groups remain excluded from publication
