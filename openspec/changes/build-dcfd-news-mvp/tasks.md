## 1. Project Foundation and Contracts

- [x] 1.1 Establish the repository workspace, service boundaries, configuration convention, local development environment, linting, type checking, and automated test commands.
- [x] 1.2 Define and test the versioned capture-envelope schema, including receiver identity, talkgroup, UTC timing, frequency, encryption status, audio properties, decoder metadata, and SHA-256 digest.
- [x] 1.3 Define and test shared schemas for transcript revisions, evidence ranges, typed observations, sensitivity labels, incidents, approved public claims, story revisions, and edge heartbeats.
- [x] 1.4 Add PostgreSQL migrations for receivers, captures, object state, durable jobs and leases, transcript revisions, observations, incidents, incident-observation links, state history, policy decisions, story revisions, and public projections.
- [x] 1.5 Create representative fixtures for clear calls, encrypted calls, duplicates, routine acknowledgements, corrected locations, negation, sensitive medical content, multi-call fires, cancellations, and cross-hour incidents.
- [x] 1.6 Document configurable MVP defaults, including selected talkgroups, RF coverage declaration, retry limits, evidence thresholds, publication allowlist, grace period, and private retention periods.

## 2. Secure Cluster Ingestion

- [x] 2.1 Implement receiver authentication and authorization with encrypted transport and receiver-scoped object prefixes.
- [x] 2.2 Implement capture-manifest creation and private S3-compatible upload initiation without storing audio bytes in PostgreSQL.
- [x] 2.3 Implement upload commit validation for schema version, required metadata, content type, byte count, and digest.
- [x] 2.4 Implement deterministic idempotency handling that acknowledges matching retries and rejects capture-ID digest conflicts.
- [x] 2.5 Implement atomic transition from validated capture to ready state with one durable transcription job.
- [x] 2.6 Implement PostgreSQL job claiming, leases, retry/backoff, lease expiry, terminal failure, and idempotent stage completion.
- [x] 2.7 Implement expiration and cleanup for abandoned uploads, orphaned objects, expired audio, and expired transcript text while preserving required provenance.
- [x] 2.8 Add integration tests covering unauthorized access, malformed manifests, digest failure, duplicate delivery, conflicting duplicates, abandoned uploads, worker death, and retention.

## 3. Raspberry Pi Capture and Forwarding

- [x] 3.1 Create a documented Trunk Recorder configuration profile for one HackRF, one initial RF window, and the selected clear DCFD Dispatch, Main, and incident talkgroups.
- [x] 3.2 Implement finalized-call detection that ignores partial files, encrypted calls, and non-allowed talkgroups and produces the versioned capture envelope.
- [x] 3.3 Implement a crash-safe local spool with atomic state transitions for finalized, uploading, acknowledged, retryable, and conflicted calls.
- [x] 3.4 Implement deterministic capture-ID and audio-digest generation and preserve them across upload retries.
- [x] 3.5 Implement authenticated manifest creation, object upload, commit, acknowledgement handling, and exponential retry against the cluster ingestion service.
- [x] 3.6 Implement acknowledged-call grace cleanup, spool capacity thresholds, visible backpressure, and preservation of unacknowledged calls.
- [x] 3.7 Implement periodic edge heartbeats containing RF coverage, control-channel activity, last call and acknowledgement, spool depth/age, disk, dropped samples, clock offset, CPU temperature, and version data available on the host.
- [x] 3.8 Package the edge components as supervised Raspberry Pi services with restart behavior, least-privilege credentials, log rotation, and operational setup documentation.
- [x] 3.9 Add edge tests for partial files, network outage, lost acknowledgement, process restart, duplicate retry, conflicting content, full spool, encrypted calls, and quiet-but-healthy reception.

## 4. Private Speech-to-Text Processing

- [x] 4.1 Implement the private STT worker interface and an initial configurable Whisper-compatible adapter for Kubernetes execution.
- [x] 4.2 Implement secure, time-limited audio retrieval and audio validation/conversion without exposing object credentials to public workloads.
- [x] 4.3 Add versioned DCFD unit, talkgroup, DC street, quadrant, and landmark hint sets and record the hint-set version with each run.
- [x] 4.4 Persist immutable transcript revisions with model/configuration identity, timing, confidence signals, normalized text, and source-capture provenance.
- [x] 4.5 Detect silent, non-speech, and unintelligible calls and complete them with an explicit non-transcribable status rather than generated text.
- [x] 4.6 Support idempotent retry and authorized reprocessing with a different STT model or configuration without overwriting prior revisions.
- [x] 4.7 Add STT tests using noisy, clipped, silent, jargon-heavy, location-sensitive, and negation-sensitive fixtures.

## 5. Structured Observation Extraction

- [x] 5.1 Implement the strict observation schema for typed claims, raw and normalized values, confidence, epistemic status, source revision, and supporting evidence range.
- [x] 5.2 Implement a configurable OpenAI-compatible extraction adapter that receives transcript and talkgroup context and returns only schema-constrained observations.
- [x] 5.3 Implement validation that rejects missing evidence ranges, malformed fields, unsupported claims, guessed location components, and lost negation.
- [x] 5.4 Implement modality extraction that distinguishes reports, dispatches, responses, arrivals, on-scene observations, escalations, cancellations, containment, resolution, and corrections.
- [x] 5.5 Implement routine-content and sensitivity classification for medical content, names, exact unit addresses, behavioral health, suicide, overdose, juvenile involvement, and configured privacy categories.
- [x] 5.6 Implement normalization for DC addresses, blocks, intersections, quadrants, units, talkgroups, incident types, and timestamps while retaining raw values and uncertainty.
- [x] 5.7 Persist immutable extraction revisions with model, prompt/schema, and vocabulary versions and support idempotent retry and reprocessing.
- [x] 5.8 Add adversarial extraction tests for “no smoke,” uncertain quadrants, corrected addresses, unsupported casualty counts, routine acknowledgements, and personally identifying details.

## 6. Incident Correlation and State

- [x] 6.1 Implement deterministic candidate matching based on normalized location, event time, incident talkgroup, unit overlap, compatible incident type, and explicit references.
- [x] 6.2 Implement durable incident creation and linking that remains idempotent when observations or jobs are replayed.
- [x] 6.3 Implement incident lifecycle transitions for candidate, corroborating, publishable, active, resolved, corrected, retracted, and suppressed states.
- [x] 6.4 Implement evidence weighting that preserves dispatch versus on-scene modality and prevents routine or single low-confidence observations from becoming publishable.
- [x] 6.5 Implement append-only handling of corrections, contradictions, downgrades, cancellations, and superseded values.
- [x] 6.6 Implement cross-hour correlation against active incidents and a configurable lookback window without creating hourly duplicates.
- [x] 6.7 Implement sensitivity aggregation that cannot be erased by later lower-sensitivity observations.
- [x] 6.8 Implement versioned correlation rules and deterministic replay tooling for rebuilding and comparing derived incident state.
- [x] 6.9 Add correlation tests for one incident across talkgroups, similar incidents at different locations, cross-hour updates, cancelled candidates, stronger later evidence, duplicate observations, and mandatory suppression.

## 7. Publication Policy and Grounded Generation

- [x] 7.1 Implement a default-deny publication policy with explicit rules for significant fires, rescues, hazmat responses, major collisions or entrapments, and escalated incidents.
- [x] 7.2 Implement category-specific corroboration thresholds that distinguish reported, dispatched, and on-scene-confirmed conditions.
- [x] 7.3 Implement mandatory suppression for routine medical responses and configured sensitive categories, including exclusion from public aggregates.
- [x] 7.4 Implement deterministic sanitization and location generalization that removes names, patient details, source-radio IDs, and exact residential unit numbers before generation.
- [x] 7.5 Implement approved-public-claim creation with allowed certainty, public value, source observation IDs, and policy-decision audit records.
- [x] 7.6 Implement an OpenAI-compatible story generator that accepts only approved claims and returns a structured title, summary, timeline, status, and supporting claim IDs.
- [x] 7.7 Implement a fail-closed story validator that rejects unsupported facts, stronger certainty, privacy violations, unknown claims, and absent causes, casualties, or outcomes.
- [x] 7.8 Add publication-policy and generation tests for confirmed fires, unconfirmed cancellations, unknown categories, sensitive mixed incidents, unsupported LLM additions, and dispatch-only wording.

## 8. Hourly Publisher and Public Site

- [x] 8.1 Implement the hourly watermark scheduler with a configurable late-arrival grace period and deferral of incompletely analyzed incidents.
- [x] 8.2 Implement append-only story revisions that retain one public story ID across active, resolved, corrected, and retracted updates.
- [x] 8.3 Implement atomic public-projection generation and rollback so a failed cycle leaves the previous known-good projection available.
- [x] 8.4 Build the read-only public site with active incidents, resolved incidents, individual story timelines, hourly digests, and a current-day view sourced only from the public projection.
- [x] 8.5 Add automation, radio-source, hourly-delay, incomplete-coverage, correction, and non-emergency disclaimers to every story and digest surface.
- [x] 8.6 Ensure empty hourly digests say that no qualifying incidents were published rather than claiming no emergencies occurred.
- [x] 8.7 Add automated boundary tests proving public responses, static assets, metadata, logs, and counts contain no audio URI, transcript, private observation, source-radio ID, suppressed incident, or credential.
- [x] 8.8 Add end-to-end tests from fixture capture through hourly story creation, later update, resolution, correction, retraction, and failed-cycle rollback.

## 9. Deployment, Security, and Operations

- [x] 9.1 Create Kubernetes deployment configuration for ingestion, workers, scheduler, private-preview site, PostgreSQL connectivity, object storage, retention jobs, and public site.
- [x] 9.2 Apply least-privilege service accounts, secrets, network policies, private bucket policy, public/private ingress separation, resource requests, and worker concurrency limits.
- [x] 9.3 Export metrics and structured logs for capture heartbeat freshness, control-channel activity, ingestion lag, object failures, job backlog, STT latency, extraction failures, candidate counts, suppression decisions, and publication outcomes.
- [x] 9.4 Create alerts and operational runbooks for receiver failure, RF degradation, clock drift, edge backlog, low disk, cluster backlog, retention failure, private-data access denial, and publication failure.
- [x] 9.5 Implement backup and recovery procedures for PostgreSQL incident/story state and verify that private source objects are excluded or retained according to policy.
- [x] 9.6 Add supply-chain and security checks for dependencies, images, secret leakage, container privileges, and public endpoint exposure.

## 10. Private Validation and Launch Gate

- [ ] 10.1 Validate control-channel decoding, HackRF calibration, antenna placement, frequency coverage, Pi temperature, dropped samples, and simultaneous recorder capacity at the intended receiver location.
- [ ] 10.2 Run upload fault-injection tests covering network loss, cluster downtime, duplicate retries, process restarts, and spool recovery on the actual edge hardware.
- [x] 10.3 Define measurable capture-completeness, transcription, location, negation, incident-grouping, privacy, and factual-grounding acceptance thresholds for public launch.
- [ ] 10.4 Operate the full pipeline in private-preview mode for at least seven days and compare sampled transcripts, observations, incident states, and generated stories with retained audio and later known outcomes.
- [ ] 10.5 Tune vocabulary, evidence thresholds, correlation rules, privacy policy, and public location precision from documented validation failures, then rerun affected evaluation cases.
- [ ] 10.6 Verify audio and transcript lifecycle deletion, correction provenance, receiver/pipeline alerts, and public/private access boundaries in the deployed environment.
- [ ] 10.7 Document the measured limitations of single-receiver coverage and add the resulting coverage statement to the public site.
- [ ] 10.8 Enable public hourly publication only after all launch thresholds pass; document the kill switch that disables new publication while preserving the last safe projection.
