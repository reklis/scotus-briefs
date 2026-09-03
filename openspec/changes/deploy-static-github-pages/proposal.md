## Why

The local SCOTUS Legal Briefs MVP still requires a live FastAPI service, PostgreSQL, and private object storage to serve readers, so it cannot run on GitHub Pages. The project now needs a serverless public-release path that checks official Court material nightly, publishes only validated static files, preserves the last known-good site on failure, and is safe to develop in a public open-source repository.

## What Changes

- **BREAKING** Replace the production FastAPI/PostgreSQL public site with a pre-generated static site deployable under a GitHub Pages project base path; no production page may require a server, database, object store, or runtime secret.
- Add a deterministic static exporter for the homepage, term and argument-date archives, case pages, search data/UI, public projection JSON, metadata, sitemap, robots policy, assets, and GitHub Pages support files.
- Replace request-time search, filtering, and pagination with generated pages and progressively enhanced client-side search over a minimal sanitized index.
- Add a nightly and manually dispatchable GitHub Actions batch whose analysis/build job runs on the dedicated local Spark self-hosted runner, discovers Court updates, processes only new or changed case material with the local Ollama `qwen3.8:27b` model in an ephemeral private workspace, merges unchanged validated public cases from the prior public snapshot, and atomically deploys a complete Pages artifact.
- Preserve sanitized publication state in an auditable generated-content branch while forbidding copied Court documents, extracted transcript text, prompts, credentials, internal claim identifiers, and private processing records from commits, caches, logs, and workflow artifacts.
- Add bounded one-shot/drain modes, document-revision detection, conditional discovery, model-call budgets, migration/bootstrap support, and publication gates suitable for an ephemeral CI runner.
- Keep the last successfully deployed site and generated-content snapshot unchanged when discovery, parsing, generation, validation, or deployment fails.
- Retire the production Kubernetes public workload and public database dependency; keep a fixture-backed local static preview and retain dormant infrastructure only where explicitly documented as non-production.
- Prepare the repository for public collaboration with an OSI-approved software license, clear generated-content/source-material rights boundaries, contribution and security policies, stronger secret/data ignores, pinned CI dependencies, and least-privilege Pages workflow permissions.

## Capabilities

### New Capabilities

- `static-scotus-site`: Generate a complete, base-path-safe, accessible, searchable SCOTUS Legal Briefs site containing only sanitized public data and no runtime backend dependency.
- `nightly-static-publication`: Run the Court discovery-to-publication pipeline as a bounded, incremental nightly CI batch with safe state carry-forward, strict cost/privacy controls, validation, and last-known-good deployment semantics.
- `open-source-release`: Publish and maintain the codebase with explicit licensing, source/content boundaries, contributor/security documentation, secret hygiene, reproducible dependencies, and least-privilege automation.

### Modified Capabilities

None. The related MVP capabilities have not yet been promoted into `openspec/specs`; this change defines the deployable static publication boundary without treating unarchived change specs as main specifications.

## Impact

- Affects `src/ragchew/scotus` discovery, worker, publisher, public contracts, rendering, and CLI entry points; SCOTUS templates/assets and public tests; configuration and documentation; GitHub workflows; repository metadata/ignore files; and Kubernetes public deployment manifests.
- Adds a checked/validated public snapshot and build-manifest format plus static search JavaScript. Production serving no longer uses FastAPI, Uvicorn, PostgreSQL credentials, or S3 credentials.
- Nightly processing contacts only reviewed official Supreme Court endpoints and the loopback Ollama service on the dedicated Spark self-hosted runner. No evidence or prompt is sent to an external model provider. Raw documents and extracted text exist only in ephemeral job storage and are never included in the Pages artifact or generated-content branch.
- Requires a protected dedicated self-hosted runner, local Ollama availability, repository Pages configuration, and protected GitHub environments. The project owner must confirm the final OSI software license and generated-content license before public launch; Apache-2.0 for repository-authored software/docs and CC BY 4.0 for original generated briefs are the proposed defaults, with official Court materials excluded and linked rather than redistributed.
