## 1. Proceedings Domain Foundation

- [x] 1.1 Add versioned contracts for official sources, proceedings, metadata revisions, media assets/chunks, documents, participants, transcript segments, observations, topics, government events, approved claims, and public stories.
- [x] 1.2 Add enums and validators for jurisdiction, authority, proceeding lifecycle, evidence kind, speaker identity basis, statement/action type, and procedural/legal status.
- [x] 1.3 Add PostgreSQL migrations for the proceedings domain, immutable revisions, durable collection/analysis jobs, append-only links/history, source checkpoints, retention, and public projections.
- [x] 1.4 Add representative fixtures for schedules, postponements, cancellations, live gaps, archive replacement, revised agendas, anonymous testimony, oral-argument questions, floor debate, votes, Council actions, and mayoral announcements.
- [x] 1.5 Add configurable proceeding-source, chunk duration/overlap, archive wait, retention, beat quota, policy, grace-period, and launch-gate defaults.
- [x] 1.6 Document the radio-to-proceedings pivot, retained shared components, dormant edge path, data boundaries, and local development workflow.

## 2. Authorized Source Registry and Discovery Framework

- [x] 2.1 Implement the fail-closed source registry with access-basis review fields, approval audit, host allowlist, rate limits, expected schedule, enabled state, and health state.
- [x] 2.2 Enforce that disabled, unreviewed, expired-review, redirected-host, and changed-access-method sources cannot create media collection jobs.
- [x] 2.3 Define a source-adapter interface for discovery, metadata revisions, approved media descriptors, document descriptors, health, checkpoints, and conditional requests.
- [x] 2.4 Implement deterministic proceeding identity and idempotent schedule discovery with append-only metadata changes.
- [x] 2.5 Implement scheduled, live, delayed, completed, postponed, cancelled, archive-pending, and unavailable lifecycle transitions without inferring completion from silence.
- [x] 2.6 Implement bounded polling, ETag/Last-Modified support, retry/backoff, stale-source detection, and independent quiet-versus-failed health reporting.
- [x] 2.7 Add registry authorization, redirect, duplicate discovery, schedule correction, cancellation, quiet source, and endpoint failure tests.

## 3. Initial Official Source Adapters

- [x] 3.1 Verify and document the exact official Supreme Court calendar, case metadata, live/archived audio, transcript, docket, and opinion/order access methods and their approved automation/reuse basis.
- [x] 3.2 Implement the Supreme Court adapter for argument discovery, case/docket metadata, approved audio descriptors, official transcripts, and later opinion/order documents.
- [x] 3.3 Verify and document the exact House floor schedule, live/archived media, floor activity, bill, amendment, roll-call, and Clerk data access methods and their approved automation/reuse basis.
- [x] 3.4 Implement the House adapter for session discovery, approved media descriptors, floor activity, legislation, amendments, and official vote records.
- [x] 3.5 Verify and document DC Council calendar, LIMS/agendas, participant metadata, live/archive media, legislation, and vote access methods, keeping platform-only media disabled absent permission.
- [x] 3.6 Implement the DC Council adapter for hearings/meetings, agendas, legislation, approved media descriptors, and official actions.
- [x] 3.7 Verify and document official DC mayoral calendar, briefing/release, participant, and live/archive media access methods, keeping platform-only media disabled absent permission.
- [x] 3.8 Implement the mayoral briefing adapter for event discovery, releases, approved media descriptors, and supporting documents.
- [x] 3.9 Add recorded HTTP contract fixtures and adapter tests for every approved method, plus tests proving unapproved platform embeds produce metadata only and no media job.

## 4. Proceeding Media and Document Ingestion

- [ ] 4.1 Implement authority-scoped private object prefixes and ingestion authorization for proceeding media and official documents.
- [ ] 4.2 Implement bounded live-media chunking with source timestamps, sequence, overlap, digest, content type, discontinuity markers, and durable checkpoints.
- [ ] 4.3 Implement archive-media ingestion with streaming digest validation, idempotency, retries, and one durable normalization/transcription chain.
- [ ] 4.4 Implement interrupted-live resumption, repeated-segment deduplication, explicit unrecoverable gaps, and source-end detection.
- [ ] 4.5 Implement immutable official-document ingestion and revision detection for agendas, dockets, bills, amendments, votes, orders, releases, rosters, and transcripts.
- [ ] 4.6 Implement host, redirect, MIME, size, digest, and source-scope validation for all fetched assets.
- [ ] 4.7 Implement live/archive timing reconciliation, canonical media designation, discrepancy review, and explicit reprocessing without overwriting live evidence.
- [ ] 4.8 Implement retention for copied media, chunks, raw document extraction, failed transfers, and orphaned objects while preserving digests and provenance.
- [ ] 4.9 Add integration tests for interruption, duplicate chunks, conflicting content, changed playlists, redirects, gaps, revised documents, archive fill, mismatch, and retention.

## 5. Long-Form Transcription and Speaker Handling

- [ ] 5.1 Extend private audio validation/normalization for authorized proceeding formats, channels, sample rates, silence, and corrupt media.
- [ ] 5.2 Implement chunked Whisper-compatible transcription with proceeding-relative timing, model/config identity, confidence, and idempotent immutable revisions.
- [ ] 5.3 Implement deterministic overlap reconciliation that removes duplicate boundary words and preserves all source revision references.
- [ ] 5.4 Implement explicit gap and unintelligible markers that prevent text from being joined across missing media.
- [ ] 5.5 Implement anonymous diarized speaker labels and evidence-bearing identity assignment from official metadata, authoritative captions/transcripts, or explicit introductions only.
- [ ] 5.6 Add source-specific vocabulary for courts, House procedure, DC legislation, agencies, titles, docket/bill identifiers, and named public officials.
- [ ] 5.7 Add tests for long sessions, crosstalk, applause, recesses, overlapping chunks, capture gaps, title ambiguity, introductions, anonymous witnesses, and identity inference rejection.

## 6. Government Observation Extraction

- [ ] 6.1 Implement the strict proceeding-observation schema with jurisdiction, body, topic, speaker, identity basis, statement/action type, legal status, target identifier, confidence, and exact evidence range.
- [ ] 6.2 Implement separate evidence adapters for spoken segments, official transcripts, agendas, dockets, legislation, amendments, votes, orders, releases, and participant rosters.
- [ ] 6.3 Implement a schema-constrained extraction adapter that receives only bounded transcript segments and approved document context.
- [ ] 6.4 Implement validators that reject unsupported identities, quotations, vote totals, dates, outcomes, policy effects, status upgrades, and document-to-speech conflation.
- [ ] 6.5 Implement source-aware modality rules for questions, arguments, testimony, proposals, announcements, introductions, amendments, motions, adoption, chamber passage, orders, signatures, effectiveness, implementation, denial, and withdrawal.
- [ ] 6.6 Implement normalization for case dockets, bill/resolution numbers, committees, agencies, public roles, dates, votes, and government bodies while preserving raw values.
- [ ] 6.7 Implement private-witness and sensitive-testimony classification for contact details, home addresses, medical information, minors, immigration status, and personal circumstances.
- [ ] 6.8 Persist immutable extraction revisions with model, prompt/schema, vocabulary, document, and media versions and support authorized replay.
- [ ] 6.9 Add adversarial tests for hypothetical judicial questions, disputed testimony, planned agenda votes, House-only passage, revised roll calls, future mayoral intent, and unsupported implementation claims.

## 7. Proceeding, Topic, and Government-Event Correlation

- [ ] 7.1 Implement deterministic association of chunks, transcripts, documents, participants, and observations to authority-scoped proceedings.
- [ ] 7.2 Implement evidence-backed topic boundaries using official identifiers, agenda/docket references, normalized subjects, participants, and temporal continuity.
- [ ] 7.3 Implement durable government-event creation and matching by case, bill, resolution, agency action, official topic references, body, and time.
- [ ] 7.4 Implement lifecycle/history for scheduled, debated, proposed, advanced, passed-one-chamber, adopted, ordered, signed, effective, implemented, denied, withdrawn, corrected, and unresolved developments.
- [ ] 7.5 Implement append-only disagreement, correction, postponement, revised-document, supersession, and withdrawal handling.
- [ ] 7.6 Prevent questions, testimony, debate, schedules, and announcements from deriving final action without supported official evidence.
- [ ] 7.7 Implement cross-proceeding updates such as hearing-to-vote, argument-to-opinion, announcement-to-implementation, and archive-driven correction.
- [ ] 7.8 Add versioned deterministic replay and comparison tooling for proceeding, topic, and event state.
- [ ] 7.9 Add tests for duplicate sessions, similar titles, multi-topic hearings, distinct bills with similar rhetoric, later actions, corrections, and false finalization.

## 8. Proceedings Publication Policy and Generation

- [ ] 8.1 Implement default-deny source-aware rules for consequential rulings/orders, official votes/actions, material legislative developments, oversight/budget findings, and substantive mayoral announcements.
- [ ] 8.2 Suppress routine procedure, ceremonial content, repeated remarks, unsupported predictions, unknown categories, and incomplete/gapped evidence.
- [ ] 8.3 Implement deterministic public-official naming from supported role evidence and default anonymity/sanitization for private witnesses and sensitive testimony.
- [ ] 8.4 Create approved public claims with authority, jurisdiction, official page URL, proceeding time, evidence kind, exact allowed status, and source observation IDs.
- [ ] 8.5 Extend grounded generation for proceeding titles, summaries, key actions, debate context, timelines, status, and supporting claim IDs.
- [ ] 8.6 Implement source-specific fail-closed validation for judicial questions versus holdings, debate versus passage, one-chamber passage versus law, committee versus full-Council action, and announcements versus implementation.
- [ ] 8.7 Add policy and generation tests for consequential actions, routine sessions, private testimony, unknown speakers, unsupported vote counts, legal overstatement, and valid source-linked prose.

## 9. Hourly Proceedings Site and Operations

- [ ] 9.1 Extend the hourly watermark to defer incomplete chunks/documents and publish atomic national and District proceeding projections.
- [ ] 9.2 Implement append-only story revisions and visible corrections when archives or later official documents supersede live-derived evidence.
- [ ] 9.3 Add public national/District beats, proceeding pages, topic timelines, hourly digests, official source links, and jurisdiction/status filters.
- [ ] 9.4 Add automation, delay, incompleteness, correction, source, non-official-record, and non-legal-advice disclaimers to every proceeding surface.
- [ ] 9.5 Ensure public endpoints expose no copied media, bypass media URL, raw transcript, private document extraction, hidden participant metadata, model prompt, credential, or disabled-source data.
- [ ] 9.6 Add source discovery/availability, schedule mismatch, live gap, archive wait, collection throughput, STT backlog, extraction failure, suppression, and publication metrics/logs.
- [ ] 9.7 Add alerts and runbooks for authorization expiry, host/access-method change, source outage, live discontinuity, archive mismatch, analysis backlog, sensitive-data denial, and publication failure.
- [ ] 9.8 Update Kubernetes workloads, schedules, network policies, secrets, service accounts, resource limits, backups, and retention jobs for collectors and proceeding workers.
- [ ] 9.9 Add end-to-end tests from official-source fixtures through schedule revision, live gap, archive reconciliation, topic/event update, hourly story, correction, and failed-cycle rollback.

## 10. Private Validation and Source-by-Source Launch

- [ ] 10.1 Define measurable source discovery, media completeness, transcription, speaker identity, modality/status, topic grouping, privacy, grounding, and public-boundary launch thresholds.
- [ ] 10.2 Run collection fault injection for source/network loss, changed redirects, playlist discontinuity, process restart, duplicate segments, archive delay, and object/job retries.
- [ ] 10.3 Operate at least seven consecutive private-preview days and review every public candidate plus sampled media, transcripts, documents, observations, events, and stories.
- [ ] 10.4 Require at least one representative proceeding before validating a source; leave sources without a qualifying event disabled rather than inferring success.
- [ ] 10.5 Tune source adapters, vocabulary, chunking, status rules, correlation, privacy, and generation from documented failures and rerun affected cases.
- [ ] 10.6 Verify copied-media/document retention, correction provenance, source/pipeline alerts, backup recovery, and public/private access boundaries in deployment.
- [ ] 10.7 Document measured source coverage, schedule/archive latency, known gaps, and the distinction between summarized reporting and official records.
- [ ] 10.8 Enable each source independently only after its access review and launch thresholds pass; document source and global kill switches that preserve the last safe projection.
