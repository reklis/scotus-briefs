## Why

A source-grounded page can still fail readers when independently valid fragments are selected arbitrarily and placed under headings they do not answer. Every published case needs one coherent citizen-facing guide built from all newly available official case documents, not a positional compilation of whichever extracted claims sort first.

## What Changes

- Preserve incremental discovery and download only unseen or changed official docket, transcript, order, and opinion documents.
- Parse and analyze the complete available official case record before writing public prose.
- Generate disposition-only and argued-case pages through a coherent plain-English citizen-guide contract rather than the deterministic fragment compiler.
- Require a fixed editorial structure whose sections answer distinct reader questions and whose outcome identifies the operative relief and procedural effect.
- Distinguish Court reasoning and action from lower-court decisions, party requests, and separate opinions.
- Reject guides that are grounded sentence-by-sentence but incomplete, irrelevant to their headings, dissent-led, or operationally ambiguous.
- Add regression coverage for `Trump v. California`, docket `26A124`, including its executive-order background, standing/ripeness issue, procedural path, stay, and separate dissents.

## Capabilities

### New Capabilities
- `citizen-case-guide-generation`: Incremental official-document processing and coherent, source-grounded plain-English generation for a complete Supreme Court case guide.

### Modified Capabilities

None.

## Impact

The change affects SCOTUS live static processing, legal extraction and brief-generation contracts, validation, model budgets/fingerprints, generated public case prose, and pipeline tests. Public URLs and sanitized JSON schemas remain compatible; corrected cases publish as new revisions at their existing URLs.
