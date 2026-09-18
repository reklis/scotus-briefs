## Context

The immutable archive contains 2,161 PDFs. Import preserved the files and opaque source UUIDs but assigned `unknown` document types and created 1,787 unresolved case records. The preserved import paths identify transcripts, opinions, and orders; embedded PDF text and metadata usually identify the docket, caption, dates, and term. Historical groups are imperfect containers: some contain unrelated dockets, while some dockets and hashes occur across groups. The site correctly excludes records without a docket and term.

Recovery must be deterministic, resumable, reviewable, and fail closed. Archived PDFs are primary sources and cannot be rewritten. Existing curated records and accepted guides must not be replaced by weaker inferred metadata.

## Goals / Non-Goals

**Goals:**
- Recover publishable case identity and metadata from archived Court PDFs with field-level provenance.
- Classify historical document types from preserved path plus content.
- Cluster at document/docket level, splitting polluted source groups and merging duplicate docket components.
- Reconcile recovered components with existing cases without duplicating PDFs or weakening existing metadata.
- Produce an atomic plan, conflict report, coverage report, and resumable workflow operation.
- Make successfully recovered records eligible for bounded evidence and guide backfills.

**Non-Goals:**
- Guess metadata for documents without unambiguous primary-source evidence.
- Rewrite PDFs, erase historical-group associations, or replace stronger curated metadata.
- Generate all historical guides in one unbounded run.
- Treat model output as authoritative case identity metadata.

## Decisions

### Parse documents before groups

Each PDF produces a versioned metadata candidate containing its hash, path-derived type, canonical docket set, caption/title, dates, term, parties, confidence, and warnings. Components are then built from exact docket overlap and shared hashes. Historical group UUIDs are retained as provenance but are not identity boundaries. This avoids propagating known polluted groups.

Alternative: resolve one case per historical group. Rejected because corpus inspection found groups containing multiple unrelated dockets and shared documents across groups.

### Use deterministic primary-source parsing

Recovery uses embedded PDF text and metadata only. Path-derived type is accepted when the preserved parent directory is `transcript`, `opinion`, or `order`; extracted content must remain readable. Dockets and dates use explicit Court labels. Titles prefer a docket-bearing embedded title, then an explicit opinion caption, then a transcript caption. Model inference is not used.

Alternative: ask the local language model to infer metadata. Rejected because identity must be reproducible and fail closed.

### Preserve stable public identities

Existing cases are matched first by canonical docket. New recovered cases use a term-qualified, docket-based ID and title-independent slug. Original-jurisdiction dockets use canonical `<number>O` notation while retaining source wording in aliases/provenance. Once written, IDs are not renamed by later title corrections.

### Require an apply plan

`recover --plan` writes a deterministic report without mutating canonical data. `recover --apply` validates the full plan and then atomically writes case files and the manifest under the existing manifest lock. Ambiguous candidates remain unresolved. Repeated application is idempotent.

### Resolve lifecycle conservatively

An opinion/order with an explicit decision date yields `decided`; a transcript with an explicit argument date and no qualifying disposition yields `argued`; weaker records remain `unresolved`. Term comes from an explicit opinion term or the Court term containing the argument/decision date, never merely from the docket prefix.

### Reuse existing bounded generation

Recovery is a distinct ingestion operation. It commits normalized metadata before extraction or generation. Subsequent bounded backfills select newly recovered cases through the existing orchestrator and retain all existing fail-closed guide acceptance behavior.

## Risks / Trade-offs

- **Caption layouts vary across decades** → Use multiple explicit parsers, confidence thresholds, golden fixtures, and retain unresolved records on conflict.
- **A docket can appear only in cited material rather than the case caption** → Restrict docket parsing to PDF metadata and the opening pages/header region with Court labels.
- **Consolidated cases can look like duplicates** → Preserve complete docket sets and merge only components with exact docket overlap supported by the same caption/document.
- **A recovery bug could relink many records** → Default to plan-only, validate corpus invariants, apply atomically, and preserve a mapping report suitable for rollback.
- **Transcript-only old cases lack a decision source** → Publish them as argued/source-limited rather than claiming a decision.
- **Backfill volume is expensive** → Continue small, resumable batches and commit after every validated batch.

## Migration Plan

1. Add docket, parsing, planning, reconciliation, validation, CLI, and workflow tests.
2. Generate a repository-wide plan and inspect conflicts and coverage without mutation.
3. Apply only high-confidence components and commit the recovery report and canonical metadata.
4. Run repository validation and a no-op second recovery to prove idempotence.
5. Dispatch small bounded guide backfills and monitor acceptance reports.
6. Leave unresolved/conflicting groups intact for later parser improvements or manual review.

Rollback is a normal Git revert of metadata/report commits; immutable PDF blobs remain untouched.

## Open Questions

- Records that expose only an original-jurisdiction docket and no reliable term will remain unresolved until an authoritative term can be extracted.
- Dismissal and denial orders require explicit disposition patterns; otherwise they remain unresolved rather than being forced into `dismissed`.
