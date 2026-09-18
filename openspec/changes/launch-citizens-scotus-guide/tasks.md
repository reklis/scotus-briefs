## 1. Repository and Toolchain Setup

- [x] 1.1 Record the current `reklis/scotus-briefs` default-branch commit and preserve it as a recoverable legacy reference before replacing repository contents
- [x] 1.2 Remove the obsolete `dev` and generated-content branch protection requirements so the MVP workflow can commit directly
- [x] 1.3 Establish the clean repository layout for `documents`, `manifests`, `data/cases`, `data/guides`, Python pipeline code, SvelteKit site code, schemas, prompts, tests, and generated reports
- [x] 1.4 Configure a locked Python project and developer commands for formatting, linting, type checking, tests, ingestion, generation, and backfill
- [x] 1.5 Scaffold a TypeScript SvelteKit project using `adapter-static` with locked dependencies and commands for formatting, checking, testing, and building
- [x] 1.6 Add repository documentation covering local prerequisites, the ARM64 runner, Ollama configuration, directory ownership, common commands, and the AI/not-legal-advice policy

## 2. Shared Data Contracts

- [x] 2.1 Define and test the document-manifest schema, including source identity, hash, size, type, retrieval metadata, case associations, and supersession
- [x] 2.2 Define and test the normalized case schema, including stable ID, title, term, multiple docket numbers, aliases, lifecycle, dates, parties, metadata provenance, and document references
- [x] 2.3 Define and test the evidence schema with document hash, page range, evidence kind, attribution, extracted text, and confidence/status fields
- [x] 2.4 Define and test the citizen-guide schema with section completeness, material claim citations, glossary entries, validation state, and generation provenance
- [x] 2.5 Generate or maintain TypeScript types compatible with the canonical schemas and add cross-language fixture validation
- [x] 2.6 Implement docket normalization, application-docket handling, consolidated docket representation, stable case slugs, and numeric docket sort keys with unit tests

## 3. Immutable Document Archive

- [x] 3.1 Implement streamed PDF download and validation for response status, nonempty content, PDF signature, size, and SHA-256
- [x] 3.2 Implement content-addressed document placement and deduplication without modifying previously accepted blobs
- [x] 3.3 Implement atomic manifest updates with source provenance, case associations, and replacement/supersession relationships
- [x] 3.4 Implement idempotent reconciliation that preserves prior records when a source fails or returns an unexpectedly empty result
- [x] 3.5 Implement an existing-corpus importer that verifies `manifest.jsonl`, maps PDFs into the content-addressed archive, and reports missing or mismatched files
- [x] 3.6 Implement unresolved historical-group records so ambiguous UUID groups are preserved without fabricated docket metadata
- [x] 3.7 Add archive integrity commands and tests that verify every manifest hash/path pair and detect unreferenced or missing documents

## 4. Official Source Discovery and Case Normalization

- [x] 4.1 Document and fixture the authoritative Supreme Court source pages or feeds selected for current case, docket, transcript, opinion, order, and available brief discovery
- [x] 4.2 Implement the current-term case and document discovery adapter with polite request behavior, retry limits, and deterministic fixture-based tests
- [x] 4.3 Normalize discovered title, term, docket numbers, dates, document classes, and official URLs into case and document candidates
- [x] 4.4 Detect revised content at a previously known URL and add a new document revision while retaining the old hash
- [x] 4.5 Associate consolidated or shared documents with all supported dockets without duplicating PDF blobs
- [x] 4.6 Add discovery failure reports and changed-case output so only affected cases proceed to generation

## 5. PDF Extraction and Evidence Pipeline

- [x] 5.1 Implement page-aware embedded-text extraction that retains PDF hash and source page numbers
- [x] 5.2 Add extraction quality checks and a configured OCR-or-unavailable fallback for pages without usable text
- [x] 5.3 Implement document and opinion-part classification for transcripts, party submissions, orders, majority/plurality opinions, concurrences, and dissents
- [x] 5.4 Implement deterministic page-aware chunking with token budgets below the 32,768-token Ollama context limit
- [x] 5.5 Build evidence-extraction prompts that treat source text as untrusted data, require attribution, prohibit outside facts, and return schema-constrained records
- [x] 5.6 Implement the Ollama client for `ragchew-gpt-oss:120b-32k`, including health checks, model digest capture, timeouts, retries, response parsing, and configurable generation parameters
- [x] 5.7 Implement resumable per-document evidence generation with durable status and failure reports
- [x] 5.8 Add tests using the curated fixtures for page citations, party attribution, opinion-part distinction, chunk boundaries, malformed model output, and unavailable pages

## 6. Citizen Guide Synthesis and Verification

- [x] 6.1 Create versioned synthesis prompts for overview, background/question, party positions, oral argument, decision, why-it-matters, glossary, and source sections
- [x] 6.2 Implement case-level synthesis from cited evidence records without passing an unbounded raw case corpus to the model
- [x] 6.3 Implement lifecycle rules that prevent undecided cases from receiving a holding and mark unavailable sections as pending or source-limited
- [x] 6.4 Implement deterministic checks for schema validity, citation existence and page ranges, document currency, required attribution, and case lifecycle consistency
- [x] 6.5 Implement an adversarial model-verification pass that evaluates support, attribution, majority/dissent distinctions, oral-argument characterization, and overstatement
- [x] 6.6 Accept only verified guide candidates, retain the prior accepted guide after rejection, and emit actionable validation reports
- [x] 6.7 Record source hashes, model name and digest, prompts and schema versions, extractor version, parameters, timestamp, and validation result in every accepted guide
- [x] 6.8 Evaluate the curated cases for factual accuracy, neutrality, readability, completeness, traceability, and restraint; document the initial acceptance thresholds

## 7. SvelteKit Citizen Site

- [x] 7.1 Implement build-time loading and validation of normalized cases and accepted guides without reading or copying PDF bytes
- [x] 7.2 Build the responsive site shell, navigation, civic-purpose explanation, AI-generation disclosure, independence statement, and not-legal-advice notice
- [x] 7.3 Build the home page with current/latest-term orientation, recently updated cases, status explanations, and entry points to search and browse
- [x] 7.4 Build prerendered term browse pages with parsed docket ordering, lifecycle labels, consolidated docket display, and navigation among terms
- [x] 7.5 Build prerendered case pages with layered overview, question/background, each side's arguments, oral argument, decision, significance, glossary, and pending/source-limited states
- [x] 7.6 Build reusable source citations and document lists that show page references and link to both official URLs and immutable GitHub archive copies
- [x] 7.7 Add a generated client-side search index covering titles, aliases, dockets, questions, and accepted guide text with title/docket ranking
- [x] 7.8 Implement the search interface, highlighted result context, empty state, and links back to term browsing
- [x] 7.9 Add metadata, canonical URLs, sitemap, robots configuration, custom-domain support, and a useful static 404 page
- [x] 7.10 Add accessibility, responsive-layout, route-prerender, broken-link, data-state, and search behavior tests

## 8. Backfill, Workflow, and Deployment

- [x] 8.1 Implement the generation orchestrator with explicit incremental, bounded-backfill, case-regeneration, and validation-only modes
- [x] 8.2 Implement durable backfill checkpoints and batching so interrupted runs resume without reprocessing accepted cases
- [x] 8.3 Add commit automation that avoids empty commits and preserves downloaded sources even when later generation or deployment fails
- [x] 8.4 Replace legacy workflows with a manually dispatched and nightly scheduled workflow targeting `[self-hosted, spark]` with minimal write and Pages permissions
- [x] 8.5 Add publication concurrency control and ensure no public pull-request event executes repository code on the self-hosted runner
- [x] 8.6 Configure the workflow to validate data, test and build SvelteKit, assert that PDFs are absent from and size-limit the Pages artifact, then deploy through GitHub Pages
- [x] 8.7 Add non-destructive error handling so failed extraction, Ollama, guide validation, build, or deployment retains the last accepted guide and last successful Pages site
- [x] 8.8 Add workflow summaries and retained failure reports for discovery, archive import, extraction, generation, validation, build, and deployment outcomes

## 9. Initial Publication

- [x] 9.1 Import the existing PDFs and checksum manifest into the immutable document archive in Git-manageable commits
- [x] 9.2 Normalize the curated fixture cases, including standard, consolidated if present, pending, decided, and application-docket examples
- [x] 9.3 Generate, inspect, and accept the first citizen guides through the complete extraction, synthesis, citation, and verification pipeline
- [x] 9.4 Build the complete site from a fresh checkout and verify that archived PDFs permit regeneration while remaining absent from the static artifact
- [ ] 9.5 Deploy the initial representative catalog to `https://scotusbriefs.us/` and verify custom-domain routing, search, browsing, source links, mobile layout, and accessibility smoke tests
- [ ] 9.6 Run and verify one resumable historical backfill batch and one no-change incremental nightly simulation
- [ ] 9.7 Enable the nightly schedule and document launch status, known source-coverage limitations, recovery procedures, and next-backfill operation
