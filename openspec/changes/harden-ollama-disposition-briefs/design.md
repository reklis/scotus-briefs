## Context

The active-term slip-opinion parser now finds current emergency and merits dispositions, but local generation with Ollama `qwen3.8:27b` repeatedly fails after syntactically valid JSON is returned. Disposition prose is generated with a large prompt inherited from argued cases, negative instructions prime absent argument concepts, and paragraph-level action validation compares natural-language verbs against an undifferentiated or final-action-only support set. Production receipts then reject an unchanged request on later nights unless broad manual replay is enabled.

All source documents, extraction text, prompts, observations, and rejected responses must remain ephemeral. Public state may retain only allowlisted Court metadata, projections, fixed validator codes, opaque fingerprints, bounded retry metadata, and receipts. Every publication gate remains fail closed.

## Goals / Non-Goals

**Goals:**
- Make disposition-only prompting small, positive, and structurally distinct from argued-case prompting.
- Keep official identity and Court-action facts deterministic.
- Validate requested, lower-court, and Supreme Court actions against claims of the same role.
- Eliminate false oral-argument failures without accepting invented proceedings.
- Permit finite scheduled retry cycles for model-output failures while preserving auditable no-duplicate accounting.
- Prove the behavior against the three newest emergency cases before backlog drain.

**Non-Goals:**
- Replacing Ollama or exposing it outside the Spark host.
- Weakening privacy, grounding, completeness, integrity, or publication checks.
- Publishing rejected or partial model prose.
- Adding a runtime service or durable private data store.
- Automatically retrying source, parser, policy, privacy, or unknown failures as model failures.

## Decisions

### Use a genuinely separate disposition prompt

The disposition request will use affirmative instructions: write only facts in cited claims, attach every action to its actor, and omit unavailable proceedings. It will not list oral-argument phrases to avoid. Its schema remains strict and fixes `argument_analyses` at zero.

Alternative: retain the shared prompt and add more prohibitions. Rejected because repeated negative instructions are already echoed by the local model and make the contract harder to follow.

### Keep formal facts outside model discretion

Caption normalization, docket, dates, status, official links, and approved action claims remain deterministic. The model may explain those claims but cannot supply authoritative values. Rendering continues to use the official caption if a generated title is generic or noncanonical.

Alternative: require verbatim generated action text. Rejected because it produces brittle prose and still delegates a formal field to a probabilistic component.

### Validate actions per sentence and legal role

Each action-bearing sentence is classified as requested relief, lower-court action, or Supreme Court action from narrow actor markers. Its canonical verb and negation are compared with cited claims carrying `REQUESTED`, `LOWER_COURT_HELD`, or `COURT_HELD`/`COURT_ORDERED`, respectively. Ambiguous unsupported action statements fail closed. This avoids comparing a lower-court verb with the Supreme Court result merely because both appear in one paragraph.

Alternative: union all action support. Rejected because it allows actor swaps. Final-action-only comparison is also rejected because it creates false failures.

### Recognize negation before absent-proceeding detection

A narrow sanitizer removes only explicit forms such as “without oral argument” and “no oral argument occurred” before applying the existing positive-invention detector. Any remaining counsel or justice exchange still fails.

Alternative: prohibit every mention. Rejected because it generates false positives and encourages repetitive corrective failures.

### Add stable retry scopes and finite cycles

A retry scope is an opaque SHA-256 over the case key, current evidence/document digests, processor fingerprint, model, endpoint identity, generation schema, and relevant configuration. It excludes timestamps and run IDs. Pending model-output failures retain only the scope, completed cycle count, last-cycle time, next eligibility time, status, and fixed failure code.

The default policy permits an initial cycle plus two scheduled automatic cycles, at least twenty hours apart, with a separate per-run retry-case limit. Transport attempts remain separately counted by existing receipts and all current global budgets still apply. A changed source or reviewed processor version creates a new scope. Accepted output closes the scope. Unknown and non-model failures are not retryable.

Broad `authorized_replay` remains an owner-only escape hatch during migration but scheduled retries use the narrow scope authorization and never timestamp-salt the underlying request fingerprint.

### Persist retry state through sanitized publication checkpoints

Retry state travels with `PendingWork`; no raw response or prompt is retained. Case-processing failures communicate only typed stage/category and fixed validator code to the orchestrator. The workflow enables automatic retry only for scheduled nightly mode and persists receipts before checkpoint or release promotion.

## Risks / Trade-offs

- [Natural language may remain unreliable] → Reduce model responsibility, retain strict role-aware checks, and keep failures case-local.
- [Sentence role classification can be ambiguous] → Recognize only narrow markers and fail closed on unmatched action-bearing sentences.
- [Automatic retries consume Spark time with deterministic output] → Use cooldowns, two-cycle lifetime bounds, per-run quotas, and close/reset scopes only on reviewed changes.
- [Contract migration could invalidate old state] → Add optional retry fields with strict defaults; legacy pending entries become fresh under the new prompt/policy scope rather than receiving implicit broad replay.
- [Crash before receipt persistence can repeat work] → Preserve existing per-run limits and serialized workflow; without a transactional private service exactly-once transport cannot be guaranteed.

## Migration Plan

1. Add tests for role-aware actions, negated/positive oral-argument language, stable retry scopes, cooldown, and exhaustion.
2. Add backward-compatible retry state and configuration defaults.
3. deploy the new prompt, validator, policy, and scope fingerprints with publication disabled in a reduced three-case run.
4. Inspect sanitized outputs and retained candidate; deploy only if all gates pass.
5. Run the normal nightly queue with bounded automatic retries and monitor fixed-code outcomes.
6. Roll back source code if needed; prior public release and compatible pending state remain valid.

## Open Questions

- If the 27B model still cannot satisfy the reduced contract, evaluate a reviewed larger local instruction-following model without changing the public or privacy architecture.
