## Context

Protected publication-disabled canary `34579544086` ran the recalibrated validation policy with `qwen3.8:27b`. It attempted the fixed ten-case manifest and retained an auditable rejected report: zero accepted, ten hard failures, and no warning-bearing hard-valid prose. Existing design explicitly requires model replacement to be a separate reviewed change.

The dedicated ARM64 Spark runner currently has several local Ollama models. `cogito:70b` is a 70.6B Q4_K_M Llama-family completion model with a 131,072-token native context and Apache-2.0 license. Its installed tag resolves to digest `8f2632d0faa422ff60435bc0095575d032a8b4a0f728df034d90ea654ffb60bb`. On 2026-09-11, a synthetic public-evidence probe through the same loopback OpenAI-compatible endpoint returned strict schema-valid JSON in 25 cold-start seconds, identified the exact Court action and object, and correctly declined to predict a winner. A post-implementation protected Spark probe on 2026-09-12 repeated the nested strict-schema protocol in 19.852 seconds with 71 prompt tokens and 45 completion tokens (116 total), the exact pinned digest, grounded actor/action/object, and no unsupported winner prediction; no prompt or response was retained.

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

### Reuse the rejected manifest with a contemporaneous Qwen control

The reviewed prior order is pinned in typed configuration. The historical all-failed Qwen run deliberately retained no candidate state, so its final per-case document checkpoints are not recoverable; run `34662688685` confirmed that the earlier durable checkpoint cannot stand in for those inputs. The replacement therefore uses a paired publication-disabled measurement from one unchanged generated-content parent: first exact installed `qwen3.8:27b` content digest `22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643` establishes a sanitized current-evidence baseline, then Cogito independently processes the same manifest from the original parent. The control digest recorded here must match the installed reviewed content before Court work.

Both arms use exactly the configured ten keys in order and derive configurations that differ only by explicit model role, tag, and digest. They run as sequential protected jobs so each retains the unchanged five-hour runtime budget; the Cogito job cannot start until the Qwen job has uploaded its complete sanitized binding. Before integrity hashing and analysis, docket HTML removes only the Court edge's nondeterministic Akamai Boomerang telemetry script; ambiguous or malformed telemetry markup fails closed, and all remaining official content stays byte-sensitive. Old attempts, retry scopes, aggregate counts, warnings, candidate identity, and reviewer state reset independently. The control baseline retains only the parent digest, fixed manifest, protocol digest, document identity/integrity digest, and sanitized control report digest. Cogito output is retained only if all ten control outcomes are accounted for, every nonaccepted control case is a bounded model-output failure, and its current document identities, SHA-256 values, byte counts, and disposition metadata exactly match the control baseline. Drift aborts the complete Cogito candidate. Receipts remain validated and uploadable after either arm fails, while private workspaces are still removed. If any key cannot be reconstructed safely, the paired run stops rather than substituting a case. Fresh unrelated Court activity remains explicit pending work and cannot consume measured slots.

### Treat the Cogito arm as a fresh approval decision

The new processor receives a fresh aggregate report and candidate digest. Prior reviewer counts and decisions do not carry over. The run uses `deploy=false`; an all-hard-failed result retains only the privacy-scanned report and opaque receipts. Promotion and 25/100-case stages remain unavailable unless at least eight cases are accepted and judged improved, accepted error counts are zero, no legacy page is degraded, and privacy/release validation pass.

## Risks / Trade-offs

- **[70B latency exceeds the five-hour budget]** → Keep current per-request and run bounds, benchmark synthetic requests first, stop with explicit pending work, and do not increase runtime limits in this change.
- **[Cogito returns fluent but unsupported legal prose]** → Preserve all deterministic grounding, action, chronology, prediction, and source validators without downgrade.
- **[Ollama tag changes in place]** → Require the reviewed full digest before client construction and around every completion, reject output on drift, and include the digest in configuration-derived fingerprints.
- **[Schema support differs between a tiny probe and full prompts]** → Add representative nested-schema tests, retain strict parsing/repair limits, and require the ten-case canary.
- **[Larger model increases memory pressure on the persistent runner]** → Run only under existing workflow concurrency, retain bounded context/output, and verify cleanup plus an empty `ollama ps` state after operational review.
- **[Historical Qwen evidence is irrecoverable]** → Run a contemporaneous Qwen control and Cogito arm from the same parent, bind Cogito to the sanitized current-evidence digest, and discard both on drift.
- **[Paired run approaches runner limits]** → Give each arm the unchanged per-run budgets, keep publication disabled, and allow no partial measurement or stage advancement.

## Migration Plan

1. Record the installed tag, full digest, license, architecture, and successful synthetic probe without retaining model prose beyond fixed expected fields and aggregate timing.
2. Update typed configuration, runtime/workflow preflight, repository checks, tests, and documentation for exact `cogito:70b` identity. Do not install or pull models in CI.
3. Confirm the resulting processor/request fingerprint differs from the rejected Qwen processor while prompts and policy versions remain fixed.
4. Run focused schema and latency probes, the full suite, typing, lint, OpenSpec, privacy, repository-policy, and workflow validation.
5. Run a protected `deploy=false` paired `canary_10`: preflight both exact local identities, run Qwen control and Cogito independently from the same parent, and retain only a privacy-scanned baseline, reports, receipts, and hard-valid public candidates.
6. Require exact evidence/protocol/parent bindings, then manually inspect every hard-valid Cogito candidate and warning against the contemporaneous control, active page, and official-source meaning.
7. If rejected or incomplete, retain the sanitized report, leave the live release unchanged, and stop. If approved, promote only the exact reviewed Cogito candidate and proceed through the existing measured 25/100 gates.

Rollback before approval is configuration-only because no public state is promoted. After any approved promotion, rollback uses the existing immutable prior release and requires a separately validated processor migration; changing the model tag or digest never occurs implicitly.

## Open Questions

None. The exact model, digest, comparison manifest, qualification threshold, and fail-closed outcome are fixed for implementation.
