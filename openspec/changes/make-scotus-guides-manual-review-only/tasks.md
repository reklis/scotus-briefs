## 1. Manual-review Guide contracts

- [x] 1.1 Remove sentence, word-count, jargon, grounding, action-language, and style acceptance from strict Citizen’s Guide response assembly while retaining expected-field, string-type, and nonempty final-content checks.
- [x] 1.2 Remove automated Citizen’s Guide validation and targeted repair from live processing so the first schema-valid assembled response becomes the review candidate.
- [x] 1.3 Keep deterministic Guide metadata assembly and operational privacy, source, model-identity, cleanup, dry-run, release, and explicit-approval gates unchanged.

## 2. Review-state accounting

- [x] 2.1 Mark schema-valid generated Guides with a sanitized `manual_review_required` status or warning that cannot be interpreted as factual approval.
- [x] 2.2 Ensure promotion and publication remain denied without an explicit candidate-bound reviewer decision.

## 3. Regression coverage and documentation

- [x] 3.1 Add tests proving lexically novel, verbose, jargon-heavy, role-confused, and otherwise poor schema-valid prose remains available for manual review without a repair call.
- [x] 3.2 Preserve tests for invalid schema, empty final content, deterministic metadata, model identity, privacy, cleanup, release validation, publication-disabled operation, and explicit approval.
- [x] 3.3 Update operations, security, and model documentation to distinguish generated review candidates from manually approved Guides.
- [x] 3.4 Run focused tests, full tests, lint, typing, strict OpenSpec validation, workflow checks, and repository/privacy policy checks.

## 4. Publication-disabled sample

- [x] 4.1 Run a small exact-model publication-disabled sample and expose first schema-valid Guides for manual inspection without retaining reasoning, prompts, source text, or intermediate responses.
