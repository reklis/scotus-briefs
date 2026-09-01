## Context

The repository already contains private object ingestion, PostgreSQL-backed jobs, Whisper-compatible STT, evidence-range validation, append-only correlation, grounded generation, hourly projections, and Kubernetes deployment foundations for a radio-derived news MVP. RF reception from Leesburg cannot decode the DC system reliably, and third-party scanner/platform capture lacks approved automation rights. Official government proceedings offer authoritative schedules, public media, and documents, but differ from radio calls: sessions are long, sources are heterogeneous, speaker and legal status matter, and a question or proposal is not government action.

The initial source set is Supreme Court oral arguments, House floor proceedings, DC Council hearings/meetings, and DC mayoral briefings. An official source can still be hosted by a third-party platform; official authorship does not itself authorize automated download through that platform. Every adapter therefore needs both technical validation and a recorded access basis.

## Goals / Non-Goals

**Goals:**

- Discover scheduled, live, completed, postponed, and cancelled proceedings from approved official sources.
- Reliably ingest authorized live media when available and reconcile it with authoritative archives later.
- Combine private transcripts with official agendas, dockets, bills, vote records, orders, releases, and participant rosters while keeping evidence types distinct.
- Extract speaker-aware statements and government actions with precise modality and legal/procedural status.
- Correlate long sessions into durable proceedings, topics, and evolving public events.
- Publish conservative hourly reporting with official source links, jurisdiction, timing, provenance, privacy controls, and corrections.
- Reuse proven infrastructure without forcing proceedings into radio-specific contracts.

**Non-Goals:**

- Circumventing platform controls, downloading media contrary to source terms, or using a generic arbitrary-URL scraper.
- Republishing source audio, video, full transcripts, captions, or protected media.
- Real-time trading, legal advice, emergency alerts, or a verbatim legislative/court record.
- Treating oral-argument questions as holdings, floor debate as enacted law, hearing testimony as adopted policy, or an announcement as completed implementation.
- Comprehensive coverage of every federal or District agency in the MVP.
- Removing the existing HackRF implementation; it remains dormant and outside this launch path.

## Decisions

### 1. Use an explicit, fail-closed source registry

Each source record includes authority, jurisdiction, official index URL, adapter type, discovery and media methods, terms/authority notes, approval timestamp and reviewer, polling limits, expected schedule, enabled state, and health state. A source cannot create collection jobs until enabled with a non-empty access basis. Redirects to an unapproved host or a changed media method disable capture pending review.

A generic `yt-dlp` adapter was rejected because it obscures source-specific terms, breaks unpredictably, and encourages collection from platform URLs that do not authorize downloading. Source adapters may consume documented APIs, feeds, downloadable files, captions, or official HLS endpoints only when that access is approved.

### 2. Model proceedings separately from emergency incidents

Add `official_sources`, `proceedings`, `proceeding_revisions`, `media_assets`, `media_chunks`, `official_documents`, `participants`, `transcript_revisions`, `proceeding_observations`, `topics`, `government_events`, and append-only link/history tables. External source IDs plus authority form deterministic idempotency keys.

Reusing radio capture and incident tables was rejected because talkgroups, voice grants, and emergency lifecycle states do not express dockets, chambers, agendas, motions, votes, or rulings. Object storage, jobs, model adapters, evidence validation, projections, and operational libraries remain shared.

### 3. Treat live capture and official archives as distinct evidence revisions

The scheduler discovers an event and creates a proceeding before media starts. Authorized live streams are segmented into bounded immutable chunks with sequence, source timestamps, overlap, digest, and discontinuity markers. Network interruptions resume from durable checkpoints without inventing missing media. If only an archive is authorized, the proceeding remains pending until that asset appears.

An official archive does not overwrite live chunks. It becomes the canonical media revision after integrity and timing reconciliation, and affected analysis can be reprocessed. This preserves provenance and permits corrections when the live stream was incomplete.

### 4. Keep documents and spoken evidence distinct

Official agendas, dockets, bill text, amendments, roll calls, opinions/orders, transcripts, press releases, and participant rosters are immutable document revisions. They can establish identity, scheduled subject, written text, or official action, but they do not prove that a person spoke a sentence unless the document is an official transcript tied to that segment. Extracted claims record evidence kind and exact source range/page/segment.

### 5. Transcribe long media incrementally and reconcile overlap

Media chunks are normalized privately and transcribed with chunk-relative and proceeding-relative timestamps. Adjacent chunks overlap slightly; a deterministic reconciler removes duplicate boundary text and records discontinuities. Transcript revisions retain model, language, prompt/hints, diarization configuration, media revision, and confidence.

Speaker labels begin as `unknown-N`. Identity is assigned only from an official roster plus reliable turn mapping, an explicit introduction in evidence, or authoritative captions/transcript metadata. Voice resemblance and LLM inference cannot assign identity.

### 6. Encode procedural and legal modality before prose generation

Observations include jurisdiction, body, proceeding type, topic, speaker, statement type, action type, action status, target document, vote numbers when official, epistemic status, confidence, and evidence references. Status values distinguish `questioned`, `argued`, `proposed`, `announced`, `introduced`, `amended`, `moved`, `adopted`, `passed_one_chamber`, `ordered`, `signed`, `effective`, `implemented`, `denied`, `withdrawn`, and `unknown` where applicable.

An LLM may extract schema-constrained observations but cannot upgrade status. Deterministic validation rejects unsupported identity, vote totals, quotations, legal outcomes, dates, and stronger certainty.

### 7. Correlate three levels of state

A proceeding is one scheduled government session. Topics group related segments within and across a proceeding. Government events represent durable developments such as a bill action, court case development, Council oversight issue, or mayoral policy announcement. Matching uses official identifiers first, then normalized document/case/topic references, body, participants, and time. Similar rhetoric alone cannot merge events.

Disagreement, questions, corrections, postponements, revised vote records, and later official actions are append-only. A later archive or document can supersede a value without deleting its provenance.

### 8. Apply source-aware newsworthiness and privacy policy

Default-deny rules admit consequential rulings/orders, recorded votes and adopted motions, material bill actions, budget/oversight developments, and substantive mayoral policy or emergency-management announcements. Routine procedure, repeated remarks, ceremonial content, and unsupported predictions remain private.

Public officials may be named when official metadata or explicit evidence supports identity. Private witnesses and members of the public are unnamed by default. Personal contact information, home addresses, medical information, minors, immigration status, and sensitive testimony are removed or cause suppression. Public availability of a hearing does not waive this editorial policy.

### 9. Publish hourly with source links and status-specific language

Continuous ingestion feeds an hourly watermark after a late-arrival grace period. Approved claims include source authority, official URL, proceeding time, evidence kind, and allowed certainty. Generated stories reference only approved claims. Validation enforces distinctions such as “a justice asked,” “a member argued,” “the House passed,” “the Council committee advanced,” and “the mayor announced.”

The public projection contains no private object key, raw transcript, media URL that bypasses the official page, hidden participant metadata, or model prompt. Failed cycles retain the last known-good projection.

### 10. Make source availability observable

Metrics distinguish no scheduled proceeding, scheduled but not started, source unavailable, media changed, capture gap, archive pending, analysis backlog, and publication suppression. Alerts fire on schedule mismatch, repeated authorization/terms failures, live discontinuities, archive reconciliation failure, and stale proceedings. Source health never equates silence with success.

## Risks / Trade-offs

- **[Official pages use changing third-party players]** → Keep source-specific adapters, contract fixtures, host allowlists, and disable on access-method change.
- **[An official stream is viewable but not authorized for automated capture]** → Record access basis separately; use archive/documents or leave the adapter disabled.
- **[Live media starts late or disappears]** → Discover from schedules, checkpoint chunks, expose gaps, and reconcile against archives.
- **[Long proceedings create high STT cost and latency]** → Incremental chunks, bounded concurrency, silence detection, and source/category priorities.
- **[Speaker diarization misattributes remarks]** → Anonymous labels by default and evidence-based identity assignment only.
- **[Questions or debate are reported as action]** → Typed modality/status, source-specific validators, and zero-tolerance launch tests.
- **[Public testimony exposes sensitive personal data]** → Deterministic sensitive-data policy and private-witness anonymity.
- **[National proceedings overwhelm local coverage]** → Separate jurisdiction beats, configurable quotas, and digest sections.
- **[Archive differs from live capture]** → Immutable media revisions and reprocessing with visible corrections.

## Migration Plan

1. Add proceedings-domain contracts and migrations without changing existing radio tables.
2. Implement the source registry with every adapter disabled by default.
3. Validate official discovery/access methods and terms for each source; enable sources independently.
4. Add proceeding/media/document ingestion and exercise interruption, duplication, archive replacement, and retention.
5. Add chunked STT, speaker handling, document parsing, structured extraction, and adversarial status tests.
6. Add proceeding/topic/event correlation and deterministic replay.
7. Add source-aware policy, grounded generation, and proceedings public views behind private preview.
8. Run at least seven days spanning available source events; sources with no event during the window remain unvalidated and disabled for launch.
9. Enable public projections per validated source. Roll back by disabling its registry entry and the publisher while preserving the last safe projection.

## Open Questions

- Which exact official machine-access methods and reuse terms will be approved for each of the four source families?
- Should national and District stories share one homepage or separate feeds by jurisdiction?
- Should clearly identified expert witnesses be nameable after deterministic policy review, or remain unnamed for MVP?
- What archive wait time should apply before a live-only proceeding is considered incomplete?
- Should short transcript excerpts ever be public, or should MVP output remain paraphrase-only?
