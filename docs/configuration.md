# Configuration

`config/scotus.yaml` is the non-secret product configuration. Production output is
static-only and uses canonical origin `https://reklis.github.io`, project path
`/scotus-briefs/`, and section path `/scotus/`. Paths are normalized and must remain
inside the output/state roots; a runtime API URL is forbidden for Pages.

## Scheduled and bounded work

The nightly schedule is `17 3 * * *` (03:17 UTC). Routine work checks the active term,
a recent transcript/correction/opinion window, and a small rotating historical slice.
Historical bootstrap is a separate manually dispatched mode with independent term,
case, request, and byte caps.

Runner limits bound selected cases/documents, HTTP requests/download bytes, private
disk, and runtime. Model limits separately bound extraction calls, brief calls, total
attempted calls, input characters/tokens, output tokens, zero local cost, request
timeout, and transport attempts. Configuration validation rejects inconsistent
maxima. Budget exhaustion creates sanitized pending work; it does not create a
partial release.

The source user agent used by protected automation is:

```text
SCOTUS-Legal-Briefs/0.1 contact=https://github.com/reklis/scotus-briefs
```

It is descriptive without inventing an email address. Change it only with source
review.

## Launch gates and secrets

These settings remain `false` before owner launch:

- source review, Apache-2.0/CC-BY-4.0 license, canonical origin, model-runtime,
  and launch approvals;
- model brief generation;
- static publication.

A manual workflow dispatch cannot bypass a gate. The protected `scotus-publication`
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
processor fingerprints, and release pointers are validated as public data. No
setting can permit source documents, extracted text, prompts, or model output in the
state/output tree.

Fixture preview does not need live secrets:

```bash
uv run ragchew-scotus-static fixture-preview \
  --fixture tests/fixtures/static/one-case.json --output site-output \
  --build-epoch 2026-08-28T03:17:00Z
uv run ragchew-scotus-static validate --output site-output
```

Dormant `config/proceedings.yaml` and `config/mvp.yaml` remain local legacy paths.
They are unrelated to static SCOTUS publication.
