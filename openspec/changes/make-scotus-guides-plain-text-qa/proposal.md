## Why

The current Citizen’s Guide pipeline asks a local model to first encode Court documents into a detailed legal-observation schema and then translate planner-selected JSON packets into prose. That indirection produces brittle extraction failures, unnatural answers, and prompts optimized for machine validation rather than helping an ordinary person understand the case.

## What Changes

- **BREAKING**: Replace structured legal-observation extraction and strict-JSON Guide writing in the Citizen’s Guide path with direct question-and-answer generation from bounded official Court text.
- Ask one simple, high-level citizen question per model call and accept one nonblank plain-text answer rather than model-generated JSON.
- Use deterministic questions and headings for what the case is about, what each side wants, what has happened, what the Court decided or is being asked to decide, and why the case may matter.
- Select and bound official source text deterministically, including a chunk-and-synthesize path when the relevant documents cannot fit in one reviewed context window.
- Keep answers short, explain unavoidable legal terms in ordinary words, and say when the supplied material does not answer a question rather than guessing.
- Preserve first answers unchanged for candidate-bound manual review; do not add automated prose scoring, semantic acceptance, or repair.
- Retain exact local-model identity, source authorization, private-evidence handling, budgets, cleanup, privacy scanning, deterministic metadata, publication-disabled generation, release validation, and explicit approval.

## Capabilities

### New Capabilities
- `scotus-plain-text-guide-qa`: Direct plain-text answers to deterministic citizen questions from bounded official Court materials, with manual review and no structured model-output dependency.

### Modified Capabilities

None. The earlier unarchived `scotus-manual-guide-review` and Citizen’s Guide planning changes are superseded for Guide generation by this new capability; they are not main capabilities under `openspec/specs/` and therefore cannot receive a main-spec delta before archival.

## Impact

This changes the Citizen’s Guide source-packet builder, model request and response contracts, live static case processor, processor fingerprints, model-call budgeting, review-candidate assembly, tests, canary protocol, and operations/model documentation. Existing structured extraction may remain available for non-Guide legal-analysis uses, but it will no longer block or supply Citizen’s Guide generation. This change supersedes prior unarchived requirements that made approved claims, action-slot IDs, planner packets, or strict Guide JSON prerequisites for Citizen’s Guides. Public identity, headings, source links, status, release integrity, and publication authorization remain deterministic.
