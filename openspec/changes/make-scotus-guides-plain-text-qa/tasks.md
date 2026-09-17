## 1. Plain-text Q&A contracts

- [x] 1.1 Add versioned deterministic question IDs, wording, applicability, headings, and order for decided, ordered, and pending cases.
- [x] 1.2 Add question-specific official-source packet and plain-text answer contracts with deterministic source provenance and no model-controlled metadata.
- [x] 1.3 Add a Q&A Guide draft/assembly boundary that does not require legal-observation claims or model-generated IDs.

## 2. Official-material packet construction

- [x] 2.1 Build deterministic question-specific packets from authorized docket, opinion, order, and transcript blocks while distinguishing syllabus, controlling Court text, advocate material, and separate opinions.
- [x] 2.2 Enforce reviewed character, token, document, and source-order bounds without silently treating first-window truncation as complete evidence.
- [x] 2.3 Implement bounded plain-text map-and-synthesize processing for relevant material that cannot fit one request, with transient intermediate answers and finite call/runtime limits.

## 3. Plain-text model transport

- [x] 3.1 Implement the minimal one-question plain-text prompt without `response_format`, observation vocabulary, JSON instructions, action schemas, or repair feedback.
- [x] 3.2 Accept only nonblank final message text, preserve it unchanged apart from surrounding whitespace, and discard reasoning and transport metadata.
- [x] 3.3 Retain exact tag/digest/context verification, zero temperature, bounded low reasoning, output-token ceilings, local-only inference, and opaque cost receipts for every direct and synthesis call.

## 4. Live Guide integration

- [x] 4.1 Generate Citizen’s Guide answers directly from source packets without invoking `LegalExtractionService`, `ReaderGuidePlanner`, strict Guide JSON schema, canonical action validation, or targeted repair.
- [x] 4.2 Assemble deterministic public headings, ordering, official source links, case status, dates, identity, revision metadata, and `manual_review_required` accounting around the plain-text answers.
- [x] 4.3 Update processor fingerprints, request scopes, budget accounting, pending outcomes, and safe diagnostics for the question set, packet builder, direct-answer prompt, and synthesis protocol.
- [x] 4.4 Keep live generation publication-disabled and require an approved report bound to the exact candidate before deployment or promotion.

## 5. Regression and fixture coverage

- [x] 5.1 Add unit tests proving requests contain one simple question plus official text, omit JSON response schemas, and preserve awkward or JSON-looking nonblank answers for manual review.
- [x] 5.2 Add source-packet fixtures for a disposition-only order, full opinion, pending argued case, decided-after-argument case, long document set, syllabus, and separate opinions.
- [x] 5.3 Add integration tests proving incomplete or failed structured extraction cannot block Q&A generation and that pending cases never receive decided-case wording.
- [x] 5.4 Preserve and extend tests for empty final content, reasoning non-retention, exact model identity, context drift, source authorization, budgets, cleanup, privacy, release validation, dry-run enforcement, and candidate-bound approval.

## 6. Documentation and qualification

- [x] 6.1 Update operations, security, configuration, model qualification, and Citizen’s Guide documentation to describe direct plain-text Q&A, deterministic source packets, structural-success accounting, and manual review.
- [x] 6.2 Run focused and full tests, Ruff, mypy, strict OpenSpec validation, workflow checks, repository/privacy policy checks, and diff checks.
- [x] 6.3 Run publication-disabled exact-model samples with GPT-OSS and Qwen against the same recovered official evidence, expose unchanged final answers for human review, and verify workspace/model cleanup without retaining prompts, source text, reasoning, or intermediate answers.
- [x] 6.4 Complete fixture-backed and fixed ten-case publication-disabled qualification before requesting any exact-candidate approval.
