## Why

Significant DC Fire and EMS incidents are described across many short, fragmented radio transmissions, making them difficult to follow without continuously monitoring the system. A self-operated receiver and conservative automated editorial pipeline can turn available unencrypted traffic into useful hourly local incident reporting without publicly redistributing audio or raw transcripts.

## What Changes

- Add an edge radio appliance that uses one HackRF and one Raspberry Pi to capture selected unencrypted DCFD talkgroups from an initial RF window.
- Forward finalized per-call audio and radio metadata reliably from an outage-tolerant local spool to a Kubernetes-hosted ingestion service.
- Privately transcribe calls and extract evidence-bound observations such as incident type, location, units, dispatch status, escalation, and resolution.
- Correlate observations across talkgroups and time into durable, evolving incidents with confidence and provenance.
- Publish conservative hourly incident stories and digests while suppressing routine medical calls, sensitive personal details, encrypted traffic, and insufficiently confirmed events.
- Keep audio and transcripts private, apply configurable retention, and expose only generated incident reporting on the public site.
- Provide capture and pipeline health telemetry so a broken receiver or processing backlog is distinguishable from a quiet radio period.

## Capabilities

### New Capabilities
- `dcfd-radio-capture`: Capture selected clear DCFD talkgroups as discrete calls on a Raspberry Pi and reliably forward them with metadata and health signals.
- `radio-call-ingestion`: Securely ingest, deduplicate, store, queue, and lifecycle-manage private call audio and metadata in Kubernetes.
- `radio-call-analysis`: Transcribe call audio privately and derive structured, confidence-scored observations without inventing unsupported facts.
- `incident-correlation`: Associate related observations with durable incidents and maintain evidence-backed incident state as new calls arrive.
- `hourly-news-publishing`: Apply newsworthiness and privacy policy, then generate and publish hourly incident stories, updates, digests, and corrections without exposing source audio or transcripts.

### Modified Capabilities

None.

## Impact

- Introduces Raspberry Pi radio-capture configuration and an edge forwarding process using the existing HackRF.
- Adds Kubernetes workloads for ingestion, asynchronous processing, transcription, correlation, scheduling, and public-site generation.
- Requires private S3-compatible object storage, durable event delivery, a relational incident store, and an OpenAI-compatible LLM interface.
- Adds a public read-only news site and private operational telemetry.
- Establishes retention, redaction, provenance, correction, and publication policies for sensitive emergency-radio-derived information.
- Initial coverage is intentionally incomplete: one receiver, selected clear DCFD talkgroups, one RF window, and no MPD or encrypted-channel processing.
