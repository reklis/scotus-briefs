## Context

The project starts without an application implementation. The available capture hardware is one HackRF connected to a Raspberry Pi, while compute-intensive services run in an existing Kubernetes cluster. The District of Columbia radio system spans separated 700 MHz and 800 MHz ranges; one receiver cannot cover both simultaneously. The MVP therefore observes selected unencrypted DCFD talkgroups in one configured RF window and makes no claim of complete coverage.

Radio transmissions are short, noisy observations rather than authoritative incident records. Dispatch information can be corrected, cancelled, or contradicted by on-scene reports. The design must preserve source modality and uncertainty, prevent sensitive medical traffic from reaching the public surface, and ensure an LLM acts as a constrained editor rather than a source of facts.

Source audio and transcripts are private processing material. The public product contains only sanitized, generated incident reporting. MPD, encrypted channels, multi-receiver capture, and third-party radio aggregation APIs are outside the MVP.

## Goals / Non-Goals

**Goals:**

- Capture selected clear DCFD calls as discrete audio files with radio metadata on a resource-constrained edge device.
- Deliver calls at least once to Kubernetes without losing them during temporary network or cluster outages.
- Privately transcribe calls and convert them into provenance-bearing, confidence-scored observations.
- Correlate fragmented calls into evolving incidents while retaining contradictions and corrections.
- Publish conservative hourly stories and digests from approved facts only.
- Enforce privacy, newsworthiness, retention, and public/private data boundaries independently of LLM judgment.
- Make receiver silence distinguishable from receiver or pipeline failure.
- Support replacement of STT and LLM providers without changing capture or public data contracts.

**Non-Goals:**

- Complete coverage of all DCFD frequencies or calls.
- Reception, decryption, or inference of encrypted communications.
- MPD or broad law-enforcement reporting.
- Public audio playback, raw transcripts, or a scanner interface.
- Real-time emergency notification or safety-of-life use.
- Publishing routine medical responses or personally identifying patient information.
- Training or fine-tuning speech or language models on captured content.
- User accounts, comments, subscriptions, or a mobile application.

## Decisions

### 1. Use Trunk Recorder as the edge demodulator and call segmenter

Trunk Recorder supports HackRF through `gr-osmosdr`, Raspberry Pi deployments, P25 Phase I/II, talkgroup filtering, and per-call audio plus metadata. The edge configuration will initially cover one RF window and an allowlist of clear DCFD Dispatch, Main, and incident talkgroups. Encrypted and unselected traffic will not be forwarded.

A continuously mixed audio stream was rejected because it loses talkgroup boundaries, complicates concurrent calls and retries, and wastes transport bandwidth. Scanner-style single-call following was rejected because leaving the control channel increases missed-call risk. The MVP accepts incomplete RF coverage explicitly and measures it before considering a second receiver.

### 2. Keep the Raspberry Pi as an outbound-only, store-and-forward appliance

A small edge forwarding process will watch for finalized call artifacts, create an immutable capture envelope, persist it in a local spool, upload it, and remove it only after cluster acknowledgement. Partial files are never uploaded. Retries use exponential backoff and preserve the same capture identifier.

The capture identifier will be deterministic from receiver identity, system, talkgroup, start time, frequency, and audio digest. This provides idempotency when acknowledgement is lost and a call is uploaded again. The Pi will retain acknowledged files only for a short configurable edge grace period and will apply backpressure rather than silently deleting unacknowledged calls when storage approaches its limit.

The Pi initiates all network connections using TLS and scoped credentials. No public inbound management endpoint is required.

### 3. Upload binary audio to private object storage and queue only metadata

The cluster ingestion API will authorize the receiver, accept a capture manifest, arrange an upload to a private S3-compatible object, validate size and digest, and atomically mark the call ready for analysis. Durable PostgreSQL records will hold call state and analysis jobs; workers will claim jobs transactionally. Audio bytes will not be placed in PostgreSQL or a message broker.

A PostgreSQL-backed job queue is selected for MVP volume and operational simplicity. Kafka or NATS would add infrastructure without a demonstrated throughput need. The call and job tables preserve at-least-once processing and idempotent stage outputs, so a broker can be introduced later without changing the capture contract.

The public web workload has no credentials or route to private audio or transcript storage.

### 4. Define a versioned capture and analysis contract

Each capture envelope includes at least:

- schema version and deterministic capture ID;
- receiver and radio-system identifiers;
- talkgroup ID and configured display name;
- UTC start/end timestamps and duration;
- assigned voice frequency;
- source radio IDs when available;
- encryption and emergency indicators when available;
- audio content type, byte count, and SHA-256 digest;
- decoder quality fields available from the capture software.

Analysis creates immutable transcript revisions and observation records. An observation contains a typed claim, normalized and raw values, confidence, epistemic status, source capture and transcript revision, and supporting time range. Representative claim types include location, incident type, dispatch, arrival, on-scene observation, escalation, cancellation, containment, resolution, unit assignment, injury mention, and privacy sensitivity.

Versioning allows captured calls to be reprocessed with a new model while retaining prior outputs and published-story provenance.

### 5. Separate transcription, extraction, and correlation

An STT adapter will initially target a Kubernetes-hosted Whisper-compatible worker, with the provider selected through configuration. Radio-domain hints include DCFD unit vocabulary, talkgroup context, DC streets, quadrants, and landmarks. The original transcript, normalized transcript, model identity, confidence signals, and processing status remain private.

A structured extraction stage produces observations under a strict schema. It must preserve qualifiers such as “reported,” “dispatched,” “on scene,” and “unable to confirm.” Low-confidence locations and negation-sensitive claims are retained as uncertain rather than promoted to facts.

Correlation first uses deterministic evidence: normalized location, time proximity, assigned incident talkgroup, unit overlap, incident type, and explicit references. An LLM may assist extraction and ambiguity resolution but does not create incident identity without supporting observations. Active incidents remain open across hourly boundaries.

### 6. Model incidents as evidence-backed state, not generated articles

An incident has a durable ID, type, normalized public location, lifecycle state, confidence, sensitivity classification, linked observations, and an append-only change history. Lifecycle states are `candidate`, `corroborating`, `publishable`, `active`, `resolved`, `corrected`, `retracted`, and `suppressed`.

Contradictory observations do not overwrite history. Later, stronger evidence can downgrade a reported event, close it without publication, or generate a public correction. Dispatch reports and on-scene confirmation remain distinct evidence levels.

Time buckets are not incident boundaries. Correlation considers active incidents and a configurable lookback window so a multi-hour fire receives updates to one story.

### 7. Make publication eligibility deterministic and conservative

Before prose generation, a policy engine creates a sanitized set of approved public claims. The MVP allowlist covers confirmed or strongly corroborated structure fires, rescues, hazmat responses, major collisions or entrapments, and escalated/multi-alarm incidents. Routine medical calls and sensitive behavioral-health, suicide, overdose, juvenile, patient, named-person, and exact-unit-address details are suppressed or generalized.

A publishable incident requires sufficient evidence for its category, not merely a dramatic transcript. On-scene observations have more evidentiary weight than initial caller reports. Unconfirmed events cancelled within the publication window remain private.

The LLM receives only approved claims and their allowed modality, and returns structured title, summary, timeline entries, and referenced claim IDs. A validator rejects output that lacks claim support, violates redaction rules, asserts stronger certainty than allowed, or introduces names, casualty counts, causes, or outcomes absent from approved facts.

### 8. Publish on an hourly editorial watermark

Capture and analysis run continuously. An hourly Kubernetes-scheduled job waits through a configurable late-arrival grace period, evaluates changed incidents, generates validated revisions, and atomically advances the public projection. Active incidents may be updated in later cycles, and resolved incidents retain the same public story ID.

The public read model contains sanitized story text, approximate/public location, incident category, status, first-reported and updated times, and revision history. It contains no raw transcript, audio URI, private object key, source radio ID, or sensitive observation.

The site will clearly state that reports are automatically generated from public DCFD radio communications, may be delayed or incomplete, and are not an emergency service.

### 9. Retain private evidence briefly and maintain auditable derived history

MVP defaults are 24 hours for cluster audio and 30 days for private transcript revisions, both configurable. Capture manifests, observation provenance, model/version metadata, incident history, and public revisions are retained longer for deduplication and correction audits. Lifecycle deletion must include failed and orphaned uploads.

Audio retention was not set to zero because short-term replay is needed to diagnose transcription errors during private validation. Indefinite audio retention was rejected because it increases privacy and storage exposure without serving the public product.

### 10. Treat observability as part of capture correctness

The edge sends periodic heartbeats containing control-channel decode activity, last finalized and acknowledged calls, spool depth and age, free disk, dropped samples when available, clock offset, CPU temperature, and software/configuration version. Cluster metrics cover ingestion lag, job failures, transcription latency, unpublished candidates, policy suppressions, and hourly publication outcomes.

A quiet voice channel is healthy only when control-channel and heartbeat signals remain current. Alerts distinguish receiver failure, RF/decode degradation, uploader backlog, analysis backlog, and publication failure.

## Risks / Trade-offs

- **[Incomplete single-receiver coverage]** → Publish an explicit coverage disclaimer, measure voice-frequency distribution and comparison counts, and defer a second receiver until data demonstrates the need.
- **[Simulcast distortion or HackRF drift causes missing/garbled calls]** → Track decode quality, calibrate frequency, provide cooling, tune antenna placement/gain, and consider a band-specific antenna/filter or TCXO.
- **[Raspberry Pi overload during a major incident]** → Filter talkgroups, bound recorder concurrency, monitor dropped samples and temperature, and keep STT/LLM processing off the Pi.
- **[STT mistakes change locations, negation, or outcomes]** → Use domain vocabulary, confidence thresholds, structured uncertainty, multi-call corroboration, and a private evaluation period.
- **[LLM invents or overstates facts]** → Generate only from approved claims, require claim IDs, validate structured output, and fail closed rather than publishing invalid prose.
- **[Sensitive medical or personal information is exposed]** → Default-deny publication categories, deterministic redaction, private-only transcripts, isolated credentials, and short retention.
- **[Duplicate or out-of-order delivery corrupts incidents]** → Use deterministic IDs, idempotent stage writes, immutable revisions, event timestamps, and transactional job claims.
- **[An incident crosses hours or is later contradicted]** → Maintain durable incidents and append-only public revisions with correction/retraction states.
- **[The site appears authoritative or real-time]** → Display source, automation, delay, incompleteness, and non-emergency disclaimers on every story surface.
- **[Kubernetes or network outage fills the Pi spool]** → Alert on backlog, retry with backoff, reserve disk, and stop capture visibly rather than silently evicting unacknowledged calls.

## Migration Plan

1. Deploy private cluster foundations: PostgreSQL schema, private object storage integration, ingestion API, scoped receiver identity, and operational telemetry.
2. Configure Trunk Recorder and the edge forwarder for one RF window and a narrow DCFD talkgroup allowlist; validate reception without public publishing.
3. Exercise interrupted uploads, duplicate delivery, clock handling, retention, and spool recovery.
4. Deploy STT, structured extraction, and correlation workers; collect a private benchmark set and tune domain vocabulary and confidence thresholds.
5. Run at least seven days in private-preview mode, compare generated incident states with retained source audio and later outcomes, and document failure rates.
6. Enable policy evaluation and generated public previews while keeping the public projection inaccessible.
7. After acceptance criteria are met, expose the read-only site and hourly publisher with conservative category allowlists.
8. Roll back public launch by disabling the publication schedule and serving the last known safe static projection; capture and private analysis can continue independently.

## Open Questions

- Which Raspberry Pi model, available storage, antenna, cooling, and physical receiver location will be used?
- Which S3-compatible service and PostgreSQL deployment already exist in the Kubernetes cluster?
- Which Whisper-compatible runtime and OpenAI-compatible LLM endpoint will be used initially?
- What measured capture completeness and factual-accuracy thresholds must the seven-day private evaluation meet before public launch?
- Should public fire locations remain at block-level or be generalized further for residential incidents?
