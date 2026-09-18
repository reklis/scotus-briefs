# Guide generation quality gate

A candidate guide is published only when **all** deterministic checks and all adversarial
model checks pass. Rejection leaves the checked-in accepted guide unchanged and writes an
actionable report under `reports/validation/`.

## Deterministic acceptance

The validator requires schema validity, matching case identity and lifecycle, current
manifest-backed source documents, extractable cited pages, known supported evidence IDs,
complete provenance, attribution for allegations/positions/questions/separate opinions,
and a current holding source for a completed decision. Undecided cases must have an empty
`pending` decision. A justice's question cannot be described as a vote or settled view, and
a concurrence or dissent alone cannot support the Court's holding.

## Curated-case scoring thresholds

The adversarial verification prompt scores each candidate from 0 to 1. Initial launch
thresholds are deliberately fail-closed:

| Dimension | Minimum |
| --- | ---: |
| Factual accuracy | 0.90 |
| Neutrality | 0.80 |
| Readability for a general civic audience | 0.75 |
| Completeness relative to available sources | 0.75 |
| Traceability to supplied evidence | 0.95 |
| Restraint (no prediction or unsupported consequence) | 0.90 |

It must also return `true` for support, attribution, opinion-part distinction,
oral-argument characterization, and absence of overstatement. These thresholds are
versioned in `pipeline/scotus_guide/validation.py`; changing them requires an intentional
code review and regeneration report. Scores are retained in each case validation report.

Quality evaluation uses curated pending, argued, decided, consolidated, and application-
docket cases when those fixtures are available. No real LLM call is part of unit tests;
responses are mocked. Before launch, operators should inspect the accepted curated guides
against their cited primary-source pages and record any threshold adjustment in Git.

## Commands

```bash
# Stable end-to-end operator interface (aliases shown):
scotus-guide run --mode incremental --batch-size 25
scotus-guide run --mode backfill --batch-size 10
scotus-guide run --mode case --batch-size 1 --case-id scotus-24-7
scotus-guide run --mode validate --batch-size 1

# Workflow-compatible staged interface:
scotus-guide extract --mode bounded-backfill --batch-size 10
scotus-guide generate --mode bounded-backfill --batch-size 10
scotus-guide validate
scotus-guide health
```

Set `SCOTUS_OCR_COMMAND` to an argv-style command template whose stdout is one page's OCR
text. `{input}` and `{page}` are replaced without a shell, for example:

```bash
export SCOTUS_OCR_COMMAND='my-pdf-ocr --input {input} --page {page}'
```

With no OCR command, unusable pages are explicitly recorded as unavailable and can never
support evidence.
