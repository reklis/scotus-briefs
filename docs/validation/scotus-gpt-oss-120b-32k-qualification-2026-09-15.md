# GPT-OSS 120B 32K SCOTUS qualification — 2026-09-15

## Status

**Blocked before Court evidence or publication-disabled canary use.** The exact local artifact fits Spark’s reviewed resource envelope, but the currently implemented non-thinking OpenAI-compatible request protocol returned no content for every cold and warm strict-schema extraction and Citizen’s Guide probe. Production publication remains disabled and no model prose was retained.

## Provenance, license, and owner decision

The candidate is a locally derived Ollama artifact named `ragchew-gpt-oss:120b-32k`. It derives from OpenAI’s upstream [`gpt-oss-120b`](https://huggingface.co/openai/gpt-oss-120b), distributed locally by Ollama as `gpt-oss:120b`. The derived Modelfile references local model blob `sha256-6be6d66a3f546d8c19b130dc41dc24b2fc159f84ffbc76a0ee0676205083cf5a` and pins `PARAMETER num_ctx 32768`; the upstream default temperature remains present in the Modelfile, while every reviewed application request overrides temperature to zero.

`ollama show --license` reports the Apache License 2.0. The repository owner selected this artifact for the project’s intended local use on 2026-09-15. Apache-2.0 has no Mistral-style revenue eligibility condition; this records the project decision and observed model metadata, not general legal advice. The model is separately installed and is not redistributed by this repository.

## Exact identity and operating envelope

- Reviewed tag: `ragchew-gpt-oss:120b-32k`
- Full Ollama digest: `820a68f9c4f7253846f5d82d225bc45ca93faa8cfe2a1009bff6efb15d662863`
- Base installed tag: `gpt-oss:120b`
- Base installed digest: `a951a23b46a1f6093dafee2ea481d634b4e31ac720a8a16f3f91e04f5a40ecd9`
- Reported model size: approximately 65 GB
- Reviewed context ceiling: 32,768 tokens
- Application temperature: 0
- Endpoint: loopback Ollama OpenAI-compatible `/v1`
- Hosted, mutable-tag, native-context, and alternate-model fallback: prohibited

Before probing, Spark reported approximately 116.07 GiB available memory, 16.00 GiB total swap with effectively none used, and approximately 1.67 TB free on the model filesystem. Ollama had no loaded model. During each loaded probe, available memory remained between 51.62 and 51.73 GiB. After explicit cleanup, available memory returned to 116.01 GiB and `ollama ps` confirmed the candidate was unloaded.

## Sanitized strict-schema probe results

The repository generated one synthetic extraction request and one synthetic decided-case Citizen’s Guide request. They contained no live Court material or private production data. The exact configured protocol used strict JSON schema, `reasoning_effort="none"`, root `think=false`, `num_ctx=32768`, temperature zero, and a 2,000-token probe output ceiling. Each request was run once cold and once warm. Only fixed validity booleans and aggregate timing/token/resource measurements were retained.

| Probe | Load state | Seconds | Prompt tokens | Completion tokens | Content present | Valid JSON/schema |
|---|---:|---:|---:|---:|---:|---:|
| extraction | cold | 19.52 | 358 | 305 | no | no |
| extraction | warm | 8.29 | 358 | 305 | no | no |
| Citizen’s Guide | cold | 20.02 | 1,530 | 318 | no | no |
| Citizen’s Guide | warm | 4.32 | 1,530 | 149 | no | no |

All four responses ended normally according to the transport but exposed an empty assistant content field. This reproduces the earlier observation that explicit `think=false` suppresses GPT-OSS answer content on this Ollama build. The response cannot enter the typed parser and therefore fails closed before grounding or publication checks.

## Runtime assessment

Measured cold and warm transport latency is comfortably below the unchanged five-hour ceiling in isolation. Even the conservative upper bound of 100 cold 20.02-second requests would consume about 33.4 minutes of model time before Court I/O and validation. That estimate does **not** authorize a canary: every measured response was unusable, and valid-output retry behavior cannot be projected from an all-empty protocol.

## Decision and cleanup

The exact artifact passes identity, license-record, disk, memory-headroom, context, and cleanup checks. It fails the currently specified non-thinking request protocol. No Court retrieval, private-evidence request, generated-content mutation, candidate upload, release change, or model substitution occurred. Transient request files were removed from `/dev/shm`, the model was stopped, and only this sanitized report remains.

Before another model request, the planning artifacts must decide whether GPT-OSS may use its supported reasoning mode while retaining only final schema content and never retaining reasoning. That is a request-semantics change requiring a new prompt/profile fingerprint and fresh bounded qualification.
