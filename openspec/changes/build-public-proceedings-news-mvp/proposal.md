## Why

The available receiver location cannot reliably decode the District's public-safety radio system, while official government proceedings provide substantial public-interest audio, schedules, documents, and authoritative metadata without depending on distant RF reception. The MVP should pivot to evidence-based reporting from approved official sources rather than use unlicensed scanner or platform feeds.

## What Changes

- Replace the active MVP source path with approved official proceedings from U.S. Supreme Court oral arguments, U.S. House floor sessions, DC Council hearings and meetings, and DC mayoral briefings.
- Discover scheduled and active proceedings from official calendars, feeds, APIs, and pages, with archive ingestion when a live endpoint is unavailable.
- Require a source registry recording authority, access method, terms review, expected metadata, polling limits, and enabled state; sources fail closed when access is not explicitly approved.
- Capture or reference official media without publicly redistributing source audio or raw transcripts and without relying on `yt-dlp`, platform scraping, or bypassing access controls.
- Privately transcribe proceedings, identify speakers conservatively, combine audio evidence with official agendas and documents, and extract evidence-linked statements, questions, motions, votes, rulings, and announced actions.
- Correlate segments into durable proceedings and topics, preserving disagreement, uncertainty, corrections, and the difference between proposals, debate, votes, orders, and final government action.
- Publish delayed hourly stories and digests with source links, jurisdiction labels, provenance, and deterministic grounding/privacy checks.
- Permit naming public officials when identity is supported by official metadata or explicit introduction; omit private witnesses' names and sensitive personal testimony by default.
- Keep the HackRF edge implementation available for future authorized deployments, but remove it from the launch-critical path for this MVP.

## Capabilities

### New Capabilities

- `official-source-discovery`: Register, authorize, discover, schedule, and health-check approved official government proceeding sources.
- `proceeding-media-ingestion`: Reliably ingest live or archived official media and documents as immutable, deduplicated private source artifacts.
- `proceeding-analysis`: Transcribe proceedings and derive speaker-aware, evidence-linked government statements and actions without overstating status.
- `government-event-correlation`: Correlate segments, documents, topics, and later actions into durable proceedings and evolving public-policy events.
- `proceedings-news-publishing`: Apply public-interest, privacy, provenance, and grounding policy to publish delayed stories and digests from approved claims.

### Modified Capabilities

None.

## Impact

- Adds source adapters and contracts for Supreme Court, House, DC Council, and mayoral proceedings, plus official calendar/document retrieval.
- Extends PostgreSQL state for source authorization, proceedings, media segments, documents, speakers, topics, and source health.
- Reuses private object storage, durable jobs, STT, evidence validation, append-only correlation, grounded generation, public projections, and Kubernetes operations where their contracts remain applicable.
- Changes extraction vocabulary and publication policy from emergency incidents to government proceedings and public actions.
- Removes Raspberry Pi reception, Trunk Recorder, and receiver-location validation from the new MVP's deployment requirements.
- Requires per-source technical and terms validation before enabling collection; unsupported or platform-only sources remain disabled.
