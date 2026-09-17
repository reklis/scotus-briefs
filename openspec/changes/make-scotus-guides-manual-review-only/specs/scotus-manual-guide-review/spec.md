## ADDED Requirements

### Requirement: Schema-valid Guides enter manual review without prose gating
The system SHALL preserve every nonempty final Citizen’s Guide response that contains exactly the planner-required string fields and SHALL NOT reject that response for grounding heuristics, lexical overlap, action-language classification, style, jargon, sentence count, word count, or other automated prose-quality judgments.

#### Scenario: Ordinary-language paraphrase differs lexically
- **WHEN** GPT-OSS returns nonempty strict-schema fields using wording that differs from the approved claims
- **THEN** deterministic assembly creates a private manual-review candidate without classifying the prose as correct, grounded, or publication-ready

#### Scenario: Prose is verbose or awkward
- **WHEN** a strict-schema Guide violates prior sentence, word, jargon, or style preferences
- **THEN** the Guide remains available unchanged for manual review

#### Scenario: Final schema is invalid
- **WHEN** final content is empty, malformed, contains missing or extra fields, or uses non-string field values
- **THEN** generation fails closed without a review candidate

### Requirement: First schema-valid response is not automatically repaired
The system MUST retain the first schema-valid Citizen’s Guide response for review and MUST NOT invoke automated field repair in response to prose content or quality findings.

#### Scenario: Prior grounding heuristic would reject a field
- **WHEN** a Guide would previously have produced `ungrounded_citizens_guide_field`
- **THEN** the first assembled Guide is retained and no `reader_guide_field_repair` request is made

### Requirement: Generated does not mean approved
The system SHALL label every schema-valid Citizen’s Guide as requiring manual review and SHALL NOT infer factual acceptance, improvement, correctness, or publication approval from successful generation.

#### Scenario: Guide generation succeeds
- **WHEN** the model response parses and deterministic assembly completes
- **THEN** sanitized accounting records a generated review candidate with `manual_review_required`

#### Scenario: No reviewer decision exists
- **WHEN** a generated Guide has not received explicit candidate-bound manual approval
- **THEN** promotion and publication remain denied

### Requirement: Operational safety boundaries remain mandatory
The system MUST retain exact local model identity and context verification, source authorization, local-only private evidence handling, request and runtime budgets, final-content-only parsing, reasoning non-retention, transient cleanup, privacy scans, release validation, dry-run enforcement, compare-and-swap protection, and explicit candidate-bound approval.

#### Scenario: Model identity drifts
- **WHEN** the configured tag, digest, or context differs before or after generation
- **THEN** processing fails closed without a review candidate

#### Scenario: Review artifact fails privacy or release validation
- **WHEN** a candidate cannot pass the existing privacy or static release boundary
- **THEN** it is not uploaded, promoted, or published

#### Scenario: Publication remains disabled
- **WHEN** schema-valid Guides are generated for manual review
- **THEN** the active public release remains unchanged until a separately authorized explicit promotion

### Requirement: Deterministic ownership remains outside model control
The system SHALL continue to own case identity, title, headings, field order, claim IDs, action-slot IDs, citations, legal status, source metadata, and publication eligibility deterministically.

#### Scenario: Model returns prose fields
- **WHEN** strict final JSON is assembled into a review candidate
- **THEN** only prose values come from the model and all metadata comes from the planner and trusted source state
