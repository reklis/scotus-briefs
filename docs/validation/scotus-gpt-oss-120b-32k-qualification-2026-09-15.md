# GPT-OSS 120B 32K SCOTUS qualification — 2026-09-15

## Status

**Historical structured-output qualification passed; the first protected canary attempt stopped in its Qwen control arm and produced no GPT-OSS candidate.** The exact local artifact returned nonempty final schema content for cold and warm extraction and Citizen’s Guide requests, retained no reasoning, fit Spark’s reviewed resource envelope, and was unloaded cleanly. Production processing, brief generation, launch, and publication remain disabled.

The structured extraction/JSON Guide protocol described below is now superseded for
Citizen’s Guides by `scotus-plain-text-qa-v1`. The new protocol asks five deterministic
questions directly against bounded official Court text, reads only nonblank final plain
text, and uses no Guide response schema or extracted claims. Historical measurements remain
model/runtime evidence only; fresh fixture-backed, GPT-OSS/Qwen sample, and fixed-canary
qualification are required for the new fingerprint.

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

### Source-failure diagnosis

A later source-only diagnostic on Spark reconstructed the public canonical transcript, docket, and disposition URLs for all ten fixed cases. It used the repository's exact `HttpxSourceFetcher`, reviewed user agent, one persistent no-redirect client, 50 MiB response limit, 60-second timeout, and one-second shared request interval. All 26 canonical documents returned HTTP 200 through the same host path. Responses totaled 9,094,771 bytes and individually ranged from 37,545 to 891,079 bytes, well below configured response, run-download, and volatile-cache limits. No model was loaded or called. Response bodies remained process-local, the mode-0700 tmpfs checkout and URL packet were removed, and no diagnosis workspace remained.

The six failed case URLs are therefore not stale, redirected, oversized, or permanently unavailable. The historical evidence cache and response bodies were correctly deleted at the end of the failed run, and the earlier sanitized logger collapsed every `SourceFetchError` to its class name, so the exact historical HTTP or transport reason cannot be recovered safely. The observed pattern—six immediate first-document failures after four long-running model cases, followed by complete success through the same persistent source client—is consistent with a transient endpoint or transport failure, not a manifest defect. This remains a diagnosis rather than proof of a specific past status code.

Future source failures now carry only an allowlisted safe reason code (`official_http_<status>`, `official_redirect`, `official_response_too_large`, or `official_transport_error`) into the existing sanitized case-failure log. They still retain no URL, source text, response body, field path, or private diagnostic. This improves a fresh canary's failure classification without weakening source or publication gates.

### Manual-review-only sample

On 2026-09-17, a publication-disabled sample used the recovered official `26A124`
opinion and a freshly fetched official docket in a mode-0700 Spark tmpfs workspace. Fresh
production extraction completed, the exact GPT-OSS tag, full digest, and 32,768-token
context were verified before and after inference, and the first Guide response passed only
the strict field/type/nonblank schema boundary. It was displayed unchanged with
`manual_review_required`; no prose validator or repair request ran.

No authorized human review decision was recorded for this sample. An assistant assessment
flagged that one field did not fairly summarize both sides and that another contained awkward
or apparently malformed wording requiring source-level verification, but that assessment was
not an approval or rejection. The candidate therefore remains `manual_review_required`.
This is the intended separation: schema success produced a reviewable draft but did not
establish correctness or approval. No prompt, source text, reasoning, observation, transport
body, or generated prose was written to an artifact. The workspace and temporary probe were
removed, the model was unloaded, and Spark returned to 115 GiB available memory. The active
release was unchanged.

### Direct plain-text Q&A comparison

On 2026-09-17, the new `scotus-plain-text-qa-v1` path parsed one recovered official
`26A124` opinion and one freshly fetched official docket once, built one deterministic set
of five source packets, and replayed those identical packets through exact GPT-OSS and exact
Qwen. Both models returned five nonblank plain-text answers without structured extraction,
a Guide response schema, or repair. The unchanged answers were displayed to the human
reviewer with `manual_review_required`; no reviewer decision was inferred or recorded.
Qwen’s Court-decision answer ended with an incomplete final sentence, demonstrating why
structural success remains distinct from approval.

The exact tag/digest and reviewed context capacity were verified around every completion.
No source text, prompt, reasoning, intermediate map answer, transport body, or final prose
was written to a retained artifact. The mode-0700 tmpfs workspace and local probe were
removed, both models were unloaded, Spark returned to 115 GiB available memory, and the
active release remained unchanged. This is a small protocol sample, not fixture-backed or
fixed-canary qualification.

### Candidate-bound review decision for 26A124

On 2026-09-17, an authorized human reviewed the five unchanged GPT-OSS answers for
`26A124` against the linked official docket and opinion and recorded **approved** for exact
candidate SHA-256
`6942f6128c3a6b2fa4511524ec0df9f6d39a24f592c49cb19da1afa5f1ac845d`.
The approval covers only that one transient five-answer candidate. It does not approve the
fixed-ten set, the processor generally, deployment, promotion, or publication of any other
candidate. The generated prose was not retained as an artifact; the active release remained
unchanged.

On the same date, the authorized human also reviewed the five unchanged GPT-OSS answers for
argued-and-decided case `24-43` against its official docket, opinion, and transcript and
recorded **approved** for exact candidate SHA-256
`e3eee626b89d482afad88c420a82ea20f19de07500308fed4ca79874b2837b3a`.
That approval likewise covers only the named transient five-answer candidate and does not
approve a full-corpus run, deployment, promotion, or publication. The generated prose was not
retained, the private workspace was removed, and the active release remained unchanged.

## Direct Q&A fixed-ten qualification

On 2026-09-17, the fixture-backed Q&A suite and a publication-disabled fixed-ten run
completed against the recovered manifest without changing public state. The run verified all
18 PDF sizes and SHA-256 digests, fetched each official docket, parsed the recovered opinions
and transcripts, built all five bounded question packets, and generated transient answers with
the exact pinned models. Exact tag/digest and the reviewed 32,768-token request envelope were
rechecked around every completion.

Sanitized structural results were:

| Model | Cases attempted | Five-answer candidates | Failed cases | Model calls | Runtime |
|---|---:|---:|---:|---:|---:|
| `ragchew-gpt-oss:120b-32k` | 10 | 10 | 0 | 88 | 457.4 s |
| `qwen3.8:27b` control | 10 | 9 | 1 `empty_content` | 82 | 1,252.5 s |

The Qwen failure was bounded and case-local; every fixed case was attempted and every
nonaccepted outcome included a model attempt. Structural generation does not establish that
any answer is correct, improved, reviewed, or publishable. No answer, copied source text,
prompt, reasoning, intermediate map answer, or transport body was retained by the run; the
separately retained recovered-PDF archive was unchanged. Only the aggregate counts above
crossed the private workspace boundary. Both models were unloaded, the mode-0700 tmpfs
workspace was removed, Spark reported no loaded model and 116 GiB available memory, and the
active release remained unchanged.

## Runtime assessment

The measured fixed-ten GPT-OSS run used 88 calls and 457.4 seconds of model time; the Qwen
control used 82 calls and 1,252.5 seconds. Both remained below the unchanged five-hour and
model-call ceilings without a budget increase. Fixture and fixed-ten structural qualification
are complete, but candidate-bound human review and explicit approval are still required before
promotion.

## Decision and cleanup

The exact artifact passes historical identity, license record, disk, memory headroom, context, final-content-only low-reasoning transport, runtime projection, and cleanup checks. The new direct Q&A processor is **not approved for production or publication**. No Court retrieval, private-evidence request, candidate upload, public-content mutation, release change, or model substitution occurred during these probes.

Next required gates are one fixture-backed publication-disabled end-to-end qualification, a complete fixed publication-disabled ten-case canary, manual review of every schema-valid generated Guide, privacy/release checks, and explicit exact-candidate approval. The failed control-only run above supplies no reviewable candidate. All processing and launch gates remain closed until those gates pass.
