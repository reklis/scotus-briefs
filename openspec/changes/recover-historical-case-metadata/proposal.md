## Why

The archive contains 2,145 documents in 1,787 opaque historical groups that the public site cannot browse because their dockets, titles, terms, dates, and document types were not recovered during import. The archived Supreme Court PDFs contain enough primary-source metadata to identify most records, but recovery must split polluted groups, preserve provenance, and leave ambiguous records unpublished rather than guessing.

## What Changes

- Add deterministic metadata extraction from archived transcript, opinion, and order PDFs, including docket numbers, captions, argument and decision dates, Supreme Court terms, parties, and document types.
- Add support for original-jurisdiction docket notation and historical docket variants.
- Build a reviewable recovery plan that clusters documents by canonical docket instead of assuming each opaque source group is one case.
- Reconcile recovered records with existing normalized cases and update case files and manifest associations atomically while preserving immutable PDFs and historical-group provenance.
- Retain unresolved records and conflict reports whenever identity or metadata is ambiguous.
- Expose recovery as a resumable CLI/workflow operation and use accepted recovered cases as inputs to bounded evidence and guide backfills.
- Add repository-wide integrity checks and corpus reports for duplicate dockets, dangling references, polluted groups, and recovery coverage.

## Capabilities

### New Capabilities
- `historical-case-recovery`: Deterministic, provenance-preserving recovery and reconciliation of normalized Supreme Court case metadata from archived primary-source PDFs.

### Modified Capabilities
- None.

## Impact

The change affects the Python docket parser, models, historical importer/reconciliation pipeline, CLI, publication workflow, validation, tests, and operations documentation. It updates normalized case and manifest metadata but never rewrites archived PDF blobs. Recovered cases become eligible for the existing evidence-generation and static-publication pipeline only after deterministic validation succeeds.
