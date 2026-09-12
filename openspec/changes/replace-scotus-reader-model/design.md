## Context

Protected publication-disabled canary `34579544086` ran the recalibrated validation policy with `qwen3.8:27b`. It attempted the fixed ten-case manifest and retained an auditable rejected report: zero accepted, ten hard failures, and no warning-bearing hard-valid prose. Existing design explicitly requires model replacement to be a separate reviewed change.

The dedicated ARM64 Spark runner currently has several local Ollama models. `cogito:70b` is a 70.6B Q4_K_M Llama-family completion model with a 131,072-token native context and Apache-2.0 license. Its installed tag resolves to digest `8f2632d0faa422ff60435bc0095575d032a8b4a0f728df034d90ea654ffb60bb`. On 2026-09-11, a synthetic public-evidence probe through the same loopback OpenAI-compatible endpoint returned strict schema-valid JSON in 25 cold-start seconds, identified the exact Court action and object, and correctly declined to predict a winner.

Official documents, extracted text, prompts, model responses, repair diagnostics, and rejected prose must remain transient. Model replacement cannot relax any source, grounding, actor/action, chronology, prediction, privacy, severe-bound, publication, or rollout gate. The active release must remain unchanged until a separately reviewed measured candidate qualifies.

## Goals / Non-Goals

**Goals:**

- Replace the reviewed local reader-guide model with exact installed `cogito:70b` content.
- Fail before Court retrieval or completion if either the model tag or content digest differs.
- Keep prompts and validation policy fixed so the canary measures the model change rather than a mixed intervention.
- Produce a distinct processor and request fingerprint automatically from the model/config change.
- Reuse the rejected recalibrated ten-case manifest when every case remains eligible, enabling direct comparison.
- Run the replacement through focused compatibility checks and a protected publication-disabled canary before any rollout.

**Non-Goals:**

- Relaxing hard validation, warning policy, privacy, source, budget, or publication requirements.
- Sending evidence to a hosted provider or adding model credentials.
- Installing or downloading another model during a workflow.
- Using `mistral-large:123b`, whose research license needs separate legal review.
- Using a custom `charmander` tag without provenance/license review, or `deepseek-r1:70b` while its explicit reasoning behavior remains a structured-output/runtime risk.
- Treating a successful synthetic probe as publication approval.

## Decisions

### Use Cogito 70B as the single reviewed replacement

Configuration and typed allowlists will select only `cogito:70b`. It materially increases capacity over the failed 27B model, has an Apache-2.0 license, fits the existing Spark host, supports completion through Ollama, and passed the production-protocol strict-schema probe.

Alternative: use `mistral-large:123b`. Rejected pending license review and because its 73 GB footprint creates a larger five-hour canary risk.

Alternative: use `deepseek-r1:70b`. Rejected for this attempt because explicit reasoning can consume bounded output/runtime before strict JSON is returned.

Alternative: use Charmander. Rejected until its custom provenance, license, raw prompt template, and production schema behavior are reviewed.

### Pin both model tag and immutable Ollama digest

The reviewed configuration and workflow policy will carry both `cogito:70b` and digest `8f2632d0faa422ff60435bc0095575d032a8b4a0f728df034d90ea654ffb60bb`. The protected build will query loopback `/api/tags` and require one entry matching both values before source or model work. The runtime adapter will perform the same native tag/digest check before constructing Court/model clients and immediately before and after each completion, rejecting any output that spans mutable-tag drift. The digest participates in the configuration hash, so processor and request scopes change if reviewed model content changes even under a reused tag.

Alternative: pin only the tag as before. Rejected because Ollama tags are mutable and could silently change the measured model.

### Qualify protocol behavior with synthetic evidence before live use

Tests and the protected preflight will cover the OpenAI-compatible strict `json_schema` response contract, non-thinking request flag, exact grounded action extraction, refusal to invent a predicted winner, and bounded response time/token use. Probe prompts contain only repository-authored synthetic evidence. A probe failure stops before Court retrieval and does not authorize fallback to another installed model.

### Keep prompts, policy, budgets, and hard validators fixed

The replacement changes model identity only. Existing compact writer, planner, field repair, hard/error tiers, warning codes, source allowlists, transient workspace, zero-cost ledger, transport limits, and five-hour bound remain unchanged. This isolates the model comparison and prevents a larger model from bypassing correctness controls.

### Reuse the rejected canary manifest or fail closed

The reviewed prior order is pinned in typed configuration. When the prior `canary_10` state belongs to a different processor and all ten public cases remain eligible, the new processor starts with exactly those case keys in that order. Old processor attempts, retry scopes, aggregate counts, warnings, candidate identity, and reviewer state are reset. Each admitted case must retain the durable comparison metadata and official document bytes; drift fails before model work. If any key cannot be reconstructed safely, the measured run stops rather than silently substituting a more favorable case. Fresh unrelated Court activity remains explicit pending work and cannot consume reserved measured slots. A new replacement-model failure may re-enter only the existing finite scheduled retry policy.

### Treat the new canary as a fresh approval decision

The new processor receives a fresh aggregate report and candidate digest. Prior reviewer counts and decisions do not carry over. The run uses `deploy=false`; an all-hard-failed result retains only the privacy-scanned report and opaque receipts. Promotion and 25/100-case stages remain unavailable unless at least eight cases are accepted and judged improved, accepted error counts are zero, no legacy page is degraded, and privacy/release validation pass.

## Risks / Trade-offs

- **[70B latency exceeds the five-hour budget]** → Keep current per-request and run bounds, benchmark synthetic requests first, stop with explicit pending work, and do not increase runtime limits in this change.
- **[Cogito returns fluent but unsupported legal prose]** → Preserve all deterministic grounding, action, chronology, prediction, and source validators without downgrade.
- **[Ollama tag changes in place]** → Require the reviewed full digest before client construction and around every completion, reject output on drift, and include the digest in configuration-derived fingerprints.
- **[Schema support differs between a tiny probe and full prompts]** → Add representative nested-schema tests, retain strict parsing/repair limits, and require the ten-case canary.
- **[Larger model increases memory pressure on the persistent runner]** → Run only under existing workflow concurrency, retain bounded context/output, and verify cleanup plus an empty `ollama ps` state after operational review.
- **[Old and new canaries are not comparable]** → Reuse the exact prior manifest or fail closed; keep all non-model policy inputs unchanged.

## Migration Plan

1. Record the installed tag, full digest, license, architecture, and successful synthetic probe without retaining model prose beyond fixed expected fields and aggregate timing.
2. Update typed configuration, runtime/workflow preflight, repository checks, tests, and documentation for exact `cogito:70b` identity. Do not install or pull models in CI.
3. Confirm the resulting processor/request fingerprint differs from the rejected Qwen processor while prompts and policy versions remain fixed.
4. Run focused schema and latency probes, the full suite, typing, lint, OpenSpec, privacy, repository-policy, and workflow validation.
5. Run a protected `deploy=false` `canary_10` against the exact prior manifest and manually inspect every hard-valid candidate and warning against its active page and official-source meaning.
6. If rejected, retain the sanitized report, leave the live release unchanged, and stop. If approved, promote only the exact reviewed candidate and proceed through the existing measured 25/100 gates.

Rollback before approval is configuration-only because no public state is promoted. After any approved promotion, rollback uses the existing immutable prior release and requires a separately validated processor migration; changing the model tag or digest never occurs implicitly.

## Open Questions

None. The exact model, digest, comparison manifest, qualification threshold, and fail-closed outcome are fixed for implementation.
