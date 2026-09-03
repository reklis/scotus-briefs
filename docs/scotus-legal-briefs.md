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

At 03:17 UTC a protected, serialized, non-cancelling workflow:

1. reads the validated public state from `generated-content` and reconciles its active
   release ID with Pages;
2. conditionally checks the current term/recent correction window and a bounded
   rotating historical slice using a descriptive GitHub project user agent;
3. selects work under request, byte, document, case, disk, runtime, model-call/token,
   and zero-local-cost limits;
4. downloads required official documents into a mode-0700 runner workspace and fully
   recomputes every required argument session for each changed case;
5. merges only complete accepted cases while preserving unchanged case bytes and
   recording incomplete work as coarse public pending state;
6. exports and validates a fresh candidate, deploys that exact Pages artifact, then
   compare-and-swap promotes the exact generated state.

No Docker services run in publication. Court documents and model material stay only
in the self-hosted runner's mode-0700 workspace. Cleanup runs before and after each
build, including after failure. Deploy, receipt, and promotion jobs remain
GitHub-hosted and receive no Court/model/database/object credentials. A
no-content-change run skips Pages deployment and may advance only validated discovery
checkpoints. A failed cycle leaves the previous release untouched.

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
newline-terminated. Public state may retain official URLs, HTTP validators, content
digests/counts, bounded cursors, processor fingerprints, release pointers, sanitized
pending reasons, and opaque model-attempt receipts with zero local cost.

It must not retain Court/source bodies, PDFs/media, extracted transcript text,
evidence windows, observations, claims/claim IDs, prompts, model output, object keys,
credentials, stack traces, or private/internal IDs. Structural contracts and a
whole-bundle textual scan enforce this boundary before any upload.

## Evidence and generation

Only reviewed `www.supremecourt.gov` methods are eligible. A transcript establishes
attributed speech, not a vote or holding. Only an official order/opinion can establish
a Court disposition. Changed source bytes allocate a new logical document revision;
one missing or malformed response never retracts an existing brief.

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
