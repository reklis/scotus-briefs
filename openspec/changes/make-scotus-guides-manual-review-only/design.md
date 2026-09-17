## Context

The Citizen’s Guide writer already receives planner-isolated evidence packets and returns strict final JSON. Deterministic code owns public identity, headings, order, claim IDs, action slots, citations, status, and publication eligibility. After assembly, however, several lexical, action-language, style, sentence, jargon, and word-count validators can reject a schema-valid Guide before a person can inspect it. The latest three-case probe produced valid structured responses for every case but retained no reviewable Guide because every response failed heuristic grounding and bounded repairs also failed.

The owner has chosen a manual-review-only model: automated generation establishes transport and structural validity, while a person decides factual correctness and editorial quality. Private evidence must remain local and publication must remain disabled until explicit candidate-bound approval.

## Goals / Non-Goals

**Goals:**

- Preserve every nonempty strict-schema Citizen’s Guide as a private review candidate without automated prose acceptance or repair.
- Keep deterministic metadata and field boundaries outside model control.
- Keep exact model identity, source authorization, privacy, cleanup, release integrity, and publication authorization controls unchanged.
- Make it impossible to confuse successful schema generation with factual or editorial approval.
- Let reviewers inspect the model’s first answer rather than validator-shaped rewrites.

**Non-Goals:**

- Removing strict JSON parsing, expected-field checks, final-content-only handling, output-token limits, or exact model verification.
- Allowing hosted inference, mutable model substitution, source bypass, private-data leakage, automatic publication, or automatic promotion.
- Claiming that a generated Guide is grounded, correct, improved, or publication-ready before manual review.
- Retaining model reasoning, prompts, source text, or rejected/intermediate responses.

## Decisions

### Treat schema-valid prose as a review candidate

Citizen’s Guide generation will require nonempty final JSON with exactly the planner’s expected string fields. Deterministic assembly will continue to supply title, headings, order, claim IDs, action-slot bindings, status, and source metadata. Once assembly succeeds, prose will not be rejected for lexical overlap, grounding heuristics, actor/action phrasing, style, jargon, sentence count, total words, or similar content judgments.

Alternative: lower individual thresholds. Rejected because threshold tuning keeps heuristics in the role of factual gate and continues to hide potentially useful model output.

### Remove automated Guide repair

The live Guide path will make one bounded writer request and retain that first schema-valid result for review. It will not invoke targeted repair based on prose validators. This prevents repair prompts from optimizing text toward lexical rules and makes manual comparisons reproducible.

Alternative: retain repair as an optional reviewer aid. Rejected for this change because the owner requested removal of automated prose gates and wants to see the model’s unmodified answer.

### Keep operational and publication gates separate

Exact model tag/digest/context checks, local-only endpoint restrictions, source authorization, request and run budgets, transient workspace cleanup, final-content-only parsing, privacy scanning, static release validation, compare-and-swap protection, dry-run enforcement, and explicit candidate-bound approval remain mandatory. These controls do not judge prose quality; they protect evidence, runtime identity, and publication integrity.

### Make manual-review status explicit

A successfully assembled Guide is `generated_for_review`, not factually accepted. Existing aggregate schemas may retain an `accepted_count` field for compatibility, but documentation and UI/reporting must describe it as schema/processing acceptance only. Every generated candidate carries a deterministic `manual_review_required` warning until an explicit review record exists. Qualification and promotion cannot infer correctness from generation success.

### Preserve private lifecycle rules

Only final schema content needed for the private review candidate may be retained in the candidate workspace. Reasoning, prompts, source text, transport bodies, and superseded responses remain transient. Any artifact leaving the protected host must still pass privacy and release scans.

## Risks / Trade-offs

- **[Unsupported or role-confused prose reaches reviewers]** → Keep evidence packets and deterministic metadata, label every Guide unreviewed, and require explicit human review before approval.
- **[Legacy `accepted_count` is mistaken for factual acceptance]** → Add an explicit manual-review-required marker and update reports/tests/documentation.
- **[Removing word and style gates produces poor prose]** → Preserve prompt instructions and let reviewers judge actual output rather than silently discarding it.
- **[A privacy-sensitive phrase appears in model output]** → Keep private workspace permissions and boundary privacy scans; do not upload or publish a failing artifact.
- **[A later code path reintroduces validation]** → Centralize manual-review semantics in the Citizen’s Guide generation path and add tests proving prose validators and repair are not invoked.

## Migration Plan

1. Add manual-review-only Citizen’s Guide contracts and tests.
2. Remove prose-bound checks from strict Guide response assembly while retaining field/type/final-content checks.
3. Bypass post-assembly prose validation and targeted repair for Citizen’s Guides only.
4. Mark generated Guides as requiring manual review in sanitized canary accounting.
5. Run focused and full tests plus privacy, release, repository, typing, and OpenSpec checks.
6. Run publication-disabled samples and expose schema-valid first responses for manual inspection.

Rollback restores the prior Guide validation and repair calls. Publication remains disabled throughout, so rollback does not modify an active release.
