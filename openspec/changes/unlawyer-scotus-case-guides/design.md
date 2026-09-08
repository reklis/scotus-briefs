## Context

The existing pipeline correctly keeps source retrieval, parsing, observations, approved claims, model responses, and public projection separate. Its writing boundary is wrong for the available local model. Argued-case generation receives a large claim ledger and must simultaneously select facts, preserve legal roles, organize a whole guide, translate legal language, emit citations, and satisfy a strict schema. On correction it usually receives only a fixed code and regenerates the whole draft.

Run `34147702102` tested the prompt-only plain-language revision against a maximum of 100 recent cases. The five-hour budget ended after 62 attempts. No existing public brief received accepted new prose. Repeated failures involved unexplained terms, action-role ambiguity, omitted context, excessive length, and empty output. The only new page admitted by the run still contained terms such as `waiver` and `pretext` and made unsupported statements about what the Court would do. The run also placed all 1,710 legacy cases into budget-pending state even though most were never selected.

Official documents and all derived private material must remain transient. A failed rewrite must leave the prior public case unchanged. Public URLs and JSON schemas must remain stable. The configured loopback Ollama model remains the initial writer; using a hosted or larger model requires separate review of cost, privacy, operations, and model identity.

## Goals / Non-Goals

**Goals:**
- Give the LLM one bounded editorial job: translate selected, already-approved case facts into useful everyday prose.
- Keep fact selection, section purpose, legal role, chronology, action identity, status, citations, and publication eligibility deterministic.
- Make corrections local to one rejected field and give the writer actionable ephemeral feedback.
- Reject unexplained courtroom language, process commentary, unsupported absence statements, and predictions before publication.
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

### Repair only the rejected field

Validation will return a private structured diagnostic containing the field path, fixed public-safe code, offending term or rule, and a concrete required transformation. During the same run, a repair call receives only the failed paragraph or heading, its section packet, and that diagnostic. Valid fields from the prior draft remain unchanged in memory. The repaired field must pass all grounding, legal-role, status, privacy, reader-language, and length checks again.

Rejected text and detailed diagnostics are transient and must not enter logs, receipts, pending state, artifacts, or generated content. Persisted failures retain only fixed codes and opaque retry scope data. Per-field and per-case call limits remain configured and budgeted.

Alternative: deterministic replacement of every legal term. Rejected because terms such as `standing`, `stay`, and `jurisdiction` have case-specific meaning; blind substitution can change the law. Deterministic code supplies guidance and validates, while the LLM performs the rewrite.

### Move reader-language policy into a versioned resource

A reviewed resource will define lawyer-facing terms and phrases, ordinary alternatives, and patterns that count as an immediate explanation. It will cover both doctrine and procedure, including terms observed in the public corpus such as standing, jurisdiction, injunction, vacatur, remand, mootness, preemption, habeas, sovereign immunity, tolling, due process, equal protection, scrutiny, certiorari, domicile, waiver, pretext, rebuttal, finality, and procedural history.

The reader-prose validator will apply to every new or changed title, summary, section paragraph, and argument paragraph. It will combine the resource with existing sentence/paragraph limits, repeated-fragment checks, and a deterministic readability bound. Headings and titles cannot rely on an inline definition and therefore must use ordinary wording, except an exact official caption.

A separate future-language rule will reject unsupported statements that the Court has not ruled and statements about what the Court will decide, clarify, establish, affect, or do next. Process language such as `approved record`, `claim`, `model`, and `schema` remains forbidden. Central constitutional or statutory names may remain when the same sentence explains their practical role.

Alternative: add terms directly to one regular expression after each failure. Rejected because it is hard to review, gives no rewrite guidance, and produced gaps such as `waiver` and `pretext` in the canary output.

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

Rollout begins with a publication-disabled fixed 10-case newest-first canary containing disposition-only, argued, and decided-after-argument cases where available. Every accepted rewrite must pass automated gates and manual side-by-side review. At least eight cases must produce accepted improved prose, no accepted case may contain a factual, actor, status, chronology, or prediction error, and no legacy page may be made worse. The exact case set, accepted count, failure-code counts, runtime, model calls, and reviewer decision are recorded using sanitized identifiers and aggregates.

Only a reviewed canary may advance the cursor stage to 25; only a reviewed 25-case run may advance it to at most 100. `100` is a selection ceiling, not permission to exceed source, model, token, or runtime budgets. If the loopback model cannot meet the 10-case threshold after the compact planner and targeted repair are implemented, rollout stops and model replacement becomes a separate reviewed change.

## Risks / Trade-offs

- [Compact packets omit nuance] → Require deterministic role/type coverage, include source-backed action slots, and fail closed rather than fill a section.
- [The terminology resource rejects legitimate ordinary usage] → Use phrase-level patterns, contextual explanations, corpus fixtures, and reviewed exceptions rather than isolated-word bans.
- [Repair changes a fact while fixing style] → Revalidate the repaired field against the unchanged section packet and action slots.
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
6. Run a publication-disabled 10-case canary and manually compare each candidate with its active page and official-source meaning.
7. If thresholds pass, deploy only the exact retained candidate, then run reviewed 25-case and up-to-100-case stages. Otherwise leave the live release unchanged and open a separate model-qualification change.
8. Rollback uses the prior immutable generated-content release and processor version. Backfill cursor fields are optional with fail-closed defaults so old state remains readable.

## Open Questions

None. Provider replacement is deliberately deferred unless the canary proves the current reviewed runtime cannot satisfy this smaller task.
