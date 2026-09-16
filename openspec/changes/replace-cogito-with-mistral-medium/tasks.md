## 1. GPT-OSS Artifact and Operating Envelope

- [x] 1.1 Review and record GPT-OSS upstream provenance, Apache-2.0 license, owner eligibility, and the derivation of the local 32K Modelfile.
- [x] 1.2 Reverify installed `ragchew-gpt-oss:120b-32k` digest `820a68f9c4f7253846f5d82d225bc45ca93faa8cfe2a1009bff6efb15d662863`, explicit 32,768-token context, disk capacity, and idle memory headroom.
- [ ] 1.3 Run bounded cold and warm explicit-low-reasoning strict-schema extraction and Citizen’s Guide probes; verify nonempty final content and no retained reasoning, record only permitted identity, token, timing, expected-field, and resource observations, and prove model cleanup. (The rejected `think=false` attempt remains recorded as historical failure evidence.)
- [ ] 1.4 Conservatively project fixed-canary runtime from valid low-reasoning responses under existing request, retry, token, model-call, cost, and five-hour limits.

## 2. Citizen’s Guide Prompt and Planning

- [ ] 2.1 Add a versioned GPT-OSS Citizen’s Guide prompt profile that uses explicit bounded low reasoning, parses only final schema content, retains no reasoning, emits no more than 180 total words, and returns one or two ordinary-language sentences per applicable field.
- [ ] 2.2 Update guide planning so each model-writable field receives only its approved claims and applicable canonical action slots while identity, headings, order, sources, citations, claim IDs, action-slot IDs, and status remain deterministic.
- [ ] 2.3 Add field rules for issue, party positions, court action or status, and approved impact, including planner-controlled fallback or omission when evidence is insufficient.
- [x] 2.4 Require explicit actors and role-preserving verbs and prohibit ambiguous references, unsupported prediction, lawyer names, justice-by-justice detail, unnecessary procedural history, and unexplained jargon.
- [x] 2.5 Ensure assembly cannot accept model-selected metadata or move evidence between fields and continues to emit the existing public contract safely.

## 3. Canonical Action Validation

- [ ] 3.1 Rework action validation to compare each field with its canonical action slots and reserve hard rejection for demonstrated tuple conflict or required-slot omission.
- [ ] 3.2 Add transient process-local diagnostics containing field path, detected actor/action/object/role, expected slot, and contradiction or omission reason without logging private prose.
- [x] 3.3 Build a labeled paraphrase corpus covering faithful ordinary-language synonyms and role-changing counterexamples for party requests, lower-court actions, Supreme Court actions, negation, interim/final effect, and order issuer/recipient.
- [x] 3.4 Add false-positive and false-negative regression assertions showing lexical ambiguity alone is not a contradiction and demonstrated role changes still fail closed.

## 4. Exact Identity, Fingerprints, and Workflow Safety

- [x] 4.1 Update typed SCOTUS configuration and allowlists to require the exact 32K GPT-OSS tag and digest while preserving publication-disabled and no-fallback defaults.
- [ ] 4.2 Bind prompt-profile version, schema, exact model identity, reasoning level, context ceiling, and generation controls into processor and request fingerprints and invalidate stale candidate, retry, and reviewer state.
- [x] 4.3 Require exact tag/digest verification before client construction and immediately before and after every completion, rejecting absence or drift.
- [x] 4.4 Preserve workflow policy that forbids model pulls, mutable tags, hosted endpoints, reviewer-model dependencies, budget increases, and publication without explicit approval.
- [x] 4.5 Preserve unconditional volatile-evidence, prompt, response, rejected-prose, diagnostic, model-process, and workspace cleanup on success and failure.

## 5. Regression Coverage and Documentation

- [ ] 5.1 Add prompt/schema tests for field counts, word and sentence bounds, plain language, immediate term explanation, role verbs, status-safe omission, final-schema-only parsing, no reasoning retention, and no wrapper text.
- [ ] 5.2 Add exact identity tests for success, missing artifact, digest drift, context drift, reasoning-level drift, no `latest`, no native-context substitution, no hosted fallback, and fingerprint changes.
- [ ] 5.3 Extend fixture-backed, workflow-policy, privacy, repository-policy, receipt, cleanup, report, release, and promotion tests for the exact GPT-OSS candidate.
- [ ] 5.4 Update configuration, security, architecture, model, licensing, and Pages operations documentation with the exact identity, bounded low-reasoning/final-content-only protocol, Citizen’s Guide contract, validator policy, qualification procedure, and rollback.
- [ ] 5.5 Run focused tests, full tests, lint, typing, strict OpenSpec validation, public-repository/privacy checks, workflow validation, and stale Mistral/Cogito identity scans.

## 6. Protected Measurement and Decision

- [ ] 6.1 Run one fixture-backed end-to-end publication-disabled qualification and classify every failure without retaining private prose or weakening hard safety gates.
- [ ] 6.2 Run a fixed publication-disabled ten-case GPT-OSS canary from one exact parent and volatile evidence snapshot with complete outcome accounting and cleanup proof.
- [ ] 6.3 Manually inspect every hard-valid Citizen’s Guide against approved evidence, canonical action slots, and the active page; record correctness, readability, omissions, and regressions.
- [ ] 6.4 Promote only after at least eight of ten guides are accepted and judged improved, zero accepted correctness errors, no legacy degradation, passing privacy/release checks, and explicit exact-candidate approval; otherwise leave the release unchanged.
