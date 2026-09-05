## 1. Disposition Prompt and Deterministic Facts

- [x] 1.1 Replace the shared negative disposition instructions with a compact positive disposition-only prompt while retaining the strict zero-analysis schema.
- [x] 1.2 Keep caption, docket, dates, status, and typed Court-action observations deterministic and bump all affected processor fingerprints.
- [x] 1.3 Add request-capture tests proving disposition prompts omit argument priming and require actor-role distinctions.

## 2. Role-Aware Semantic Validation

- [x] 2.1 Classify action-bearing sentences as requested, lower-court, or Supreme Court actions and validate against same-role cited claims.
- [x] 2.2 Permit narrowly explicit negated oral-argument statements while continuing to reject positive or mixed invented proceedings.
- [x] 2.3 Add bidirectional regression tests for requested, lower-court, and Court actions plus negated and invented oral-argument language.

## 3. Bounded Retry State

- [x] 3.1 Add backward-compatible sanitized pending retry state and conservative retry/cooldown configuration bounds.
- [x] 3.2 Compute stable case-processing retry scopes from evidence and reviewed processor inputs without timestamps or run nonces.
- [x] 3.3 Persist typed model-output failure codes and retry-cycle outcomes without retaining prompts or rejected prose.
- [x] 3.4 Authorize only eligible scheduled retries under matching scopes, cooldown, lifetime, per-run, call, token, and runtime limits.
- [x] 3.5 Add tests for stable scopes, default duplicate denial, scheduled eligibility, cooldown, exhaustion, accepted/non-model exclusions, and scope reset.
- [x] 3.6 Enable narrow automatic retries only for scheduled nightly workflows and preserve receipt-before-checkpoint/deployment ordering.

## 4. Validation and Release

- [x] 4.1 Run the complete unit, integration, typing, lint, privacy, repository-policy, and workflow validation suites.
- [ ] 4.2 Run `26A274`, `26A203`, and `26A124` under reduced non-deploying bounds and inspect the retained candidate and sanitized diagnostics.
- [ ] 4.3 Deploy the exact retained candidate only if every gate passes, verify newest-first production surfaces and state identity, then document bounded backlog operations.
