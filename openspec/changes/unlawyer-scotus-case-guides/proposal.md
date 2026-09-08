## Why

The 100-case plain-language dry run completed only 62 attempts in five hours, rewrote no existing brief, and still admitted one new page containing courtroom shorthand and unsupported future predictions. Prompt expansion and a term denylist are not enough: the writer needs a smaller, reader-oriented task, targeted correction, a complete prose-quality gate, and a bounded migration that does not mark the entire legacy corpus pending.

## What Changes

- Replace whole-ledger brief writing with deterministic section planning and compact, role-aware evidence packets containing only the claims needed for each reader-facing section.
- Make the LLM's explicit job translation into everyday language while deterministic code retains responsibility for facts, legal status, actors, chronology, citations, and publication eligibility.
- Repair only rejected paragraphs during the same private run, using the rejected text and specific ephemeral guidance rather than regenerating an entire brief from a generic code.
- Add a versioned reader-prose gate for unexplained legal terminology, procedural/model jargon, unsupported absence statements, future predictions, section relevance, and readability.
- Require an opinion-aware rewrite when official activity changes a case from argued to ordered or decided; metadata-only updates may not leave prospective argument prose on a decided page.
- Replace all-at-once legacy migration with a resumable newest-first editorial backfill cursor that selects only the configured batch and leaves unselected cases out of pending state.
- Establish a 10-case canary and measured promotion thresholds before increasing the backfill to 25 and then 100 cases. Failed candidates remain private and the live release remains unchanged.

## Capabilities

### New Capabilities
- `reader-first-case-rewriting`: Plans, writes, validates, repairs, and incrementally backfills grounded Supreme Court case guides for readers without legal training.

### Modified Capabilities

None. The repository currently has no archived main specifications; this capability supersedes the prompt-only behavior described by completed SCOTUS change artifacts without altering public URLs or sanitized JSON contracts.

## Impact

The change affects SCOTUS brief planning and generation, action/grounding and reader-prose validation, official-disposition handling, processor fingerprints, pending-work and migration state, local-model budgets, static publication gates, workflow inputs, operations documentation, and regression/canary fixtures. Public case URLs and allowlisted public schemas remain compatible. Official documents, parsed text, prompts, rejected drafts, and model responses remain private and transient.
