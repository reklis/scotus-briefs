## 1. Restore a safe baseline

- [ ] 1.1 Remove the all-legacy processor-migration queue behavior introduced by the failed 100-case experiment so unselected legacy cases are not marked budget-exhausted.
- [ ] 1.2 Add a regression test proving a missing legacy processor fingerprint alone cannot place the complete corpus into `PendingWork` before bounded backfill selection.
- [ ] 1.3 Record sanitized run `34147702102` outcomes and the undeployed-candidate decision in the Pages operations runbook without retaining generated prose or private inputs.

## 2. Reader-guide and backfill contracts

- [ ] 2.1 Add private `ReaderGuidePlan`, section-packet, argument-packet, and canonical actor/action-slot contracts with strict size and field bounds.
- [ ] 2.2 Add an optional versioned `EditorialBackfillState` public-state contract with rollout stage, processor identity, rank boundary, and aggregate counters.
- [ ] 2.3 Extend static-state consistency and privacy validation for backfill state, fixed repair codes, and sanitized canary aggregates.
- [ ] 2.4 Add round-trip and legacy-default tests proving prior generated-content state remains readable and unknown/private fields fail closed.

## 3. Deterministic compact planning

- [ ] 3.1 Implement section planning that assigns only role-appropriate approved claims to each fixed reader purpose and fails when required support is absent.
- [ ] 3.2 Implement bounded nonduplicative claim selection for background, procedural path, legal issue, each side, justice questions, Court action, reasoning, and next-known procedural step.
- [ ] 3.3 Build canonical requested/lower-court/Supreme-Court action slots containing actor, action, object, negation, and interim/final effect.
- [ ] 3.4 Build chronological per-session argument packets that never mix claims across argument or reargument sessions.
- [ ] 3.5 Add planner tests for long ledgers, sparse dispositions, multiple sides, unknown advocate roles, reargument, separate opinions, and claim/character bounds.

## 4. Compact writing and targeted repair

- [ ] 4.1 Replace whole-ledger generation with a strict compact reader-guide request whose model task is translation rather than fact selection or legal-status determination.
- [ ] 4.2 Preserve deterministic caption, docket, dates, status, links, section/session order, and supplied claim IDs when assembling model prose.
- [ ] 4.3 Implement process-local field diagnostics and schema-constrained repair requests containing only the rejected field and its support packet.
- [ ] 4.4 Preserve every valid draft field byte-for-byte across a repair and rerun all field and whole-guide validators after assembly.
- [ ] 4.5 Apply per-field, per-case, call, token, and runtime budgets to repairs and persist only fixed safe failure codes and opaque receipts.
- [ ] 4.6 Advance planner, prompt, terminology, policy, schema, and processor fingerprints and add request-capture tests proving compact inputs and private repair behavior.

## 5. Reader-prose and action validation

- [ ] 5.1 Move reviewed legal terminology, ordinary alternatives, and same-sentence explanation patterns into a versioned resource with configuration validation.
- [ ] 5.2 Populate the resource from reviewed public-corpus vocabulary, including waiver, pretext, rebuttal, finality, standing, jurisdiction, injunction, vacatur, remand, habeas, sovereign immunity, and related doctrine/procedure terms.
- [ ] 5.3 Implement title, summary, section, and argument validation for contextual term explanations, deterministic readability, sentence/paragraph length, repetition, and section relevance.
- [ ] 5.4 Reject internal processing language, unsupported no-decision claims, and unsupported statements about what the Court will decide, clarify, establish, guide, affect, or do next.
- [ ] 5.5 Extend canonical action matching for reviewed plain verbs and phrases while preserving actor, object, polarity, and interim/final effect.
- [ ] 5.6 Add parameterized term/gloss tests and invented regressions shaped like the failed jurisdiction, injunction, equal-protection, habeas, vacatur, action-role, and new-page outputs.

## 6. Atomic opinion-aware updates

- [ ] 6.1 Restrict metadata-only updates to corrections that do not change case status, maturity, or prose meaning.
- [ ] 6.2 Require every status-changing order or opinion to pass download, parse, extraction, compact planning, writing, and validation with all required current argument sessions.
- [ ] 6.3 Atomically advance status, maturity, disposition metadata, case history, and prose only after the complete case revision passes.
- [ ] 6.4 Add tests proving a decided page cannot retain `awaiting decision` prose and that failed opinion rewriting leaves the complete prior case active.

## 7. Resumable newest-first editorial backfill

- [ ] 7.1 Implement processor-scoped newest-first backfill selection that enqueues only the configured rollout slice after fresh changes and eligible retries.
- [ ] 7.2 Persist and validate cursor progress so runtime-deferred selected cases resume without duplication while unselected cases stay out of pending state.
- [ ] 7.3 Ensure a failed newest case enters bounded retry rotation without starving older backfill candidates.
- [ ] 7.4 Add CLI/workflow controls for publication-disabled canary, 25-case, and up-to-100-case stages that can only reduce configured budgets.
- [ ] 7.5 Add ordering, cursor reset, runtime exhaustion, retry fairness, pending-count, and generated-state round-trip tests using a large synthetic legacy corpus.

## 8. Canary qualification and operations

- [ ] 8.1 Define a deterministic ten-case newest-first canary manifest covering disposition-only, argued, and decided-after-argument cases without storing source or rejected model text.
- [ ] 8.2 Add a sanitized canary report containing case keys, accepted/failure counts, fixed failure-code counts, runtime/call aggregates, processor identity, and reviewer decision.
- [ ] 8.3 Enforce advancement thresholds of at least eight improved accepted rewrites, zero accepted factual/status/actor/chronology/prediction errors, no degraded legacy page, and successful privacy/release validation.
- [ ] 8.4 Document side-by-side review, exact-candidate promotion, rollback, and the rule that model replacement requires separate approval if the ten-case threshold fails.
- [ ] 8.5 Run the protected publication-disabled ten-case canary, inspect every sanitized result against its active page and official-source meaning, and record the reviewed outcome.
- [ ] 8.6 Only after approval, promote the exact canary candidate and repeat the measured gate at 25 cases and then at an up-to-100-case selection ceiling.

## 9. Verification

- [ ] 9.1 Run focused SCOTUS brief, live-static, static-state, static-validation, activity, and workflow-policy tests.
- [ ] 9.2 Run the complete test suite, formatting, lint, type checks, OpenSpec validation, and public-repository/privacy checks.
- [ ] 9.3 Confirm a failed or partially completed run leaves private work cleaned, the prior live release intact, and only selected unfinished cases represented as pending.
