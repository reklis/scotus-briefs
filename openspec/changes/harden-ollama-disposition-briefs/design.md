## Context

The active-term slip-opinion parser now finds current emergency and merits dispositions, but local generation with Ollama `qwen3.8:27b` repeatedly fails after syntactically valid JSON is returned. Disposition prose is generated with a large prompt inherited from argued cases, negative instructions prime absent argument concepts, and paragraph-level action validation compares natural-language verbs against an undifferentiated or final-action-only support set. Production receipts then reject an unchanged request on later nights unless broad manual replay is enabled.

All source documents, extraction text, prompts, observations, and rejected responses must remain ephemeral. Public state may retain only allowlisted Court metadata, projections, fixed validator codes, opaque fingerprints, bounded retry metadata, and receipts. Every publication gate remains fail closed.

## Goals / Non-Goals

**Goals:**
- Limit disposition-only Ollama work to bounded structured observation extraction.
- Compile public disposition briefs from deterministic identity and approved source-exact claims.
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

### Remove generative disposition prose from production

Two reduced-prompt live probes still produced unsupported or actorless action prose for all three representative emergency matters. Production therefore uses Ollama only for structured, evidence-quote-validated observation extraction. A deterministic compiler selects the approved docket and Supreme Court action claims, emits a source-exact action block, adds only independently validated background claims that fit public language bounds, and fixes argument analyses at zero. The standalone compact disposition prompt remains regression-covered but is not invoked by the production disposition path.

Alternative: retain the shared prompt or add more prohibitions. Rejected because both the original and reduced prompts were echoed or semantically violated by the local model. A larger model remains a future option, not a publication prerequisite.

### Keep formal facts outside model discretion

Caption normalization, docket, dates, status, official links, and approved action claims remain deterministic. For disposition-only cases, the deterministic compiler—not the model—owns title, dek, sections, citations, and the formal Court outcome. Ollama contributes only observations that survive exact evidence and policy validation.

Alternative: require verbatim generated action text. Rejected because it produces brittle prose and still delegates a formal field to a probabilistic component.

### Preserve accepted argued briefs for metadata-only disposition updates

When strict slip discovery adds or revises a disposition for an existing accepted argued case and no transcript content changed, the processor reuses the accepted public prose and argument analyses. It adds only typed official disposition metadata, derives the latest Court date and docket-based status deterministically, appends an immutable metadata revision, and updates integrity checkpoints. It does not retrieve unchanged transcript bodies or call Ollama.

Alternative: regenerate the entire case from every transcript and opinion. Rejected after a three-case backlog run spent more than two hours on unchanged material before cancellation.

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
3. Deploy the deterministic disposition compiler, validator, policy, and scope fingerprints with publication disabled in a reduced three-case run.
4. Inspect sanitized outputs and retained candidate; deploy only if all gates pass.
5. Run the normal nightly queue with bounded automatic retries and monitor fixed-code outcomes.
6. Roll back source code if needed; prior public release and compatible pending state remain valid.

## Open Questions

- If the 27B model still cannot satisfy the reduced contract, evaluate a reviewed larger local instruction-following model without changing the public or privacy architecture.
