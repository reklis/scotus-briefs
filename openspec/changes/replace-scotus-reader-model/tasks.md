## 1. Exact Model Identity

- [ ] 1.1 Replace the typed SCOTUS generation allowlist with `cogito:70b` and add the reviewed full Ollama digest to validated configuration.
- [ ] 1.2 Update the protected workflow to require the exact configured tag and digest from loopback Ollama before any Court retrieval or completion, with no pull or fallback path.
- [ ] 1.3 Include the reviewed model content identity in processor and request fingerprints while leaving prompt and validation-policy versions unchanged.

## 2. Comparable Measured Selection

- [ ] 2.1 Reuse the prior rejected ten-case manifest in recorded order when starting `canary_10` under the new processor, and fail closed if any case cannot be safely reconstructed.
- [ ] 2.2 Reset candidate identity, automatic counts, warnings, and reviewer fields for the new processor while retaining existing promotion and sequential-stage guards.

## 3. Regression Coverage and Documentation

- [ ] 3.1 Add strict nested JSON-schema compatibility tests covering exact grounded Court action/object extraction and refusal to predict an unsupported winner.
- [ ] 3.2 Add regressions for exact tag/digest success, missing tag, digest drift, no fallback, processor/request fingerprint changes, and unchanged prompt/policy identities.
- [ ] 3.3 Extend measured-selection, workflow-policy, privacy, repository-policy, and promotion tests for the replacement model and exact prior manifest.
- [ ] 3.4 Update model, security, configuration, architecture, and Pages operations documentation with the reviewed Cogito identity, license, probe evidence, rollback, and canary procedure.

## 4. Verification and Protected Rollout

- [ ] 4.1 Run focused tests, the complete suite, formatting/lint, typing, OpenSpec strict validation, public-repository/privacy checks, and verify the package/workflow contains no stale production Qwen identity.
- [ ] 4.2 Repeat a bounded synthetic production-protocol probe on the protected Spark runner and record only fixed expected fields, aggregate timing/token counts, model tag, and digest.
- [ ] 4.3 Run a protected publication-disabled ten-case Cogito canary against the exact comparison manifest, inspect every hard-valid candidate and warning against its active page and official-source meaning, and record the reviewed outcome.
- [ ] 4.4 Only after qualifying approval, promote the exact reviewed candidate and proceed sequentially through the existing 25-case and up-to-100-case measured gates; otherwise stop with the live release unchanged.
