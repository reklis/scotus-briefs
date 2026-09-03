# SCOTUS Legal Briefs

SCOTUS Legal Briefs turns complete official Supreme Court oral-argument transcripts
into evidence-grounded, plain-language case briefs. Production is a deterministic
static site at **<https://reklis.github.io/scotus-briefs/>**. Readers need no FastAPI
service, API, PostgreSQL database, object store, model call, Kubernetes workload, or
runtime secret.

The analysis is automated, delayed, incomplete, and non-authoritative. It is not an
official Court record, legal advice, or a prediction of any justice's vote or case
outcome. Always consult the linked official Court materials.

## Production boundary

A protected self-hosted GitHub Actions job runs nightly at 03:17 UTC (and by
restricted manual dispatch). Its aarch64 Spark runner uses the loopback-only Ollama
model `qwen3.8:27b`; no model credential or remote model endpoint is accepted. It
checks bounded reviewed Court resources, recomputes an entire changed
case inside a permission-restricted ephemeral workspace, carries unchanged validated
case bytes forward, exports a complete project-path-safe site, and runs contract,
integrity, link, accessibility, and privacy validation. Pages receives only the
validated static artifact. Any failure leaves the last known-good site active.

The public `generated-content` branch contains only versioned projection/case JSON,
conditional validators and digests, bounded cursors/pending outcomes, immutable
public revisions, release manifests, and opaque model-attempt/zero-cost receipts. It never contains
Court PDFs, source HTML, extracted transcript text, observations/claim ledgers,
prompts, model responses, object keys, credentials, private logs, or internal UUIDs.
Official documents are linked, not redistributed.

Live Court/model processing is disabled by default. Source, licensing, origin,
model-runtime, and launch gates remain fail-closed until the repository owner
completes review.

## Local development

Install Python 3.12 and [uv](https://docs.astral.sh/uv/):

```bash
uv sync --frozen --dev
uv run ruff check .
uv run mypy
uv run pytest
```

Local preview is fixture-backed and static. It does not contact the Court or a model:

```bash
uv run ragchew-scotus-static fixture-preview \
  --fixture tests/fixtures/static/one-case.json --output site-output \
  --build-epoch 2026-08-28T03:17:00Z
uv run ragchew-scotus-static preview --directory site-output --host 127.0.0.1 --port 8000
```

The exact CLI is also used by `.github/workflows/publish-pages.yml`. Docker Compose,
legacy FastAPI/database code, and manifests under `deploy/k8s/dormant/` exist only for
isolated migration or legacy tests; they are not production serving architecture.

## Documentation

- [Architecture and privacy boundary](docs/scotus-legal-briefs.md)
- [Configuration](docs/configuration.md)
- [Security](docs/security.md) and [private vulnerability reporting](SECURITY.md)
- [Pages operations and migration](docs/pages-operations.md)
- [Generated-content and source rights](docs/generated-content-and-source-rights.md)
- [Repository governance](docs/repository-governance.md)
- [Supreme Court source review](docs/sources/supreme-court.md)
- [Contributing](CONTRIBUTING.md)

Repository-authored code and documentation are Apache-2.0. Original generated briefs
are CC BY 4.0. Official Court and third-party material is excluded; see `NOTICE`.
