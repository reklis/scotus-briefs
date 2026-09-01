## Context

The repository has a fail-closed official-source registry, a reviewed Supreme Court access basis, parsers for Court-hosted argument indexes/detail pages/transcripts/dockets/opinions/orders, private object storage, durable PostgreSQL jobs, evidence validation, grounded generation, and atomic public projections. The Court links complete official oral-argument transcript PDFs from each argument detail page. Those transcripts are a stronger and simpler MVP source than re-transcribing MP3 audio.

Oral argument is not a decision. Questions can be hypothetical, adversarial, or exploratory; advocates can dispute facts and law; and later opinions can resolve issues differently from argument. The product must provide useful legal analysis without claiming to be an official record, legal advice, or a vote/outcome predictor.

## Goals / Non-Goals

**Goals:**

- Detect newly published full Court transcript PDFs and related official case material.
- Download, validate, privately parse, and version transcript/docket/opinion/order documents.
- Preserve page/line and supported speaker structure as exact evidence provenance.
- Extract evidence-linked legal issues, procedural posture, advocate positions, justice questions, doctrinal themes, concessions, authorities, and next steps.
- Publish accessible, structured case briefs under the working title “SCOTUS Legal Briefs,” written for readers without legal training.
- Use the official OpenAI API for legal observation extraction and public brief generation.
- Correct analysis when revised transcripts, orders, or opinions arrive.

**Non-Goals:**

- Downloading, storing, or transcribing Supreme Court audio in the MVP.
- Republishing complete transcripts, briefs, or opinions.
- Providing legal advice, litigation strategy, ideological scoring, justice sentiment analysis, or factual outcome predictions.
- Treating questions as holdings, oral argument as a vote, an advocate’s assertion as an established fact, or lower-court disposition as the Supreme Court’s judgment.
- Covering lower courts, legislative bodies, local government, radio, or third-party commentary.

## Decisions

### 1. Use complete official transcript PDFs as the primary argument source

The active adapter polls the official term argument index and detail pages. An argument becomes analysis-ready only when a reviewed same-host official transcript PDF is linked and successfully parsed. MP3 links may be retained as official provenance but create no download job. Waiting for the transcript avoids STT errors, chunking cost, speaker diarization, and uncertain audio/transcript reconciliation.

The alternative—download MP3 and run Whisper—was rejected because the Court already supplies the full authoritative transcript and the user does not need audio-derived latency.

### 2. Make the case and argument the durable domain objects

A case is identified by Court term and docket number. One case can have consolidated dockets, multiple argument sessions, reargument, revised transcripts, orders, and an eventual opinion. An argument session owns transcript revisions; evidence and public analysis link to the durable case. Existing generic proceedings tables can be extended with SCOTUS case/argument tables rather than forcing legal analysis into emergency incidents or generic government events.

The public unit is one durable case page, never one competing page per argument session. Before generation, the case-level ledger includes every accepted historical argument transcript in chronological order. The brief gives each session its own plain-language breakdown and explains what later argument added, changed, or revisited without erasing earlier history.

Deterministic IDs derive from normalized docket, term, argument date/session, and official external identifiers. Captions and consolidation are revisioned because official metadata can change.

### 3. Ingest Court documents through a bounded private path

The collector performs a no-redirect HTTPS GET with an approved user agent, response-size bound, MIME and PDF-signature checks, streaming SHA-256, and a spooled temporary file. Accepted object keys are authority/source/case/document scoped. Idempotency uses official document identity, revision, and digest; conflicting bytes under one identity are quarantined. Exactly one parse/extraction chain is queued for an accepted revision.

Transcript, docket, order, and opinion revisions remain distinct. A docket page cannot prove spoken wording; a transcript cannot establish a later holding; and oral argument cannot establish final disposition.

### 4. Parse transcripts with page/line provenance

A versioned PDF parser extracts page text while preserving original page number, printed transcript page when available, line boundaries, speaker labels, and reading order. Headers, footers, page numbers, and repeated artifacts are removed deterministically without changing source coordinates. Parse failures or uncertain ordering fail closed.

Court transcript labels are authoritative for the labeled turn. Speaker turns begin anonymous and receive a justice/advocate identity only from official transcript labels, official argument metadata, or explicit introductions. Voice recognition is unnecessary and prohibited in this MVP.

### 5. Model transcript and document revisions immutably

A newly posted or changed transcript is a separate revision, not an overwrite. Parsed blocks record document digest, parser/version, page/line range, raw private text, normalized private text, speaker label, supported identity, and parse confidence. A canonical revision can supersede an earlier one while preserving evidence and triggering explicit analysis comparison/correction.

### 6. Extract legal observations before generating analysis

Schema-constrained extraction emits typed observations for case background, procedural posture, question presented, advocate contention, justice question, answer, concession, disputed premise, cited authority, doctrinal theme, requested disposition, lower-court action, order, and holding. Every observation includes document kind, exact page/line/text range, speaker identity basis, attribution, epistemic status, and confidence.

Deterministic validators reject fabricated quotes/citations, unsupported speaker names, ungrounded procedural history, and status upgrades. A question presented must come from an official Court/docket source or be labeled as an analyst’s evidence-bound formulation, never silently invented.

### 7. Generate plain-language briefs from an approved claim ledger with OpenAI

The official OpenAI API receives only sanitized approved claims, not an uncontrolled full case record. The configured model must be an approved OpenAI model; local or OpenAI-compatible substitute endpoints are not the active SCOTUS path. The case-level output schema contains: what the case is about, how it reached the Court, the main question, what each side wants, how each side reasons from the law, what the justices tested, points of agreement/disagreement, why the dispute matters, uncertainty, and what happens next. It also contains one chronological analysis for every accepted argument or reargument transcript and a comparison of what changed across sessions. Each factual or legal characterization cites claim IDs resolving to official source links and page/line ranges.

The intended reader has no legal training. The prompt requires direct everyday language, short sentences and paragraphs, active voice, concrete explanations, and definitions for unavoidable legal concepts. Headings such as “What this case is about” and “Why it matters” are preferred over lawyer-facing labels. A deterministic validator rejects unexplained legalese, overlong sentences/paragraphs, quotations, unsupported names/dockets/citations/dispositions, and certainty upgrades. Prose cannot infer a justice’s vote, describe a question as a holding, or say the Court decided a case before an official opinion/order supports it.

### 8. Publish case-oriented revisions

Polling is periodic, but publication is event-driven after a complete safe analysis revision. A brief moves through `official_transcript`, `post_order`, `post_opinion`, `corrected`, or `retracted` states. One stable case URL retains append-only revisions and visible correction notes.

The site provides term, argument date, docket, status, and topic browsing/search. One case page displays the complete case overview, chronological argument/reargument timeline, per-session analysis, cross-session comparison, case history, current status, and official Court detail/transcript/docket/opinion/order links. Public projections contain only sanitized analysis and provenance—never object keys, complete extracted transcript text, prompts, or private parser data.

### 9. Apply legal-analysis and sensitivity safeguards

The site prominently states that summaries are automated, incomplete, non-authoritative, and not legal advice. It attributes advocate claims and disputed facts. Case captions and public legal actors may be named from official metadata, but analysis minimizes unnecessary names/details concerning minors, victims, medical matters, sealed/redacted information, and other sensitive records.

### 10. Backfill conservatively

Initial backfill is limited by configured terms and case count. Every item uses the same source authorization, document ingestion, analysis, and publication path as new material. Backfill is rate-limited and lower priority than newly published transcripts. No public launch occurs until representative cases pass private validation.

## Risks / Trade-offs

- **[Court PDF or page patterns change]** → Contract fixtures, host/path allowlists, no redirects, and automatic `review_required` health.
- **[Transcript appears hours after argument]** → Accept delayed publication; do not use an audio fallback.
- **[PDF extraction loses page/line structure]** → Parser fixtures, rendered-text comparison, coordinate checks, and fail-closed parsing.
- **[Questions are interpreted as votes or holdings]** → Typed legal status, forbidden-language validation, adversarial fixtures, and zero-tolerance launch threshold.
- **[LLM produces plausible but nonexistent citations]** → Citation allowlist from official evidence and exact validator matching.
- **[Consolidated cases or reargument duplicate content]** → Separate case, docket, and argument-session identities with explicit links.
- **[Analysis sounds like personalized legal advice]** → General case-analysis format, no user-specific recommendations, disclaimers, and policy rejection.
- **[Sensitive public facts are amplified]** → Deterministic minimization/suppression and editorial review during private preview.
- **[Historical backfill overwhelms parsing/model capacity]** → Configured term/case cap, low-priority queue, and bounded concurrency.

## Migration Plan

1. Keep the prior multi-source change paused and all non-Supreme sources disabled.
2. Add SCOTUS case/argument/document contracts, migrations, configuration, and fixtures while reusing the source registry and adapter.
3. Implement transcript/document download, integrity, private storage, PDF parsing, revisioning, and retention.
4. Add speaker-turn parsing, legal observation extraction, case correlation, and adversarial legal-status validation.
5. Add approved claims, structured brief generation, and the case/term public site behind private preview.
6. Backfill a small representative case set, then validate at least seven consecutive days and at least one newly posted transcript if the Court calendar permits.
7. Enable the Supreme source and public projection independently. Roll back by disabling collection/publication and retaining the last known-good projection.

## Open Questions

- Final public name and domain: “SCOTUS Legal Briefs” is the working title.
- Which terms and maximum number of historical arguments to backfill initially.
- Whether short transcript quotations are permitted publicly or MVP remains paraphrase-first.
- Whether post-opinion revisions belong on the same argument brief or a separate decision-analysis page.
