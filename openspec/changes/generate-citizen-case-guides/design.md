## Context

The live static pipeline already discovers official Supreme Court activity incrementally, compares validators and digests, downloads only unseen or changed documents, parses them in a transient private workspace, and extracts source-grounded observations. Argued cases then receive a model-written plain-English brief. Zero-session disposition cases currently bypass that writing step and copy UUID-sorted observations into positional headings. That shortcut produced a technically grounded but incoherent page for `26A124`.

Official source documents, parsed text, prompts, and raw model output must remain transient. Published prose must continue to resolve to approved claims and sanitized page/line provenance. Existing URLs and public JSON contracts must remain stable.

## Goals / Non-Goals

**Goals:**
- Keep the existing incremental discover/download/parse/analyze stages.
- Use one coherent citizen-guide writing contract for every case, including emergency and other disposition-only matters.
- Give disposition guides a stable reader-oriented structure covering subject, procedural path, legal issue, Court action, and reasoning.
- Keep lower-court actions, party requests, Court action, majority reasoning, and separate opinions distinct.
- Reject a response when grounded fragments are irrelevant to their assigned section or do not explain the operative result.
- Cause already-published deterministic disposition pages to be regenerated as corrected revisions.

**Non-Goals:**
- Persist or publish official source text, model prompts, or raw responses.
- Replace incremental document discovery or redownload unchanged documents.
- Change public case URLs or public JSON schemas.
- Add manual prose overrides for one named case.
- Predict final merits outcomes from an interim order.

## Decisions

### Restore a bounded writing pass for disposition-only cases

After approved claims are built from all available official documents, the pipeline will make the same budgeted, schema-constrained brief call used for argued cases. The disposition writer receives typed claim status and source labels and returns only citizen-facing prose with claim IDs. This keeps analysis separate from writing while allowing facts from different pages and documents to become one explanation.

Alternative: improve the deterministic fragment compiler. Rejected because deterministic code cannot infer a coherent narrative or safely paraphrase long operative orders without recreating a brittle legal-language engine.

### Use a fixed disposition editorial contract

Disposition responses will use exact, unique headings in this order: `What this case is about`, `Why this case reached the Court`, `The legal issue`, `What the Supreme Court did`, and `Why the Court did it`. `What separate opinions said` is added only when separately attributed concurrence or dissent material is available. Each section is validated against allowed observation types and legal statuses; a grounded claim cannot be placed under an unrelated heading merely because its words are valid.

Alternative: retain free headings and normalize them positionally. Rejected because that caused the incident and erased the model's intended semantics.

### Let the writer paraphrase the complete operative result under role-aware validation

The writer receives requested, lower-court, and Supreme Court action claims rather than having formal-action claims filtered out. The existing role-aware action validator ensures each generated action is supported by claims for the same actor. The disposition section must cite a typed Court holding/order, while the procedural-path section must cite docket, requested-relief, or lower-court claims. This allows a concise explanation such as a stay of a named injunction pending appeal instead of publishing an antecedent-dependent quote fragment.

Alternative: continue inserting the shortest source-exact Court-action sentence. Rejected because exact fragments can be accurate but unintelligible outside their original paragraph.

### Treat separate-opinion language as separate context

Claims explicitly formulated as a dissent, concurrence, or separate opinion may appear only under `What separate opinions said`. They cannot support `The legal issue`, `What the Supreme Court did`, or `Why the Court did it`. A future richer opinion-role parser can replace this conservative language marker without changing the public contract.

Alternative: add a database migration and new opinion-role enum now. Rejected as unnecessary for correcting the current assembly failure; conservative textual attribution is sufficient to prevent dissent-led majority summaries.

### Change processor and prompt fingerprints

The disposition prompt version and policy version will advance. Removing the deterministic compiler marker from the processor contract intentionally makes existing disposition-only prose stale. Normal incremental processing will reuse unchanged downloaded-document state where supported, analyze available documents, and publish corrected revisions only after all gates pass.

## Risks / Trade-offs

- [The local model may still fail the stricter contract] → Keep bounded retries with fixed validation codes and retain the last-known-good release rather than publishing a malformed guide.
- [Some sparse orders lack enough material for five useful sections] → Fail closed or omit only the separately-opinions section; do not fill sections with docket boilerplate.
- [Textual dissent detection can miss unusual labels] → Require explicit attribution before using separate-opinion material and never use marked separate-opinion claims as majority support.
- [Regeneration consumes an additional brief call] → Continue enforcing per-case, per-run, token, and runtime budgets and record sanitized receipts.

## Migration Plan

1. Add schema, prompt, and semantic validation tests using invented `26A124`-shaped evidence.
2. Route disposition-only candidates through the budgeted writer and remove the fragment compiler from production.
3. Run the full test and public-boundary suites.
4. Run publication-disabled live processing and inspect sanitized candidate JSON for `26A124` and representative sparse orders.
5. Deploy the exact retained candidate; the corrected guide becomes revision 2 at the same URL.
6. Roll back code or candidate promotion if validation fails; the current release remains the last-known-good fallback.

## Open Questions

None. The public correction can proceed without a source-retention, schema, or URL migration.
