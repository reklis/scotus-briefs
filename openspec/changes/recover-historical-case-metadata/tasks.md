## 1. Parsing contracts

- [x] 1.1 Extend docket normalization and contracts for soft-hyphenated and original-jurisdiction dockets
- [x] 1.2 Add versioned historical document candidate, recovery component, plan, conflict, and report models
- [x] 1.3 Add deterministic import-path document type classification

## 2. Primary-source metadata extraction

- [x] 2.1 Extract opening-page text and embedded PDF title metadata with bounded resource use
- [x] 2.2 Parse standard, application, consolidated, and original-jurisdiction docket labels
- [x] 2.3 Parse transcript captions, parties, oral-argument dates, and Court terms
- [x] 2.4 Parse opinion captions, explicit terms, argument dates, decision dates, and lifecycle
- [x] 2.5 Parse supported order identity and disposition metadata while rejecting ambiguity
- [x] 2.6 Add golden parsing tests for historical layout and Unicode variants

## 3. Recovery planning and reconciliation

- [x] 3.1 Build document-level components from exact docket overlap and shared hashes
- [x] 3.2 Split polluted historical groups and merge cross-group docket components deterministically
- [x] 3.3 Match recovered components to existing cases by canonical docket before creating stable IDs
- [x] 3.4 Resolve fields conservatively with provenance and stronger-existing-metadata precedence
- [x] 3.5 Generate deterministic plan, coverage, split, merge, and conflict reports
- [x] 3.6 Validate duplicate dockets, hashes, associations, IDs, and plan preconditions
- [x] 3.7 Apply valid plans atomically and idempotently while retaining historical associations
- [x] 3.8 Add reconciliation, conflict, curated-merge, and idempotence tests

## 4. Operations and publication

- [x] 4.1 Add plan/apply historical recovery commands to the Python CLI
- [x] 4.2 Add a serialized recovery workflow operation with source-state commit boundaries
- [x] 4.3 Extend repository validation for recovered-case referential integrity
- [x] 4.4 Document recovery review, application, rollback, and bounded guide backfill procedures

## 5. Corpus migration and backfill

- [x] 5.1 Generate and review a full-corpus plan, retaining ambiguous records as unresolved
- [x] 5.2 Apply the validated high-confidence recovery plan and prove a second run is a no-op
- [x] 5.3 Run lint, type checking, tests, repository validation, and strict OpenSpec validation
- [ ] 5.4 Commit and push recovered metadata in Git-manageable validated batches
- [ ] 5.5 Dispatch and monitor small resumable guide-backfill batches for recovered cases
