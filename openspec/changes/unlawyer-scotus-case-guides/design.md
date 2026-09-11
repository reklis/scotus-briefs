## Context

The existing pipeline correctly keeps source retrieval, parsing, observations, approved claims, model responses, and public projection separate. Its writing boundary is wrong for the available local model. Argued-case generation receives a large claim ledger and must simultaneously select facts, preserve legal roles, organize a whole guide, translate legal language, emit citations, and satisfy a strict schema. On correction it usually receives only a fixed code and regenerates the whole draft.

Run `34147702102` tested the prompt-only plain-language revision against a maximum of 100 recent cases. The five-hour budget ended after 62 attempts. No existing public brief received accepted new prose. Repeated failures involved unexplained terms, action-role ambiguity, omitted context, excessive length, and empty output. The only new page admitted by the run still contained terms such as `waiver` and `pretext` and made unsupported statements about what the Court would do. The run also placed all 1,710 legacy cases into budget-pending state even though most were never selected.

Protected publication-disabled canary `34471623129` then exercised the compact writer and bounded repair policy against ten cases. It accepted no rewrite. Six cases reached writing but ended in `brief_validation_failed` or `repair_exhausted`; three lacked deterministically required planning support; and one had invalid source collection. In the writing failures, a preferred sentence-length violation consumed repair capacity, two style repairs subsequently changed a supported requested or lower-court action, an unchanged style repair discarded an otherwise reviewable field, and the process-language resource treated ordinary legal-reporting use of `claim` as internal commentary. The all-failed batch also retained no sanitized manifest or aggregate candidate state. This evidence shows that correctness gates remain necessary but editorial preferences and false-positive heuristics must not all behave as fatal correctness errors.

Official documents and all derived private material must remain transient. A failed rewrite must leave the prior public case unchanged. Public URLs and JSON schemas must remain stable. The configured loopback Ollama model remains the initial writer; using a hosted or larger model requires separate review of cost, privacy, operations, and model identity.

## Goals / Non-Goals

**Goals:**
- Give the LLM one bounded editorial job: translate selected, already-approved case facts into useful everyday prose.
- Keep fact selection, section purpose, legal role, chronology, action identity, status, citations, and publication eligibility deterministic.
- Make corrections local to one rejected field and give the writer actionable ephemeral feedback.
- Reject unsupported facts, legal actions, status, chronology, predictions, private processing disclosures, and severe output violations while preserving hard-valid prose with bounded editorial warnings for measured review.
- Prevent style-only repair from replacing or discarding an accurate hard-valid field when the repair is unchanged or changes supported meaning.
- Regenerate prose from an official opinion or order before publishing a status transition.
- Backfill only a bounded newest-first slice and measure quality before increasing throughput.

**Non-Goals:**
- Use an LLM to decide what the Court held, infer a vote, choose a winner, or supply missing facts.
- Persist source text, prompts, model output, rejected prose, or private repair diagnostics.
- Rewrite immutable historical revision bodies or change public URLs/public JSON schemas.
- Guarantee 100 completed cases when source, model, token, or runtime budgets stop earlier.
- Switch model providers as part of this change.

## Decisions

### Plan the reader guide before calling the writer

A deterministic planner will convert approved claims into a `ReaderGuidePlan`. Each section packet contains an exact heading and purpose, allowed and required observation types/legal statuses, relevant claim IDs and public values, actor/action slots when applicable, and plain-language guidance for legal terms found in that packet. Argument packets remain tied to one real argument session and include bounded representative claims for each established side and justice-question coverage.

The planner will enforce per-section claim and character bounds. It will prefer the strongest nonduplicative claims needed to satisfy the existing coverage contract rather than forwarding the complete case ledger. It will fail closed when a required section cannot be supported. Caption, docket, dates, status, official links, section order, argument order, and citations are not model choices.

The writer will normally receive all compact section packets in one schema-constrained call so it can maintain coherence without multiplying calls. It returns prose and the provided claim IDs. It is instructed to preserve case-specific meaning while replacing courtroom shorthand with ordinary words; a precise legal term may remain only with an immediate case-specific explanation.

Alternative: one initial model call per section. Rejected because the 27B loopback model is sequential and the five-hour run already exhausted runtime. Section-level calls are reserved for targeted repair.

Alternative: continue sending the whole ledger with a stronger prompt. Rejected by the measured run: prompt growth increased constraints without making the task tractable.

### Repair only the rejected field without sacrificing a valid original

Validation returns a private structured diagnostic containing the field path, fixed public-safe code, offending term or rule, and a concrete required transformation. During the same run, a repair call receives only the failed or warned paragraph or heading, its section packet, and that diagnostic. Valid fields from the prior draft remain unchanged in memory.

Hard correctness is evaluated before editorial quality. A repair for a hard correctness failure replaces the field only after all grounding, legal-role, action, status, chronology, prediction, privacy, and severe-bound checks pass. A repair for an editorial warning follows the same checks, but an unchanged, invalid, or meaning-changing repair is discarded and the original hard-valid field remains available with its warning. Repair exhaustion rejects a draft only while a hard correctness failure remains.

Rejected text and detailed diagnostics are transient and must not enter logs, receipts, pending state, artifacts, or generated content. Persisted failures and warnings retain only fixed codes and opaque scope or aggregate data. Per-field and per-case call limits remain configured and budgeted.

Alternative: accept a stylistic repair before rerunning semantic validation. Rejected by canary `34471623129`, where shortening prose introduced unsupported action language.

Alternative: deterministic replacement of every legal term. Rejected because terms such as `standing`, `stay`, and `jurisdiction` have case-specific meaning; blind substitution can change the law. Deterministic code supplies guidance and validates, while the LLM performs the rewrite.

### Separate hard correctness from versioned editorial warnings

A reviewed resource defines lawyer-facing terms and phrases, ordinary alternatives, patterns that count as an immediate explanation, actual process-disclosure patterns, preferred style targets, and severe bounds. It covers both doctrine and procedure, including terms observed in the public corpus such as standing, jurisdiction, injunction, vacatur, remand, mootness, preemption, habeas, sovereign immunity, tolling, due process, equal protection, scrutiny, certiorari, domicile, waiver, pretext, rebuttal, finality, and procedural history.

Validation proceeds in two tiers:

```text
model field -> hard correctness/privacy/source checks -> editorial warning checks
                    | reject                         | retain + optional repair
                    v                                v
             last-known-good page             measured candidate review
```

Hard checks cover grounding, required support, legal role, canonical action, object, polarity, interim/final effect, status, chronology, unsupported absence or future claims, privacy, actual pipeline disclosures, empty fields, and severe configured size bounds. Editorial checks cover the preferred sentence target, deterministic readability, repetition, section focus, lawyer-facing phrases, and legal terms that lack a useful same-sentence explanation. These checks produce fixed warning codes and do not by themselves discard a hard-valid measured candidate.

The sentence target and hard maximum are separate configuration values. A sentence just over the preferred target remains reviewable; only an extreme configured violation is fatal. Likewise, `approved claim packet`, `claim_id`, `model`, `prompt`, and `schema` are actual process language, while ordinary statements that a party `claims` or disputes something are not. Headings and titles still cannot rely on an inline definition, except for an exact official caption.

A separate future-language rule continues to reject unsupported statements that the Court has not ruled and statements about what the Court will decide, clarify, establish, affect, or do next. Central constitutional or statutory names may remain, with missing practical explanation represented as an editorial warning when the underlying statement is otherwise grounded.

Alternative: keep every reader-language preference fatal. Rejected because it prevents the measured canary from retaining accurate candidates for the manual improved/degraded comparison and encourages risky semantic rewrites solely to satisfy style metrics.

Alternative: remove deterministic editorial analysis entirely. Rejected because fixed warnings make review consistent, expose regressions without private text, and guide bounded repairs.

### Preserve semantic actions while allowing ordinary verbs

The planner will provide typed action slots: actor role, canonical action, operative object, negation, and timing/effect. The action validator will compare generated prose to these slots rather than require Court vocabulary. Reviewed equivalents such as `cancelled` for `vacated`, `sent the case back` for `remanded`, and `temporarily paused` for `stayed` map to the same canonical action. Every action sentence must still name the party, lower court, or Supreme Court actor.

This keeps legal status deterministic while allowing the writer to remove legalese. Unsupported actor swaps, objects, polarity, or finality continue to fail closed.

### Make status transitions atomic with prose

The metadata-only disposition shortcut will not change an argued case to `ordered` or `decided`. A new or revised official order/opinion must be downloaded, parsed, extracted, planned, written, and validated with all current argument sessions before status, maturity, disposition metadata, and prose advance together. If any stage fails, the complete prior case remains active and the new activity is represented only as sanitized pending work.

Metadata-only reuse remains allowed for a corrected official URL or date that does not change legal status, maturity, or the meaning of public prose.

### Use a resumable bounded editorial backfill

A versioned `EditorialBackfillState` in sanitized publication state will identify the target processor fingerprint, rollout stage, newest-first rank boundary, and aggregate attempted/accepted/failed counts. Discovery will select no more than the current stage limit after fresh source changes and eligible retries. Cases beyond that selected slice are not added to `PendingWork`; they remain discoverable through the cursor for the next accepted cycle.

Accepted cases acquire the target processor fingerprint. Failed cases retain a normal bounded retry scope but do not prevent the cursor from reaching older cases. A processor change starts a new backfill identity without modifying historical revisions. State and candidate validation will reject inconsistent cursor counts, unknown fields, or a cursor that skips an unaccounted selected case.

Alternative: place every mismatched legacy pointer into the work queue and let the runtime budget defer the rest. Rejected because the dry run converted almost the complete public corpus into misleading `budget_exhausted` pending work.

### Require a measured canary before scaling

Rollout begins with a publication-disabled fixed 10-case newest-first canary containing disposition-only, argued, and decided-after-argument cases where available. Every reviewable rewrite must pass hard automated gates and manual side-by-side review. Editorial warnings remain attached as fixed sanitized counts and inform whether the candidate is improved or degraded; they do not substitute for that judgment. At least eight cases must produce accepted improved prose, no accepted case may contain a factual, actor, status, chronology, or prediction error, and no legacy page may be made worse. The exact case set, accepted count, failure- and warning-code counts, runtime, model calls, and reviewer decision are recorded using sanitized identifiers and aggregates.

A canary that has no hard-valid rewrite still emits a privacy-scanned sanitized manifest and aggregate report. It contains no rejected prose or private source material, cannot be promoted, and permits the operator to record a complete rejected decision rather than relying on partial workflow logs.

Only a reviewed canary may advance the cursor stage to 25; only a reviewed 25-case run may advance it to at most 100. `100` is a selection ceiling, not permission to exceed source, model, token, or runtime budgets. Canary `34471623129` is the rejected baseline for this calibration; implementation requires a fresh processor identity and a new ten-case canary. If the recalibrated policy still cannot meet the threshold, rollout stops and model replacement becomes a separate reviewed change.

## Risks / Trade-offs

- [Compact packets omit nuance] → Require deterministic role/type coverage, include source-backed action slots, and fail closed rather than fill a section.
- [The terminology resource rejects legitimate ordinary usage] → Use phrase-level patterns, contextual explanations, corpus fixtures, reviewed exceptions, and nonfatal warning codes rather than isolated-word bans.
- [Warnings admit materially worse prose] → Keep factual and severe bounds hard, expose fixed warning counts, and require measured side-by-side review to classify every retained canary rewrite as improved or degraded.
- [Repair changes a fact while fixing style] → Revalidate the repaired field against the unchanged section packet and action slots, discard the repair, and retain the original hard-valid field with its warning.
- [A failed newest case starves migration] → Advance the backfill cursor after a recorded attempt and handle failures through the existing least-recently-attempted retry queue.
- [Legacy re-extraction remains slow] → Scale only after measured canaries and retain all current request, byte, call, and runtime limits.
- [Old legalese remains visible during gradual migration] → Keep stable pages available, label no stale page as newly rewritten, and prefer safe gradual correction over an unvalidated bulk replacement.
- [Adding private diagnostics leaks model text] → Keep detailed repair objects process-local; scan persisted state, logs, receipts, and artifacts against the existing public allowlists.

## Migration Plan

1. Restore safe queue behavior so missing legacy fingerprints do not enqueue the complete corpus before the new backfill state exists. Keep the failed 100-case candidate undeployed.
2. Add the section-plan, action-slot, terminology-resource, repair, backfill-state, and canary contracts with unit and privacy tests.
3. Implement compact whole-guide writing and field-level repair for both argued and disposition-only cases; advance processor, prompt, policy, and resource fingerprints.
4. Remove status-changing metadata-only publication and require opinion-aware regeneration.
5. Run fixture and invented-case tests, the full suite, typing/linting, static privacy validation, and fault tests.
6. Retain rejected canary `34471623129` as the sanitized baseline. Split hard correctness from editorial warnings, narrow process-language matching, add preferred and severe size bounds, preserve original hard-valid fields after failed style repairs, and retain all-failed measured reports.
7. Run regression tests shaped like every sanitized `34471623129` failure, then advance the validation-policy and processor identities.
8. Run a fresh publication-disabled 10-case canary and manually compare each hard-valid candidate, including its warnings, with its active page and official-source meaning.
9. If thresholds pass, deploy only the exact retained candidate, then run reviewed 25-case and up-to-100-case stages. Otherwise leave the live release unchanged and open a separate model-qualification change.
10. Rollback uses the prior immutable generated-content release and processor version. Backfill cursor fields are optional with fail-closed defaults so old state remains readable.

## Open Questions

None. Provider replacement is deliberately deferred unless the canary proves the current reviewed runtime cannot satisfy this smaller task.
