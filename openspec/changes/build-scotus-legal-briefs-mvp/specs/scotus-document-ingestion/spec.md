## ADDED Requirements

### Requirement: Official transcript and case documents are downloaded durably
The collector SHALL retrieve approved Court documents without redirects, stream bytes through configured bounds, compute SHA-256, and store accepted content in authority/source/case/document-scoped private object keys.

#### Scenario: Valid official transcript PDF is downloaded
- **WHEN** an authorized job receives a Court-hosted transcript with valid type, signature, and size
- **THEN** the object and immutable document revision are committed with byte count and digest

#### Scenario: Download is interrupted
- **WHEN** the connection fails before the accepted object is committed
- **THEN** the job retries from a durable state and no partial object becomes ready

### Requirement: Document identity and content are idempotent
The system SHALL acknowledge repeated delivery of the same document identity and digest and SHALL quarantine conflicting bytes under one official identity.

#### Scenario: Same transcript is processed twice
- **WHEN** official identity and digest match an accepted revision
- **THEN** no duplicate object or parsing job is created

#### Scenario: Court URL returns changed bytes
- **WHEN** an existing document identity produces a different digest
- **THEN** the system retains both revisions and provenance and triggers explicit canonical reconciliation

### Requirement: Document validation fails closed
The system SHALL validate source scope, HTTPS host/path, response status, MIME, byte bounds, PDF signature/decodability, page count, and document type before parsing.

#### Scenario: HTML error page is labeled as PDF
- **WHEN** headers claim PDF but bytes are not a valid PDF
- **THEN** ingestion rejects the asset and enqueues no parsing or analysis work

### Requirement: Transcript parsing preserves page and line provenance
The system SHALL use a versioned parser to extract reading-order text, printed/page number, line range, speaker label, raw private text, and normalized private text while preserving exact source coordinates.

#### Scenario: Transcript contains repeated page headers
- **WHEN** deterministic parsing identifies header/footer artifacts
- **THEN** normalized text removes them while evidence still resolves to the original page and line range

#### Scenario: Reading order is ambiguous
- **WHEN** the parser cannot reliably order transcript blocks
- **THEN** the document revision fails closed and creates no legal observations

### Requirement: Document kinds remain evidentially distinct
The system SHALL distinguish transcript, docket, order, opinion, and other official documents and SHALL prevent one kind from proving a status reserved for another.

#### Scenario: Transcript records an advocate's requested result
- **WHEN** counsel asks the Court to reverse a judgment
- **THEN** extraction may record the requested disposition but cannot record that the Court reversed

#### Scenario: Official opinion states the judgment
- **WHEN** a later Court opinion supports the disposition
- **THEN** analysis may create a holding/disposition observation linked to the opinion revision

### Requirement: Accepted revisions create one processing chain
The system SHALL enqueue exactly one parse/extract chain per accepted document revision and SHALL make each stage idempotent under retries.

#### Scenario: Commit transaction is retried
- **WHEN** document commit runs repeatedly after an uncertain acknowledgement
- **THEN** the database contains one ready revision and one logical parsing chain

### Requirement: Copied documents and extracted text remain private
The system SHALL deny copied PDFs and full extracted text to public workloads and SHALL delete private copies according to retention only when no active job requires them, preserving digests and official provenance.

#### Scenario: Public client guesses a transcript ID
- **WHEN** a public request targets a copied transcript or full extraction
- **THEN** access is denied without revealing object keys or storage credentials
