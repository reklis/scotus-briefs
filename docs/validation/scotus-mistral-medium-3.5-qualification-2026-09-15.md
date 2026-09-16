# Mistral Medium 3.5 local qualification — 2026-09-15

## Decision

**Rejected before implementation and live Court access.** The locally installed model did not pass the bounded synthetic qualification gate and made the Spark host unavailable during follow-up probing. No SCOTUS configuration, workflow model identity, validation policy, publication state, generated content, or live release was changed. No paired canary was started.

## License and provenance review

The reviewed upstream artifact is Mistral AI's `Mistral-Medium-3.5-128B`, documented at <https://huggingface.co/mistralai/Mistral-Medium-3.5-128B> and distributed by Ollama as the explicit local tag `mistral-medium-3.5:128b`. The upstream model card describes a dense 128B model, configurable `reasoning_effort`, and a 256K context window. It warns that GGUFs generated before the corrected Transformers configuration may have degraded long-context behavior.

The weights use Mistral AI's Modified MIT License. It permits use subject to attribution and excludes a company or employer whose global consolidated revenue exceeded USD 20 million in the preceding month unless separately licensed by Mistral AI. On 2026-09-15, the repository owner explicitly confirmed that the restriction does not prevent this intended use. This records the owner's eligibility decision; it is not a general legal conclusion or a change to the repository's Apache-2.0 and CC BY 4.0 licensing.

## Installed identity and pre-load capacity

An operator installed the model directly on Spark outside protected automation. GitHub Actions received no pull or fallback path.

- Ollama version: `0.32.13`
- Exact tag: `mistral-medium-3.5:128b`
- Native Ollama digest: `0341632adb051badc332bd814779ab931ab78619750bebe3b7c7b1401e9d70df`
- Model blob size: 80,241,475,099 bytes
- Architecture reported by Ollama: `mistral3`, 127.7B parameters, Q4_K_M
- Context reported by Ollama: 262,144 tokens
- Pre-install free disk: 1.7 TB
- Post-install free disk: 1.6 TB
- Pre-load available system memory: 115 GiB of 119 GiB
- Loaded models before qualification: none

## Bounded synthetic result

The first repository-authored synthetic extraction request used the loopback-compatible OpenAI strict JSON-schema shape, `reasoning_effort="none"`, `think=false`, an 8,000-token output ceiling, and a model-neutral instruction requiring only JSON with no reasoning or commentary. It contained no Court material or private production data. The model returned content, but the existing typed extraction parser rejected it with fixed safe code `invalid_schema`. No prompt or model prose was retained.

A follow-up diagnostic was limited to schema error locations/types and a compact-writer probe, again with synthetic data only. During model loading, Ollama stopped accepting connections and SSH became unavailable. Previous-boot logs inspected after recovery confirm the exact cause: Ollama selected the model's native 262,144-token context, allocated a 28,672 MiB CPU KV buffer in addition to 48,303 MiB of CUDA model buffers and 23,104 MiB of host model buffers, and triggered NVIDIA/system out-of-memory failures. The kernel killed `llama-server`; Ollama recorded a 90.3 GiB service memory peak and 15.8 GiB swap peak, and the host subsequently required reboot. Consequently:

- strict extraction schema compatibility was not demonstrated;
- compact-writer compatibility was not demonstrated;
- cold/warm latency and token measurements were not completed;
- safe runtime memory headroom was not demonstrated;
- post-request unload and cleanup could not be confirmed remotely.

The protected five-hour canary cannot be conservatively projected from a model that exhausts the host under Ollama's native 262,144-token default context. Existing runtime, token, retry, memory, and validation limits were not increased. A smaller explicit context might fit, but it changes the reviewed runtime protocol and must be designed and qualified before another load attempt.

## Operational follow-up

Spark recovered after reboot with Ollama active. A protected Cogito process was running at inspection time, so no competing Mistral load or unload was attempted. The installed Mistral artifact may be removed with `ollama rm mistral-medium-3.5:128b`; removal is optional but no production workflow is authorized to select it.

Mistral remains rejected unless a new reviewed change explains and corrects the capacity/protocol failures and repeats qualification from the beginning. The current Cogito publication-disabled configuration and active public release remain unchanged.
