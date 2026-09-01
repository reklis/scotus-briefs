## ADDED Requirements

### Requirement: Receiver ingestion is authenticated and scoped
The ingestion service SHALL authenticate each edge receiver over encrypted transport and SHALL authorize writes only for that receiver's configured identity and object prefix.

#### Scenario: Authorized receiver submits a manifest
- **WHEN** a receiver presents valid scoped credentials and a valid capture manifest
- **THEN** the service permits creation of the corresponding private upload and ingestion record

#### Scenario: Invalid receiver credentials are presented
- **WHEN** a manifest is submitted without valid receiver credentials
- **THEN** the service rejects the request without creating an object or processing job

### Requirement: Ingestion validates call integrity
The ingestion service SHALL validate capture schema version, required metadata, byte size, content type, and audio digest before making a call available for analysis.

#### Scenario: Valid upload is committed
- **WHEN** uploaded audio matches the manifest size and digest and all required fields are valid
- **THEN** the service atomically marks the call ready and creates a durable analysis job

#### Scenario: Uploaded content fails validation
- **WHEN** audio size or digest differs from the manifest or required metadata is invalid
- **THEN** the service rejects the commit, creates no analysis job, and records a diagnostic status

#### Scenario: Upload is abandoned
- **WHEN** an upload is created but not committed within the configured timeout
- **THEN** the service marks it expired and makes its orphaned object eligible for deletion

### Requirement: Duplicate delivery is harmless
The ingestion service SHALL use the receiver and capture identifier as an idempotency key and SHALL prevent duplicate logical calls and duplicate active jobs.

#### Scenario: Matching call is delivered twice
- **WHEN** an acknowledged capture is submitted again with the same digest
- **THEN** the service returns the existing acknowledgement and does not create another logical call or processing job

#### Scenario: Conflicting duplicate is delivered
- **WHEN** an existing capture identifier is submitted with a different digest
- **THEN** the service rejects the conflict and preserves the original call unchanged

### Requirement: Audio and transcripts remain private
The system SHALL store call audio and transcript revisions in private storage inaccessible to unauthenticated users and public-site workloads.

#### Scenario: Public client requests source material
- **WHEN** an unauthenticated or public-site client attempts to retrieve an audio object or transcript
- **THEN** access is denied without revealing private object credentials or contents

#### Scenario: Analysis worker requests a call
- **WHEN** an authorized analysis worker claims a call job
- **THEN** it receives time-limited access sufficient to process only the required private object

### Requirement: Analysis work is durable and retryable
The ingestion subsystem SHALL maintain durable stage state and SHALL permit workers to retry failed analysis without duplicating completed stage outputs.

#### Scenario: Worker terminates during transcription
- **WHEN** a worker loses its claim before completing the stage
- **THEN** the job becomes claimable again after its lease expires

#### Scenario: Completed stage is delivered again
- **WHEN** a worker retries a stage that already has a successful output for the same model and input version
- **THEN** the system reuses the completed output or completes idempotently without creating a conflicting revision

### Requirement: Private source material follows lifecycle policy
The system SHALL apply configurable retention periods to audio, transcripts, failed uploads, and orphaned objects, and SHALL retain derived provenance needed for deduplication and corrections.

#### Scenario: Audio reaches its retention deadline
- **WHEN** a private audio object is older than the configured retention period and has no active processing lease
- **THEN** the system deletes the object while retaining its manifest, digest, and derived provenance

#### Scenario: Transcript reaches its retention deadline
- **WHEN** a private transcript revision reaches its configured retention deadline
- **THEN** the system deletes or irreversibly removes the transcript text while preserving non-sensitive processing and provenance metadata required by policy
