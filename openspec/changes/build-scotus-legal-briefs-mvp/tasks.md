## 1. SCOTUS Case Foundation

- [x] 1.1 Add versioned contracts and enums for Supreme Court cases, dockets, argument sessions, document revisions, transcript pages/lines/turns, legal observations, approved claims, brief revisions, and public case projections.
- [x] 1.2 Add PostgreSQL migrations for cases, consolidated dockets, argument sessions, immutable documents/parses, legal extraction revisions, observations, case history, claims, briefs, and public projections.
- [x] 1.3 Add source-specific fixtures for consolidated cases, reargument, revised transcripts, page/line structure, speaker turns, questions, concessions, citations, lower-court posture, orders, opinions, and sensitive records.
- [x] 1.4 Add typed configuration for Court terms, backfill cap/priority, polling/crawl delay, document bounds, parser version, retention, generation policy, publication, and launch gates.
- [x] 1.5 Make Supreme Court transcript/document processing the only active product path and keep radio, non-Supreme proceedings, audio download, and STT disabled.
- [x] 1.6 Update architecture, configuration, security, and development documentation for the transcript-first “SCOTUS Legal Briefs” product.

## 2. Transcript-First Supreme Court Discovery

- [x] 2.1 Extend the reviewed Supreme Court adapter to emit case, docket, argument-session, full-transcript, docket, order, and opinion descriptors without emitting audio collection jobs.
- [x] 2.2 Implement deterministic case and argument-session identities for normal, consolidated, and reargued dockets.
- [x] 2.3 Implement append-only caption, consolidation, date, URL, transcript-availability, and source metadata revisions.
- [x] 2.4 Queue exactly one transcript collection job only after an approved Court-hosted full transcript PDF is linked.
- [x] 2.5 Keep arguments transcript-pending when only MP3 audio is available and prove no audio object or STT job can be created.
- [x] 2.6 Implement typed docket, transcript, opinion, and order discovery with evidence-kind boundaries and supported docket association.
- [x] 2.7 Implement bounded, checkpointed, lower-priority backfill by configured term and case cap.
- [x] 2.8 Add recorded contract tests for index/detail changes, transcript delay/revision, redirects, consolidated cases, reargument, opinions/orders, duplicates, and backfill limits.

## 3. Official Document Ingestion and Parsing

- [x] 3.1 Add a maintained PDF parsing dependency and pin/test its supply-chain metadata and supported PDF behavior.
- [x] 3.2 Implement authority/source/case/document-scoped private object keys and collection authorization.
- [x] 3.3 Implement no-redirect streaming document retrieval with bounded bytes, SHA-256, durable retries, and spooled temporary storage.
- [x] 3.4 Implement host/path, status, MIME, PDF signature, decodability, encryption, page-count, and document-kind validation.
- [x] 3.5 Implement immutable transcript/docket/order/opinion revisions with idempotent duplicate handling and quarantined digest conflicts.
- [x] 3.6 Enqueue exactly one parse/extract chain per accepted document revision under retries and uncertain acknowledgements.
- [x] 3.7 Implement versioned transcript PDF parsing that preserves file page, printed page, line number/range, reading order, and raw private text.
- [x] 3.8 Implement deterministic header/footer/page-number cleanup while preserving source coordinate resolution.
- [x] 3.9 Implement transcript turn segmentation and official-label speaker/role mapping with anonymous fallback.
- [x] 3.10 Fail closed on ambiguous reading order, missing pages, unsupported encryption, malformed labels, and incomplete transcript extraction.
- [x] 3.11 Implement canonical/superseded document selection and reprocessing when Court-hosted bytes change.
- [x] 3.12 Implement copied-document and extracted-text retention that defers active jobs and preserves digests/provenance.
- [x] 3.13 Add integration tests for interrupted downloads, duplicate/conflicting bytes, false PDF MIME, parser artifacts, page/line mapping, revised transcripts, privacy grants, and retention.

## 4. Evidence-Bound Legal Observation Extraction

- [x] 4.1 Implement the strict legal-observation schema with case/session, evidence kind, page/line range, speaker/role basis, attribution, observation type, legal status, certainty, and confidence.
- [x] 4.2 Implement separate evidence adapters for transcript turns, docket metadata, questions presented, orders, opinions, and other approved Court documents.
- [x] 4.3 Implement bounded context assembly by issue/turn window without sending an uncontrolled full case record to the extraction model.
- [x] 4.4 Implement schema-constrained extraction for procedural posture, question presented, contentions, questions, answers, concessions, disputed premises, authorities, doctrines, requested dispositions, lower-court actions, orders, and holdings.
- [x] 4.5 Implement deterministic quote, citation, docket, date, speaker, page/line, disposition, and evidence-range validation.
- [x] 4.6 Enforce that transcript evidence cannot establish a Supreme Court holding, judgment, vote, or final disposition.
- [x] 4.7 Preserve attribution for advocate claims, disputed facts, lower-court conclusions, and analyst formulations.
- [x] 4.8 Normalize Supreme Court dockets, consolidated cases, U.S. Reports/case citations, statutes, constitutional provisions, rules, courts, advocate roles, and disposition language while preserving raw values.
- [x] 4.9 Implement sensitivity classification/minimization for minors, victims, medical information, sealed/redacted facts, addresses, and unnecessary private names.
- [x] 4.10 Persist immutable extraction revisions with model, prompt/schema, vocabulary, parser, and document versions and support authorized replay/comparison.
- [x] 4.11 Add adversarial tests for hypothetical questions, apparent skepticism, contested facts, concessions, invented precedents, requested reversal, lower-court holdings, sensitive details, and unsupported vote predictions.

## 5. Case Correlation and Legal State

- [x] 5.1 Implement durable case association across docket metadata, consolidated dockets, argument sessions, reargument, transcripts, orders, and opinions.
- [x] 5.2 Implement evidence-backed issue/topic grouping using questions presented, official identifiers, authorities, speakers, and transcript continuity.
- [x] 5.3 Implement append-only case history for docketed, argued, reargued, order-issued, decided, corrected, and unresolved states.
- [x] 5.4 Preserve competing advocate positions, disputed premises, transcript corrections, and superseded document revisions without deleting history.
- [x] 5.5 Link later orders/opinions to the case while preserving argument observations as questions/contentions rather than holdings.
- [x] 5.6 Prevent transcript-only cases from deriving a Court vote, holding, judgment, or final disposition.
- [x] 5.7 Implement versioned deterministic replay and comparison tooling for case, issue, and legal-status state.
- [x] 5.8 Add tests for consolidated cases, similar captions, multiple issues, reargument, revised transcripts, later opinions, corrections, and false finalization.

## 6. Legal Brief Policy and Grounded Generation

- [x] 6.1 Implement eligibility requiring a complete accepted official transcript, valid case identity, sufficient grounded legal observations, and no blocking parser/privacy failure.
- [x] 6.2 Build sanitized approved claims with official URL, evidence kind, page/line range, attribution, legal status, certainty, and source observation IDs.
- [x] 6.3 Implement the structured brief schema for case at a glance, questions presented, procedural posture, positions, justice themes, pivotal exchanges, authorities, analysis, uncertainty, and next steps.
- [x] 6.4 Implement claim-ledger generation in which every factual/legal characterization and quotation references approved claim IDs.
- [x] 6.5 Implement fail-closed validation for unsupported history, citations, quotes, names, dockets, dates, disposition terms, and stronger certainty.
- [x] 6.6 Reject justice-vote/outcome predictions, question-as-holding language, tone/sentiment inference, ideological scoring, and personalized litigation advice.
- [x] 6.7 Implement supported public-actor naming and deterministic minimization/suppression of unnecessary sensitive facts.
- [x] 6.8 Implement official-transcript, post-order, post-opinion, corrected, and retracted maturity/revision states.
- [x] 6.9 Add policy/generation tests for complete briefs, omitted unsupported sections, competing arguments, citation hallucinations, predictions, sensitive cases, legal advice, and valid grounded analysis.

## 7. SCOTUS Legal Briefs Public Site

- [x] 7.1 Add public contracts containing only sanitized case metadata, brief sections, maturity, claim-backed provenance, source links, and revision/correction history.
- [x] 7.2 Implement atomic event-driven case projection activation with last-known-good rollback on generation or validation failure.
- [x] 7.3 Build the “SCOTUS Legal Briefs” homepage, term archive, argument-date archive, case page, topic/status filters, and public-only search.
- [x] 7.4 Add stable term/docket/case/argument URLs and canonical metadata without exposing private IDs or storage paths.
- [x] 7.5 Add official Court detail, docket, transcript, order, and opinion links with descriptive evidence labels.
- [x] 7.6 Display automated, delayed, incomplete, non-authoritative, not-official-record, not-legal-advice, and no-vote/outcome-prediction disclosures on every analysis surface.
- [x] 7.7 Display brief maturity, append-only revisions, visible corrections/retractions, and source update times.
- [x] 7.8 Ensure public roles/endpoints cannot access copied PDFs, full extracted transcripts, parser data, prompts, rejected claims, credentials, or unpublished cases.
- [x] 7.9 Implement semantic headings, keyboard navigation, sufficient contrast, human-readable legal status, responsive layout, and accessible source labels.
- [x] 7.10 Add route/template/public-boundary tests for browsing, search, case provenance, disclosures, accessibility landmarks, corrections, and guessed private identifiers.

## 8. Operations and Deployment

- [x] 8.1 Add discovery freshness, transcript wait age, document download, parser, extraction, correlation, generation, suppression, correction, backlog, and retention metrics/logs without private text.
- [x] 8.2 Add alerts and runbooks for source contract/review changes, transcript delay, redirect/path changes, digest conflicts, PDF parse failure, model backlog, grounding denial, sensitive-data denial, and publication failure.
- [x] 8.3 Update Kubernetes collectors/workers/schedules, service accounts, network policies, secrets, resources, and autoscaling for transcript/document workloads with no audio/STT workload.
- [x] 8.4 Update database roles and private object policies so collector, parser/analyzer, publisher, retention, and public workloads have least privilege.
- [x] 8.5 Extend backup/restore and retention verification for case history, document digests, observations, claims, brief revisions, and public projections.
- [x] 8.6 Add end-to-end tests from official transcript fixture through parsing, legal observations, case state, grounded brief, public projection, revised transcript correction, and failed-cycle rollback.

## 9. Private Validation and Launch

- [x] 9.1 Define measurable discovery, transcript completeness, PDF parsing, page/line provenance, speaker identity, legal-status, citation, issue grouping, sensitivity, grounding, and public-boundary thresholds.
- [x] 9.2 Run fault injection for source/network loss, redirects, changed bytes, malformed/encrypted PDFs, parser restart, duplicate jobs, model retries, and projection failure.
- [x] 9.3 Backfill a bounded representative set including consolidated cases, technical legal terminology, multiple advocates, sensitive facts, and later orders/opinions.
- [x] 9.4 Operate at least seven consecutive private-preview days and manually review every publication candidate plus sampled source documents, parses, observations, claims, and briefs.
- [x] 9.5 Require at least one newly published official transcript during validation when the Court calendar permits; otherwise keep live discovery explicitly unvalidated.
- [x] 9.6 Tune parser, vocabulary, extraction, correlation, privacy, and generation from documented failures and rerun all affected cases.
- [x] 9.7 Verify retention, correction provenance, alerts, backup recovery, kill switches, source disabling, and public/private access boundaries in deployment.
- [x] 9.8 Document measured term coverage, transcript publication latency, known analytical limits, citation methodology, and the distinction from official records/legal advice.
- [x] 9.9 Enable the Supreme source and public site only after all zero-tolerance safety gates pass; preserve the last safe projection on rollback.

## 10. OpenAI and Plain-Language Briefs

- [x] 10.1 Configure the active SCOTUS extraction and brief path to use the official OpenAI API and an explicit OpenAI model.
- [x] 10.2 Rewrite the brief-generation prompt for readers without legal training with everyday headings, short prose, contextual definitions, and no unexplained legalese.
- [x] 10.3 Add deterministic plain-language validation for legalese and sentence/paragraph length while preserving grounding and legal-status controls.
- [x] 10.4 Add tests proving the OpenAI model/configuration, plain-language prompt, readable output acceptance, and legalese/overlong-prose rejection.
- [x] 10.5 Update secrets, configuration, security, and product documentation for OpenAI processing and the general-public audience.
- [x] 10.6 Update and render the fixture-backed preview in the new plain-language voice.

## 11. Whole-Case Citizen Analysis

- [x] 11.1 Add case-level generation/public contracts for a chronological argument-session timeline and a separately grounded analysis of every accepted transcript.
- [x] 11.2 Aggregate all case observations and require every discovered argument/reargument session to have a complete accepted parse before whole-case publication.
- [x] 11.3 Extend the OpenAI claim ledger and schema to identify argument dates/sequences, generate per-session explanations, and compare supported changes across sessions.
- [x] 11.4 Make brief identity, revision numbering, and public projection case-level so one case cannot publish duplicate pages for multiple arguments.
- [x] 11.5 Expand the case page with a citizen-focused full-case guide, chronological argument/reargument breakdowns, official transcript links, and interpretation guardrails.
- [x] 11.6 Add tests for two-session cases, missing historical transcripts, per-session claim isolation, chronological rendering, one-case-one-page projection, and argument-date archive behavior.
- [x] 11.7 Update product/methodology documentation and the fixture preview for whole-case citizen analysis.
