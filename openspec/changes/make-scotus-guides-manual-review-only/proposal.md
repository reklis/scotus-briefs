## Why

GPT-OSS is returning strict-schema Citizen’s Guides, but automated lexical grounding and prose-quality gates reject ordinary-language paraphrases before a person can inspect them. The review workflow needs to expose every schema-valid guide for explicit manual judgment instead of treating heuristic prose checks as factual acceptance authorities.

## What Changes

- **BREAKING**: Remove automated grounding, action-language, style, jargon, sentence, word-count, and repair gates from Citizen’s Guide acceptance.
- Retain strict final-content JSON schema parsing and deterministic assembly of headings, order, claim IDs, action-slot IDs, citations, status, and case identity.
- Retain exact local model identity, source authorization, private-evidence handling, cleanup, privacy scanning, publication-disabled operation, and explicit candidate-bound approval.
- Stop automatic field repair; preserve the model’s first schema-valid Guide unchanged for manual review.
- Classify schema-valid generated Guides as review candidates, not factually accepted or publication-ready output.
- Require a human reviewer to assess correctness, grounding, actor/action roles, readability, omissions, and improvement before any explicit promotion decision.

## Capabilities

### New Capabilities
- `scotus-manual-guide-review`: Publication-disabled generation and retention of schema-valid Citizen’s Guides for manual review without automated prose acceptance or repair.

### Modified Capabilities

None.

## Impact

This affects Citizen’s Guide parsing, live static case processing, brief assembly, canary accounting terminology, manual-review artifacts, tests, and qualification documentation. It does not change source retrieval, model identity, privacy, release integrity, or publication authorization controls.
