## Why

The 100-case plain-language dry run completed only 62 attempts in five hours, rewrote no existing brief, and still admitted one new page containing courtroom shorthand and unsupported future predictions. After compact planning and targeted repair were added, protected ten-case canary `34471623129` accepted no rewrite because factual safeguards and editorial preferences were enforced as one fatal gate. The writer needs a smaller reader-oriented task, correction that cannot damage a valid original, a two-tier correctness/editorial policy, and a bounded measurable migration.

## What Changes

- Replace whole-ledger brief writing with deterministic section planning and compact, role-aware evidence packets containing only the claims needed for each reader-facing section.
- Make the LLM's explicit job translation into everyday language while deterministic code retains responsibility for facts, legal status, actors, chronology, citations, and publication eligibility.
- Repair only rejected or warned paragraphs during the same private run, using the rejected text and specific ephemeral guidance rather than regenerating an entire brief from a generic code; discard a failed style repair instead of losing its hard-valid original.
- Split the versioned reader-prose policy into hard correctness/privacy/source failures and fixed editorial warnings. Preserve strict rejection for unsupported meaning and severe bounds while retaining hard-valid prose with ordinary readability, terminology, repetition, or section-focus warnings for measured review.
- Narrow process-language rejection to actual pipeline disclosures so ordinary legal-reporting statements that a party `claims` something do not fail validation.
- Require an opinion-aware rewrite when official activity changes a case from argued to ordered or decided; metadata-only updates may not leave prospective argument prose on a decided page.
- Replace all-at-once legacy migration with a resumable newest-first editorial backfill cursor that selects only the configured batch and leaves unselected cases out of pending state.
- Establish a 10-case canary and measured promotion thresholds before increasing the backfill to 25 and then 100 cases. Retain sanitized failure/warning aggregates even when every case fails hard; private material remains transient and the live release remains unchanged.

## Capabilities

### New Capabilities
- `reader-first-case-rewriting`: Plans, writes, validates, repairs, and incrementally backfills grounded Supreme Court case guides for readers without legal training.

### Modified Capabilities

None. The repository currently has no archived main specifications; this capability supersedes the prompt-only behavior described by completed SCOTUS change artifacts without altering public URLs or sanitized JSON contracts.

## Impact

The change affects SCOTUS brief planning and generation, action/grounding and two-tier reader-prose validation, targeted-repair fallback, official-disposition handling, processor fingerprints, pending-work and migration state, sanitized warning aggregates, local-model budgets, static publication gates, workflow inputs, operations documentation, and regression/canary fixtures. Public case URLs and allowlisted public schemas remain compatible through optional sanitized fields. Official documents, parsed text, prompts, rejected drafts, and model responses remain private and transient.
