## Why

Disposition-only Supreme Court cases are discovered correctly but repeatedly remain unpublished because the local Ollama model is asked to produce free-form prose that satisfies brittle paragraph-level lexical validators. The current correction and receipt behavior can then leave failed cases permanently pending even though Court metadata and grounded observations are valid.

## What Changes

- Replace the broad argument-oriented disposition prompt with a small, positive, disposition-specific instruction set and schema.
- Make Court-action validation role-aware so lower-court actions, requested relief, and Supreme Court dispositions are checked against the corresponding approved claims.
- Reject actual invented argument content without rejecting truthful negated statements, while avoiding forbidden-term priming in the disposition prompt.
- Preserve deterministic caption, docket, publication date, status, and Court-action facts outside model discretion.
- Introduce bounded, auditable nightly retry generations for failed local-model inputs without unbounded duplicate calls.
- Add regression fixtures for `26A274`, `26A203`, and `26A124`, then validate a small retained candidate before draining the backlog.

## Capabilities

### New Capabilities
- `ollama-disposition-generation`: Grounded, bounded, retryable local-model generation for Supreme Court cases that have an official disposition but no oral-argument session.

### Modified Capabilities

None.

## Impact

The change affects `src/ragchew/scotus/briefs.py`, `src/ragchew/scotus/live_static.py`, model-attempt accounting and fingerprints in the static pipeline, `config/scotus.yaml`, disposition-focused tests, and the Pages operations runbook. It does not add production services, publish raw source/model material, or change the static-only deployment architecture.
