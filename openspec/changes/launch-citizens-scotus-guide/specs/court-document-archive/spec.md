## ADDED Requirements

### Requirement: Incremental official document discovery
The system SHALL discover Supreme Court cases and documents from configured authoritative sources and normalize discovered entries before downloading content.

#### Scenario: Newly published document is discovered
- **WHEN** an authoritative source lists a document that is not present in the archive manifest
- **THEN** the system records it as a download candidate associated with the available case and source metadata

#### Scenario: Previously discovered document is unchanged
- **WHEN** a source entry resolves to content already represented by the same source identity and SHA-256
- **THEN** the system SHALL skip creating a duplicate archive document

### Requirement: Validated PDF acquisition
The system SHALL validate each candidate download before admitting it to the archive.

#### Scenario: Valid PDF is downloaded
- **WHEN** a candidate returns a successful response, has a PDF signature, has nonzero content, and can be hashed
- **THEN** the system SHALL store the PDF and record its byte size and SHA-256

#### Scenario: Invalid response is downloaded
- **WHEN** a candidate is empty, is not a PDF, or fails retrieval
- **THEN** the system SHALL reject it, report the failure, and leave existing archive state unchanged

### Requirement: Checked-in immutable document storage
The system SHALL store accepted PDFs in content-addressed paths in the Git repository and SHALL treat an accepted PDF blob as immutable.

#### Scenario: Document is first accepted
- **WHEN** a validated SHA-256 is not already present
- **THEN** the system SHALL add the PDF at the path derived from that hash and include it in the next archive commit

#### Scenario: Official URL serves corrected content
- **WHEN** a known official URL later returns a different valid SHA-256
- **THEN** the system SHALL add a new PDF, retain the previous PDF, and record the revision relationship

#### Scenario: Same PDF belongs to multiple dockets
- **WHEN** multiple case records reference identical PDF bytes
- **THEN** the system SHALL store one content-addressed blob and associate each case with that document

### Requirement: Document provenance manifest
The system SHALL maintain schema-validated provenance metadata for every archived PDF.

#### Scenario: Manifest entry is created
- **WHEN** a PDF is accepted
- **THEN** its manifest entry SHALL include the SHA-256, archive path, source URL, official filename when available, retrieval time, size, document type, and known case associations

#### Scenario: Document has been superseded
- **WHEN** a corrected or replacement document is discovered
- **THEN** the manifest SHALL identify which prior document it supersedes without deleting the prior entry

### Requirement: Normalized case identity
The system SHALL maintain case records separately from PDF storage paths and SHALL support one or more docket numbers per case.

#### Scenario: Case metadata is available
- **WHEN** title, term, docket number, status, or relevant dates are obtained from an authoritative source or confidently extracted source text
- **THEN** the system SHALL store those values with their provenance in the normalized case record

#### Scenario: Consolidated case is encountered
- **WHEN** one proceeding or document covers multiple docket numbers
- **THEN** the system SHALL preserve all docket numbers and identify a primary display docket without duplicating the source PDF

#### Scenario: Metadata is ambiguous
- **WHEN** an imported group cannot be confidently associated with a term and docket
- **THEN** the system SHALL preserve it as unresolved and SHALL NOT invent identifying metadata

### Requirement: Existing corpus backfill
The system SHALL import the existing PDF corpus and JSONL checksums without changing accepted PDF bytes.

#### Scenario: Existing manifest entry matches its file
- **WHEN** an imported PDF's size, signature, and SHA-256 match the supplied manifest
- **THEN** the system SHALL admit it to the content-addressed archive and retain its known grouping and document type

#### Scenario: Existing manifest entry fails validation
- **WHEN** an imported PDF is absent or does not match the supplied checksum
- **THEN** the system SHALL report it as a backfill error and SHALL NOT create a valid archive entry for it

### Requirement: Non-destructive reconciliation
The system SHALL preserve all previously accepted documents and case records when an upstream source is incomplete or unavailable.

#### Scenario: Upstream source fails
- **WHEN** discovery receives an error, timeout, or unexpectedly empty result
- **THEN** the system SHALL fail that source run without deleting or marking existing records absent

#### Scenario: One candidate fails among valid candidates
- **WHEN** one document fails validation during a multi-document run
- **THEN** the system SHALL retain successfully validated candidates and report the failed candidate separately
