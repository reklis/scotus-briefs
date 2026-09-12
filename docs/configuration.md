# Configuration

`config/scotus.yaml` is the non-secret product configuration. Production output is
static-only and uses canonical origin `https://scotusbriefs.us`, root project path
`/`, and section path `/scotus/`. The exporter emits a root `CNAME` containing exactly
`scotusbriefs.us` plus one newline. Paths are normalized and must remain inside the
output/state roots; a runtime API URL is forbidden for Pages.

## Scheduled and bounded work

The daily schedule is `17 3 * * *` (03:17 UTC). Routine work independently checks the
configured active term's slip-opinion table, a recent transcript/correction window, and
a small rotating historical slice. Historical bootstrap is a separate manually
dispatched mode with independent term, case, request, and byte caps. The independent
slip path supports strict individual opinion, per-curiam, and decree rows (including
emergency `A` and consolidated dockets), not expansion of omnibus order lists.

Runner limits bound selected cases/documents, HTTP requests/download bytes, private
disk, and runtime. Model limits separately bound extraction calls, brief calls, total
attempted calls, input characters/tokens, output tokens, zero local cost, request
timeout, and transport attempts. Configuration validation rejects inconsistent
maxima. The complete eligible queue is ranked before its case limit. Fresh new/changed
Court activity comes first; unattempted fresh work uses authoritative official activity
date newest-first. Persisted pending retries are reconsidered with
least-recently-attempted rotation before date so one failing newest case cannot starve
the backlog. Processor/current rechecks and rotating historical work follow. Budget
exhaustion creates dated sanitized pending work; it does not create a partial case or
silently drop discovered supported activity.

### Editorial backfill controls

`editorial_backfill.enabled` is the reviewed feature gate and `maximum_stage` is the
largest stage an operator may request. `rollout_stage: null` deliberately selects no
legacy processor migration. A protected manual dispatch may choose `canary_10`,
`batch_25`, or `batch_100`; the CLI applies the stage as a selection ceiling and takes
the minimum of that ceiling and every configured nightly/bootstrap case budget. It
never raises a configured resource limit. `--maximum-cases` can lower the resulting
bound again.

A processor change resets selection and must begin at `canary_10`. The exact selected
case keys and newest-first rank boundary are persisted in sanitized state. Runtime-
deferred selected cases resume from that set, failed cases use the finite normal retry
rotation, and stale processor cases outside the selection never enter `PendingWork`.
Every measured stage forces `publication.dry_run=true`, so it can produce a validated
side-by-side candidate but cannot become deployment-ready before its own review.

The source user agent used by protected automation is:

```text
ragchew-scotus-briefs/1.0 (+https://github.com/reklis/scotus-briefs; contact=https://github.com/reklis)
```

It is descriptive without inventing an email address. Change it only with source
review.

## Launch gates and secrets

For a new deployment or any unreviewed source/runtime change, these settings remain
`false` until owner approval:

- source review, Apache-2.0/CC-BY-4.0 license, canonical origin, model-runtime,
  and launch approvals;
- model brief generation;
- static publication.

The owner approved bounded production on 2026-09-03, so the checked-in source,
generation, approval, and publication switches are currently `true`. The replacement
model nevertheless remains `publication.dry_run=true` until its exact canary receives
reviewed approval; schedules and manual dispatch cannot publish Cogito output in this
state. This is not a validator bypass: a manual workflow dispatch cannot override a
closed gate, and every
candidate remains subject to the source, budget, grounding, privacy, completeness, and
release-integrity checks. The protected `scotus-publication`
build runs on the self-hosted Spark runner and accepts only
`RAGCHEW_OLLAMA_BASE_URL=http://127.0.0.1:11434/v1`. That typed setting rejects remote
hosts, credentials, query strings, and non-`/v1` paths. The OpenAI SDK is used only as
Ollama's compatible JSON-schema chat client, with a non-secret placeholder key. The
only reviewed model is the Apache-2.0 `cogito:70b` content with full Ollama digest
`8f2632d0faa422ff60435bc0095575d032a8b4a0f728df034d90ea654ffb60bb`.
Before any Court retrieval or completion traffic, preflight requires one installed
inventory entry matching both the exact tag and full digest. The adapter repeats that
exact inventory check immediately before and after every completion, rejecting the
response if the mutable tag changes during a request. A missing model or tag drift fails
closed: automation never installs or pulls a model, selects another local model, or falls
back to a hosted provider. Deploy, receipt persistence, and promotion stay on
GitHub-hosted Ubuntu and receive no model setting or secret. Pages has no runtime
environment at all.

`editorial_backfill.replacement_canary_case_keys` pins the ten rejected Qwen comparison
cases in reviewed order. A replacement processor may start `canary_10` only from that
exact manifest. Old-processor attempt, retry, aggregate, candidate, and reviewer state is
reset; unrelated fresh work cannot consume a measured slot. Current official document
bytes must still match each durable comparison case before any Cogito request.

`.env.example` therefore contains no reader database/object-store credentials.
`RAGCHEW_DATABASE_DSN` is accepted only when an operator explicitly runs the one-time
legacy bootstrap exporter. Ephemeral CI PostgreSQL/MinIO values are job-local and are
not production requirements.

## Public state and local preview

The generated branch schema, active/recheck windows, state paths, output paths,
processor fingerprints, release pointers, supported activity, and sanitized freshness
metadata are validated as public data. Freshness includes counts and newest official
activity dates for discovered, published, deferred, failed, and combined pending work;
all supported discovery must be published-current or explicit pending. No setting can
permit source documents, extracted source text, prompts, rejected prose, or model
output in the state/output tree; allowlisted official captions remain public metadata.

Fixture preview does not need live secrets:

```bash
uv run ragchew-scotus-static fixture-preview \
  --fixture tests/fixtures/static/one-case.json --output site-output \
  --build-epoch 2026-08-28T03:17:00Z
uv run ragchew-scotus-static validate --output site-output
```

Dormant `config/proceedings.yaml` and `config/mvp.yaml` remain local legacy paths.
They are unrelated to static SCOTUS publication.
