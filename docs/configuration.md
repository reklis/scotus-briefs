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
generation, approval, and publication switches are currently `true`. This is not a
validator bypass: a manual workflow dispatch cannot override a closed gate, and every
candidate remains subject to the source, budget, grounding, privacy, completeness, and
release-integrity checks. The protected `scotus-publication`
build runs on the self-hosted Spark runner and accepts only
`RAGCHEW_OLLAMA_BASE_URL=http://127.0.0.1:11434/v1`. That typed setting rejects remote
hosts, credentials, query strings, and non-`/v1` paths. The OpenAI SDK is used only as
Ollama's compatible JSON-schema chat client, with a non-secret placeholder key. The
exact installed model `qwen3.8:27b` is checked before evidence or completion traffic.
Deploy, receipt persistence, and promotion stay on GitHub-hosted Ubuntu and receive no
model setting or secret. Pages has no runtime environment at all.

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
