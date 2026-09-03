## ADDED Requirements

### Requirement: Scheduled and manual bounded publication
The system SHALL provide a nightly UTC GitHub Actions workflow and a restricted manual dispatch whose analysis/build job runs only on the dedicated local Spark self-hosted runner from the protected default branch, serializes publication runs, enforces explicit time/runtime/resource limits, and never runs live publication for pull requests or forks.

#### Scenario: Nightly trigger starts
- **WHEN** the configured cron fires on the default branch
- **THEN** one non-cancelled publication cycle SHALL check out the protected source and active public state and apply all configured case, request, byte, disk, runtime, and model budgets

#### Scenario: Pull request executes CI
- **WHEN** code from a pull request or fork is tested
- **THEN** only recorded fixtures SHALL be processed on GitHub-hosted infrastructure and no self-hosted runner, live source, Ollama endpoint, Pages write, generated-content write, database, or object-storage credential SHALL be available

#### Scenario: A prior publication is still running
- **WHEN** another scheduled or manual publication is requested
- **THEN** workflow concurrency SHALL queue or reject overlap without cancelling the active publication midway

### Requirement: Public incremental state
The pipeline MUST load and validate a versioned public snapshot/state from an auditable generated-content branch and MUST persist only deterministic allowlisted public case revisions, official URLs, logical source identities, HTTP validators, content digests/counts, bounded cursors, processor/config fingerprints, pending-work metadata, opaque cost receipts, and release pointers.

#### Scenario: Existing source bytes are unchanged
- **WHEN** conditional discovery returns not-modified or a bounded retrieval matches the recorded digest
- **THEN** the exact prior public case payload SHALL be carried forward without parsing, extraction, or generation

#### Scenario: Public state contains a forbidden field
- **WHEN** loaded or candidate state contains source text, transcript text, observations, claim ledgers, prompts, raw model output, object keys, credentials, internal IDs, or an unknown schema field
- **THEN** the cycle SHALL fail closed before private processing or promotion

#### Scenario: State schema changes incompatibly
- **WHEN** the branch contains an unsupported state schema major version
- **THEN** automated publication SHALL stop and require an explicit migration

### Requirement: Incremental Court discovery
The nightly pipeline SHALL use saved conditional validators where available, poll the active term and configured recent correction/opinion window, revisit older resources through a bounded rotating cursor, and separate manually requested historical bootstrap from routine nightly work.

#### Scenario: Court supplies validators
- **WHEN** an index or document has a stored ETag or Last-Modified value
- **THEN** the next eligible request SHALL send the corresponding conditional headers and record a not-modified result without creating processing work

#### Scenario: Court supplies no reliable validator
- **WHEN** a resource is due for bounded recheck and has no reliable validator
- **THEN** the pipeline SHALL compare a bounded streamed SHA-256 digest while respecting the reviewed host/path, no-redirect, size, rate, and user-agent controls

#### Scenario: Historical bootstrap is requested
- **WHEN** an authorized maintainer manually starts bootstrap mode
- **THEN** terms, cases, requests, bytes, and model calls SHALL remain explicitly bounded and new current-term work SHALL retain priority

### Requirement: Correct document and case revision handling
Logical Court documents MUST be identified independently of their bytes, changed bytes at the same or a new official URL MUST allocate the next immutable revision, and a changed case MUST be recomputed from all currently required canonical argument sessions before replacement.

#### Scenario: Transcript bytes change at the same URL
- **WHEN** a transcript's accepted digest differs from the prior digest for the same logical case/session document
- **THEN** the pipeline SHALL create revision `N+1`, reprocess the whole case, and preserve revision `N` rather than quarantine the update as an identity conflict

#### Scenario: Corrected case passes validation
- **WHEN** changed official material produces a complete grounded and privacy-safe replacement brief
- **THEN** the public case SHALL receive an append-only revision and visible correction metadata while its stable case identity is preserved

#### Scenario: Current source is unavailable or invalid
- **WHEN** a previously published document is missing, unavailable, redirected, malformed, or fails validation during one cycle
- **THEN** the prior public case SHALL remain active, the failure SHALL be recorded without private payloads, and the system SHALL NOT infer deletion or retraction

### Requirement: Ephemeral private processing
All copied Court documents, source-page bodies, extracted transcript text, observations, claim ledgers, prompts, model responses, and private job data MUST exist only in a permission-restricted runner workspace or run-scoped service and MUST never be committed, cached, logged, or uploaded.

#### Scenario: Changed case is processed
- **WHEN** discovery selects a new or changed case
- **THEN** the pipeline SHALL download and process required evidence inside run-scoped storage and SHALL expose only a validated sanitized public case candidate outside that boundary

#### Scenario: Processing succeeds or fails
- **WHEN** the build job exits for any reason
- **THEN** an unconditional cleanup step SHALL remove the private workspace and stop/delete run-scoped data services without uploading their contents

#### Scenario: Operator inspects public logs
- **WHEN** workflow logs and summaries are viewed
- **THEN** they SHALL contain only stage, public case key, status, counts, timings, safe digests, and sanitized error categories, not source/transcript text, model payloads, signed URLs, credentials, or stack traces containing private data

### Requirement: Drainable and budgeted processing
The collector/worker/publisher path MUST support bounded one-shot and drain operation and MUST enforce hard limits for selected cases/documents, downloaded bytes, disk, runtime, extraction calls, brief calls, total model calls, model input/output, and estimated spend.

#### Scenario: Selected queue drains
- **WHEN** no runnable selected job remains and no lease is active
- **THEN** drain mode SHALL exit successfully rather than sleep indefinitely

#### Scenario: Work budget is exhausted
- **WHEN** a hard non-safety budget is reached before all discovered work is complete
- **THEN** unprocessed logical work SHALL remain in sanitized pending state for a later run and SHALL NOT be represented as published or complete

#### Scenario: Unchanged model input was attempted
- **WHEN** the same document, parser, extractor, policy, model, prompt, and relevant configuration fingerprint has a recorded attempted or blocked outcome
- **THEN** another paid call SHALL be denied unless evidence/version input changes or an authorized replay is explicit

#### Scenario: Model transport fails
- **WHEN** a loopback Ollama request times out or fails after its bounded configured attempts
- **THEN** the candidate publication SHALL fail without replacing the current site, and retries SHALL NOT exceed the run's call budget

### Requirement: Full candidate validation and last-known-good publication
A candidate MUST pass source, parse, legal-status, grounding, sensitivity, public-contract, state-consistency, link, accessibility, privacy, and release-integrity validation before deployment; any failed cycle MUST leave the prior Pages release and active generated-content snapshot unchanged.

#### Scenario: Any processing or validation stage fails
- **WHEN** discovery, retrieval, parsing, extraction, correlation, policy, generation, rendering, privacy scanning, or integrity validation fails
- **THEN** the candidate SHALL be discarded and the previous complete site and active snapshot SHALL remain current

#### Scenario: Pages deployment fails
- **WHEN** GitHub Pages does not report successful deployment of the exact candidate release
- **THEN** generated-content promotion SHALL NOT advance its active release pointer

#### Scenario: Deployment succeeds
- **WHEN** Pages reports successful deployment and the expected prior generated-content revision still matches
- **THEN** a no-secret promotion step SHALL atomically record the exact deployed snapshot/state/release as active

#### Scenario: Deployment and branch pointer diverge
- **WHEN** the live release ID and generated-content active release ID differ after an interrupted promotion
- **THEN** the next run SHALL stop normal publication and reconcile by validating/promoting the live release or redeploying the last active branch release

### Requirement: Least-privilege workflow isolation
The workflow SHALL separate build, deployment, and generated-state promotion so each job receives only its required permissions and secrets; all third-party actions and build inputs MUST be pinned and reproducible.

#### Scenario: Build job processes evidence
- **WHEN** the trusted build job runs in the protected publication environment
- **THEN** it SHALL run on the dedicated `self-hosted` Spark runner with read-only repository access, checkout credentials disabled, no Pages or branch-write permission, no external model secret, and access only to loopback Ollama

#### Scenario: Local model is unavailable or changed
- **WHEN** loopback Ollama is unavailable or does not report the exact configured `qwen3.8:27b` model
- **THEN** the build SHALL stop before Court evidence is processed or any candidate is published

#### Scenario: Persistent runner starts or finishes a build
- **WHEN** a publication job starts or exits on the self-hosted host
- **THEN** it SHALL remove stale private/candidate paths, use a permission-restricted run workspace, perform unconditional cleanup, and leave no Court document, extracted text, prompt, or model response in the Actions workspace

#### Scenario: Deployment job publishes files
- **WHEN** a validated artifact is deployed
- **THEN** the GitHub-hosted deployment job SHALL have only `pages: write` and `id-token: write`, no local-model or private-processing access, and a short retention period for the privacy-scanned Pages artifact

#### Scenario: Promotion job updates public state
- **WHEN** a successful release is promoted
- **THEN** the promotion job SHALL have only the explicit contents write needed for compare-and-swap generated-content updates and SHALL receive no model or private storage credential
