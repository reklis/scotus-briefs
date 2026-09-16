# GPT-OSS 120B 32K SCOTUS qualification — 2026-09-15

## Status

**Synthetic low-reasoning qualification passed; the first protected canary attempt stopped in its Qwen control arm and produced no GPT-OSS candidate.** The exact local artifact returned nonempty final schema content for cold and warm extraction and Citizen’s Guide requests, retained no reasoning, passed the production Guide assembler and hard validator, fit Spark’s reviewed resource envelope, and was unloaded cleanly. Production processing, brief generation, launch, and publication remain disabled.

## Provenance, license, and owner decision

The candidate is a locally derived Ollama artifact named `ragchew-gpt-oss:120b-32k`. It derives from OpenAI’s upstream [`gpt-oss-120b`](https://huggingface.co/openai/gpt-oss-120b), distributed locally by Ollama as `gpt-oss:120b`. The derived Modelfile references local model blob `sha256-6be6d66a3f546d8c19b130dc41dc24b2fc159f84ffbc76a0ee0676205083cf5a` and pins `PARAMETER num_ctx 32768`; every reviewed application request overrides temperature to zero.

`ollama show --license` reports the Apache License 2.0. The repository owner selected this artifact for the project’s intended local use on 2026-09-15. Apache-2.0 has no Mistral-style revenue eligibility condition; this records the project decision and observed model metadata, not general legal advice. The model is separately installed and is not redistributed by this repository.

## Exact identity and operating envelope

- Reviewed tag: `ragchew-gpt-oss:120b-32k`
- Full Ollama digest: `820a68f9c4f7253846f5d82d225bc45ca93faa8cfe2a1009bff6efb15d662863`
- Base installed tag: `gpt-oss:120b`
- Base installed digest: `a951a23b46a1f6093dafee2ea481d634b4e31ac720a8a16f3f91e04f5a40ecd9`
- Reported model size: approximately 65 GB
- Reviewed context ceiling: 32,768 tokens
- Application temperature: 0
- Reasoning control: `reasoning_effort="low"` and root `think="low"`
- Final-content policy: parse only final schema content; never log, persist, diagnose from, receipt, or publish reasoning
- Endpoint: loopback Ollama OpenAI-compatible `/v1`
- Hosted, mutable-tag, native-context, and alternate-model fallback: prohibited

Before the final probes, Spark reported approximately 116.05–116.06 GiB available memory and no loaded model. During loaded probes, available memory remained between 51.68 and 51.77 GiB. After explicit cleanup it returned to 116.03 GiB, transient request files were removed, and the candidate was unloaded.

## Historical rejected non-thinking protocol

The first exact cold/warm run used `reasoning_effort="none"` and root `think=false`. All four extraction and Guide responses consumed completion tokens but exposed empty final assistant content. That protocol remains rejected and must not be restored. Its recorded timings were 19.52/8.29 seconds for extraction and 20.02/4.32 seconds for the Guide.

A first low-reasoning extraction profile succeeded, while the legacy detailed Guide request still returned empty final content. The Guide was therefore reduced to the approved planner-controlled high-level contract rather than increasing token or runtime budgets. Intermediate compact profiles were rejected when they exceeded field sentence bounds or failed existing production action/role validation. Each material request, schema, planner, repair, and canonical-slot change received a new fingerprint.

## Final sanitized strict-schema probe results

The repository generated synthetic extraction evidence and a synthetic decided-case Guide plan. They contained no live Court material or private production data. The final protocol used strict JSON schema, explicit low reasoning, `num_ctx=32768`, temperature zero, an 8,000-token extraction ceiling, and a 2,000-token Guide ceiling. Each request ran once cold and once warm. Reasoning fields were observed at the transport boundary and discarded; only final schema content was parsed transiently. No reasoning or generated prose was retained.

| Probe | Load state | Seconds | Prompt tokens | Completion tokens | Final content | Schema/assembly | Production validation |
|---|---:|---:|---:|---:|---:|---:|---:|
| extraction v11 | cold | 27.74 | 636 | 572 | yes | pass | pass |
| extraction v11 | warm | 16.27 | 638 | 573 | yes | pass | pass |
| Citizen’s Guide v7 / planner v8 | cold | 21.27 | 916 | 137 | yes | pass | pass, 1 style warning |
| Citizen’s Guide v7 / planner v8 | warm | 8.10 | 831 | 136 | yes | pass | pass, 2 style warnings |

The final Guide response contained exactly the four applicable planner fields, remained within the 180-word and one-sentence-per-field profile, preserved deterministic metadata outside the model response, and passed field-specific canonical-slot validation. Style warnings are nonfatal and do not authorize model rewriting.

## First fixed-canary attempt

Protected workflow run [`35095151207`](https://github.com/reklis/scotus-briefs/actions/runs/35095151207) used source commit `685cf3a3c6539d1e4c44f86fba948e433b642b11`, `mode=nightly`, `editorial_rollout_stage=canary_10`, `maximum_cases=0`, and `deploy=false`. The fixed ten-case manifest and current generated-content parent were used without substitution. The run stopped in the contemporaneous Qwen control arm after approximately 74 minutes, before GPT-OSS started.

Sanitized outcome accounting was:

- `2025-26a124`: extraction validation failure;
- `2025-24-43`: extraction validation failure;
- `2025-24-38`: Citizen’s Guide sentence-limit failure;
- `2024-24a884`: invalid writer schema; and
- `2024-24-394`, `2024-24-304`, `2024-24-362`, `2024-24-249`, `2024-24-320`, and `2024-24-7`: source unavailable before a model attempt.

The control made 39 receipted model calls: 37 extraction calls and 2 Guide calls. All transport attempts completed, but no case was accepted. Because six nonaccepted outcomes had no model attempt, the paired-baseline validator correctly rejected the incomplete comparison with `paired canary nonaccepted outcomes require a model attempt`. No control binding, GPT-OSS call, review manifest, preview candidate, state candidate, or deployable artifact was created. Only opaque control receipts were retained for one day. Volatile evidence and workspaces were removed; the remaining Qwen process was stopped explicitly, no model remained loaded, and Spark returned to 116.06 GiB available memory. Workflow cleanup was subsequently hardened to unload both exact canary models unconditionally.

This is a failed qualification attempt, not a completed canary and not evidence for promotion. The fixed manifest must not be substituted or reduced; source availability must be diagnosed or a fresh exact canary must complete before manual Guide review.

## Runtime assessment

Using the slowest valid cold response, 100 sequential cold calls project to about 46.3 minutes of model time before Court I/O, deterministic validation, and bounded retry overhead. This remains below the unchanged five-hour ceiling and does not require a budget increase. The estimate is conservative but synthetic; fixture-backed and fixed ten-case measurements are still required before promotion.

## Decision and cleanup

The exact artifact passes identity, license record, disk, memory headroom, context, final-content-only low-reasoning transport, synthetic extraction, synthetic Guide assembly/validation, runtime projection, and cleanup checks. It is **not approved for production or publication**. No Court retrieval, private-evidence request, candidate upload, public-content mutation, release change, or model substitution occurred during these probes.

Next required gates are one fixture-backed publication-disabled end-to-end qualification, a complete fixed publication-disabled ten-case canary, manual review of every hard-valid Guide, privacy/release checks, and explicit exact-candidate approval. The failed control-only run above supplies no reviewable candidate. All processing and launch gates remain closed until those gates pass.
