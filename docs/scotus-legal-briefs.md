# SCOTUS Legal Briefs architecture

## Static-only production

GitHub Pages is the only production reader runtime. The exported tree contains the
landing page, SCOTUS/archive/case/correction/search pages, minimal search data,
sanitary public JSON, local fingerprinted assets, sitemap/robots/404 files, and a
content-derived release marker. All internal URLs derive from canonical origin
`https://scotusbriefs.us`, root project path `/`, and section path `/scotus/`. The
root `CNAME` contains exactly `scotusbriefs.us` plus one newline, is covered by the
release integrity manifest, and is validated against the canonical origin. No page
calls a runtime API.

The old FastAPI projection reader, PostgreSQL views, MinIO objects, and Kubernetes
workloads are legacy migration/local-test code. They are not deployed to readers.

## Nightly changed-case processing

Daily at 03:17 UTC (`17 3 * * *`) a protected, serialized, non-cancelling workflow:

1. reads the validated public state from `generated-content` and reconciles its active
   release ID with Pages;
2. independently and conditionally polls the active term's official slip-opinion index,
   checks current/recent argument and correction resources, and checks a bounded
   rotating historical slice using a descriptive GitHub project user agent;
3. builds one queue before applying limits: fresh Court changes first; unattempted
   fresh work is authoritative-date newest-first, while persisted retries use
   least-recently-attempted rotation before date to prevent starvation; processor/
   current rechecks and rotating historical work follow with stable tie-breaks;
4. selects work under request, byte, document, case, disk, runtime, model-call/token,
   and zero-local-cost limits, then downloads required official documents into a
   mode-0700 runner workspace;
5. fully recomputes each changed case, requiring complete transcripts for every real
   argument session but allowing a grounded dated disposition to support a zero-session
   case when the complete disposition and required docket metadata pass validation;
6. merges only complete accepted cases while preserving unchanged case bytes, records
   every failed or unselected supported activity as sanitized pending work, and
   continues after case-local failures while shared budgets permit;
7. exports and validates a fresh candidate, deploys that exact Pages artifact, then
   compare-and-swap promotes the exact generated state.

No Docker services run in publication. Court documents and model material stay only
in the self-hosted runner's mode-0700 workspace. Cleanup runs before and after each
build, including after failure. Deploy, receipt, and promotion jobs remain
GitHub-hosted and receive no Court/model/database/object credentials. A
no-content-change run skips Pages deployment and may advance only validated discovery
checkpoints and pending metadata. A global safety failure leaves the previous release
untouched; a case-local failure does not prevent an unrelated complete validated case
from being deployed.

## Supported activity and public ordering

Initial independent disposition scope is every strictly parsed individual row on the
configured active term's official `/opinions/slipopinion/{two-digit-term}` table: signed
opinions,
per-curiam dispositions, and decrees, including emergency `A` dockets, consolidated
dockets, original publication dates, and listed revision dates. Exact normalized docket
identity joins these rows to argued cases; a row can also create a disposition-only case.
A missing row in a later poll never means deletion or retraction. The
`relatingtoorders` index remains an exact-docket related-document source for known
argued cases. The system does **not** inspect an omnibus order list to create a case for
each listed matter, and it does not expand lower-court documents or every certiorari
denial into public cases.

Each current public case exposes `latest_court_document_date`, recomputed as the maximum
of its real argument dates, disposition publication dates, and disposition revision
dates. Retrieval, HTTP, processing, build, article, and model timestamps are excluded.
An explicitly migrated URL-only legacy disposition uses its latest real argument date
until an exact reviewed index match supplies the disposition date; the migration never
guesses. Contract and release validation reject inconsistent derived dates.

The shared case ordering is descending latest Court document date, followed by stable
term/docket/slug tie-breakers. The exporter supplies that exact order to the root and
SCOTUS home pages, free-text and unfiltered search, general case listings, term/status/
topic/corrections archives, and every paginated form. Browser-side filtering preserves
the generated relative order. Argument-date archives still select sessions by argument
date, but their matching cases are ordered by latest overall Court activity. Arguments
and revision history within an individual case remain chronological.

## Public generated state

The orphan branch layout is:

```text
snapshot/v1/projection.json
snapshot/v1/cases/<case-key>/revisions/<n>.json
state/v1/publication.json
state/v1/cost-ledger.json
release/v1/release.json
```

Case identity is stable term plus normalized docket; accepted case revisions are
append-only. Canonical JSON is UTF-8, sorted, schema-versioned, UTC-normalized, and
newline-terminated. Public state may retain official URLs, official activity dates,
HTTP validators, content digests/counts, bounded cursors, processor fingerprints,
release pointers, sanitized pending reasons, freshness outcomes, and opaque
model-attempt receipts with zero local cost.

`state/v1/publication.json` records a recomputable freshness summary with case counts
and newest official activity dates for discovered, published, deferred, failed, and
combined pending work. Promotion validation requires every supported discovery to be
current in the projection or represented by dated pending metadata, and rejects a
summary that disagrees with those allowlisted records. This exposes finite-budget or
failure staleness without adding captions, source bodies, prompts, rejected prose, or
model payloads to the freshness record or operational summary.

It must not retain Court/source bodies, PDFs/media, extracted transcript text,
evidence windows, observations, claims/claim IDs, prompts, model output, object keys,
credentials, stack traces, or private/internal IDs. Structural contracts and a
whole-bundle textual scan enforce this boundary before any upload.

## Evidence and generation

Only reviewed `www.supremecourt.gov` methods are eligible. A transcript establishes
attributed speech, not a vote or holding. Only an official order/opinion can establish
a Court disposition. An argued case keeps every real session and its complete
transcript requirement; a disposition-only page omits argument dates, analyses, and
transcript links rather than fabricating them. Changed source bytes allocate a new
logical document revision; one missing or malformed response never retracts an
existing brief.

Model inputs are bounded evidence windows for extraction and sanitized approved
claims for brief generation. Calls use the exact local Ollama model `qwen3.8:27b` at
the typed loopback-only `http://127.0.0.1:11434/v1` endpoint. The OpenAI SDK is only
an Ollama-compatible transport with a non-secret placeholder key; JSON-schema chat
completions remain mandatory. Exact model inventory is verified before evidence or
completion traffic. Explicit timeout/retry limits and one shared attempted-call
ledger remain in force, while all configured rates and maximum estimated cost are
zero. Unchanged source/parser/provider/endpoint/model/prompt/config fingerprints
cannot buy another attempt without an authorized replay. The workflow logs only case
keys, stages, coarse outcomes, counts, digests, and timings.

## Development and migration

Fixture preview uses only explicitly synthetic files under `tests/fixtures/`. The
operator-only legacy exporter reads retained *public projections* from PostgreSQL,
removes the old provenance claim-ID field, requires complete public revision history,
and emits a fresh scanned generated-content candidate. It never queries source,
transcript, observation, claim, or model tables.

See `docs/pages-operations.md` for bootstrap, deployment, rollback, and retirement;
`docs/generated-content-and-source-rights.md` for licensing boundaries; and
`docs/sources/supreme-court.md` for access review.
