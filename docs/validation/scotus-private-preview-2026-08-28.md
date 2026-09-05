# SCOTUS Legal Briefs fixture-backed private preview

## Scope

Validation used only checked-in recorded Court HTML/XML fixtures, `tests/fixtures/scotus_scenarios.json`, generated PDF parser fixtures, and derived adversarial variants. No additional Court material was required. The bounded manifest is `tests/fixtures/scotus_validation_manifest.json`.

The corpus contains 20 reviewed publication candidates under the configured cap of 25. It covers a normal argument, consolidated dockets, reargument, revised transcripts, technical statutory/citation terminology, multiple advocates, anonymous speakers, hypothetical questions, concessions, disputed premises, requested dispositions, lower-court posture, minors, medical facts, sealed/redacted material, later orders, later opinions, corrections, and projection rollback.

## Seven-cycle preview

The complete deterministic path was replayed with a simulated clock for seven consecutive dated cycles, 2026-08-22 through 2026-08-28. This is an accelerated fixture-backed private preview, not a claim that a process ran unattended for seven wall-clock days. Every one of the 20 candidate profiles has an explicit checklist note in the manifest; source, parse, observation, approved-claim, brief, public-boundary, correction, and rollback assertions are executable tests.

Results:

- reviewed candidates: 20/20;
- page/line accuracy: 100%;
- supported speaker precision: 100%;
- citation precision: 100%;
- grounded factual elements: 100%;
- fixture issue-grouping precision/recall: 100%/100%;
- unsupported legal-status upgrades: 0;
- sensitive public leaks: 0;
- private-boundary leaks: 0;
- failed-cycle projection changes: 0.

These are measurements of the bounded fixture corpus, not estimates of all Supreme Court documents.

## Live discovery

No newly posted transcript exists in the recorded fixtures and the validation window does not establish that the Court calendar permitted one. In accordance with task 9.5, live discovery is explicitly **unvalidated** and transcript publication latency is **not measurable** from this corpus. This does not invalidate deterministic adapter behavior; it prevents launch authorization from being inferred from fixture replay.

## Launch result

At the time of this 2026-08-28 fixture report, safety gates passed but
`config/scotus.yaml` retained both `enabled: false` and `publication.enabled: false`.
The then-current launch evaluator verified that fail-closed state while live discovery
was unvalidated. This is historical evidence, not the repository's current launch
status; bounded production was owner-authorized on 2026-09-03. Failed validation still
leaves the last safe projection active.
