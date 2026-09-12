## Why

The recalibrated `qwen3.8:27b` canary attempted all ten cases but produced zero hard-valid reader guides, so the eight-improved-case rollout threshold cannot be met with the current reviewed model. The protected Spark host already has Apache-2.0 `cogito:70b`; a production-protocol synthetic probe returned strict grounded JSON in 25 seconds, making it the strongest installed replacement that does not introduce a hosted provider, model credential, research-only license, or explicit reasoning-output risk.

## What Changes

- Replace the exact reviewed local generation model `qwen3.8:27b` with `cogito:70b` while retaining loopback-only Ollama, zero model cost, transient private inputs, and all existing source, grounding, hard-validation, privacy, and publication gates.
- Pin the reviewed installed Ollama content digest `8f2632d0faa422ff60435bc0095575d032a8b4a0f728df034d90ea654ffb60bb` as well as the model tag so mutable tag drift fails before Court or model work.
- Advance the processor/model identity without changing prompts or validation policy, allowing the model effect to be measured independently and preventing unchanged-input receipt collisions.
- Add exact-tag/digest, strict JSON-schema, latency/budget, fingerprint, workflow-policy, and fail-closed availability regressions.
- Because the completed historical Qwen run did not retain its final document checkpoints, run a contemporaneous publication-disabled Qwen control and Cogito candidate from the same generated-content parent and bind both to identical current document digests for the exact rejected ten-case manifest. Retain only sanitized comparison/review artifacts. Do not promote or run 25/100-case stages unless the Cogito candidate receives reviewed approval under the existing threshold.

## Capabilities

### New Capabilities
- `scotus-local-model-generation`: Exact local-model identity, compatibility qualification, processor isolation, and protected measured rollout for SCOTUS reader-guide generation.

### Modified Capabilities

None.

## Impact

This affects the typed SCOTUS generation/control configuration, processor fingerprinting and live Ollama preflight, sanitized generated-state comparison contracts, the protected Pages workflow, repository policy checks, model-adapter/config/workflow tests, and operations/security/model documentation. It changes no public URL, adds no external dependency or credential, sends no private material off the Spark host, and authorizes no publication by itself.
