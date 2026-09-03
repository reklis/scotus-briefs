## Context

The MVP's public boundary is already a sanitized `ScotusPublicProjection`, but production delivery is still a FastAPI/Jinja application that reads the active projection from PostgreSQL. Collection and analysis also assume durable PostgreSQL jobs and S3-compatible private storage. GitHub Pages can serve only files. The analysis/build job must run on the dedicated local Spark self-hosted runner so it can use loopback Ollama with `qwen3.8:27b`; that host is persistent even though each pipeline workspace must be ephemeral. Scheduled workflows in a public repository expose logs and artifacts broadly, and the project policy forbids redistribution of copied PDFs or extracted transcript text.

The deployment therefore needs two boundaries: a public, versioned state that may safely survive in Git, and a private processing workspace that exists only for one trusted workflow run. Public state must be sufficient to carry unchanged briefs forward and determine what needs reprocessing. When a source changes, the workflow can re-download and recompute that whole case rather than persist private evidence between runs.

## Goals / Non-Goals

**Goals:**

- Serve the production site entirely from GitHub Pages with no runtime application, database, object store, API, model call, or secret.
- Discover Court changes nightly, process a bounded amount of new or revised material, and publish a complete deterministic static site.
- Preserve unchanged public cases, append accepted public revisions, and keep the previous site live after any failed cycle.
- Keep Court document copies, source HTML, extracted transcript text, observations, claim ledgers, prompts, model responses, and credentials out of Git, Pages, caches, logs, and uploaded artifacts.
- Support GitHub project paths and a future custom domain through centralized URL generation.
- Make code, repository-authored documentation, and generated public content available under explicit open licenses with clear source-material exclusions.

**Non-Goals:**

- Running a public FastAPI service or any other dynamic production endpoint.
- Persisting private processing state in GitHub Actions, GitHub Pages, or an external managed backend.
- Republishing Court PDFs, complete transcripts, source HTML, or third-party material.
- Guaranteeing immediate publication; one bounded nightly cycle may leave work pending.
- Guaranteeing exactly-once model billing after an abrupt runner or provider failure.
- Allowing pull requests or forks to run live-source/model publication jobs.

## Decisions

### 1. Treat the static tree as the only production runtime

A new static export command will consume a validated `ScotusPublicProjection` and write a complete directory containing the root landing page, SCOTUS homepage, generated pagination, term archives, argument-date archives, stable case pages, search page/index, correction history, public projection JSON, `404.html`, `robots.txt`, `sitemap.xml`, assets, release metadata, and `.nojekyll`. GitHub Pages receives this directory through the official Pages artifact/deploy actions.

Every internal URL will be produced by one helper from `canonical_origin`, `project_base_path`, and `section_path`. Generated paths will use directories with `index.html` and trailing-slash links. Templates will not contain hardcoded `/scotus`, `/static`, or `/api` roots. Archives and case pages will remain useful without JavaScript.

The production `ragchew-public` service and its database reader are retired. A fixture-backed local preview may serve the generated directory with Python's static HTTP server, but it is not a production architecture.

Alternatives rejected: hosting FastAPI elsewhere would violate the static-only requirement; a client application that fetches a live API would retain a runtime backend.

### 2. Store only sanitized incremental state in a public generated-content branch

An orphan `generated-content` branch will hold versioned, deterministic JSON rather than private pipeline state:

- `snapshot/v1/projection.json`: the latest complete public projection.
- `snapshot/v1/cases/<case-key>/revisions/<n>.json`: immutable accepted public case revisions.
- `state/v1/publication.json`: official URLs, logical case/document keys, HTTP validators, document byte digests/counts, backfill/recheck cursors, processor/config fingerprints, pending public work, and active public revision pointers.
- `state/v1/cost-ledger.json`: opaque input fingerprints and coarse attempted/blocked outcomes needed to bound repeated model calls.
- `release/v1/release.json`: active/previous release IDs, source commit, projection digest, tool/schema versions, and generated file digests/counts.

The case key derives from normalized term and docket and does not change with the caption. JSON uses UTF-8, sorted keys, stable collection ordering, UTC timestamps, explicit schema versions, and a trailing newline. State schemas reject unknown fields and forbidden private names. Internal claim/observation/document UUIDs are removed from public contracts; provenance remains a descriptive official Court URL, evidence type, and page label.

No PDF, source-page body, transcript text, evidence window, observation, approved-claim ledger, prompt, raw model output, object key, credential, stack trace, or private identifier may enter this branch. Because it is public, the branch is treated as another publication artifact and receives the same privacy scan as Pages.

Alternatives rejected: GitHub cache/artifacts are not reliable durable state and are inappropriate for private evidence; committing private database/object snapshots would violate the privacy boundary; managed PostgreSQL/S3 would retain backend infrastructure the user is trying to remove.

### 3. Recompute changed cases in an ephemeral batch workspace

The scheduled build checks out source plus the active generated-content state. It discovers and hashes bounded Court resources, carries byte-identical case JSON forward for unchanged cases, and fully reprocesses each selected new or changed case from its current canonical official documents. This full-case recomputation preserves whole-case/reargument validation without retaining transcript text overnight.

The production batch uses in-memory adapters and local temporary files rather than PostgreSQL or MinIO. The build runs under a dedicated non-privileged self-hosted GitHub Actions account on Spark. Before and after each run it removes prior private/candidate paths, creates a permission-restricted `$RUNNER_TEMP` workspace, never uploads private files, and performs unconditional cleanup because the host survives between jobs. Logs contain stage, public case key, status, counts, and digests only—not response bodies, transcript text, model payloads, signed URLs, or secrets. Pull requests and arbitrary refs never run on the self-hosted machine.

The worker receives bounded `--once`/`--drain` semantics and exits zero only when no runnable selected job remains. Time/budget exhaustion records sanitized pending work and is not represented as complete. Changed-case output replaces a prior case only after all current required argument sessions pass the existing parser, legal-status, grounding, sensitivity, and brief validators.

### 4. Make discovery and document revision detection incremental and correction-safe

Discovery persists per-index ETag/Last-Modified validators and bounded backfill/recheck cursors in public state. The active term and a configured recent correction/opinion window are checked every night; older resources are revisited on a rotating bounded schedule. Where validators are absent or unreliable, a bounded GET and SHA-256 comparison detects changes at the same URL.

Logical identity is separate from content revision. New bytes for an existing logical transcript/docket/order/opinion allocate revision `N+1`; they are not quarantined merely because the URL or external ID stayed the same. True conflicts within one attempted revision remain quarantined. A missing, unavailable, or malformed current source cannot delete or retract an already public case. An accepted source correction causes whole-case recomputation and an append-only public revision/correction note.

Historical bootstrap is a separate manually dispatched bounded mode. The nightly run defaults to the current term plus small lookback/recheck limits rather than polling every configured term and case.

### 5. Replace request-time search with minimal client-side search

The exporter emits a compact search index containing only path, title, caption, docket, term, argument date, status, and topics. A dependency-free script normalizes whitespace/case, safely renders with DOM text APIs, applies topic/status filters, and bounds/paginates results. It does not include full sections, claim identifiers, transcript excerpts, or private metadata. The static search page explains that JavaScript is required for free-text search; generated term/date archives and case navigation work without it.

Alternatives rejected: pre-generating every possible query is impossible; a hosted search service would add a runtime dependency and external data processor.

### 6. Build and validate releases deterministically before deployment

Rendering occurs in a fresh candidate directory. A release ID is derived from content digests; discovery-only timestamp/checkpoint changes do not alter public page bytes or `generated_at`. Before upload, validators will:

- parse every JSON file with the public/static contracts;
- verify manifest digests, unique case identities, stable slugs, monotonic immutable revisions, source allowlists, and state/projection consistency;
- crawl generated HTML for internal-link, base-path, canonical, sitemap, pagination, disclosure, accessibility, and official-source correctness;
- reject root-absolute project links and private/legacy routes;
- scan the entire candidate/state bundle for forbidden fields, credentials, PDF signatures, source/transcript payloads, prompt/model payloads, and internal UUIDs.

If discovery, processing, generation, export, validation, or deployment fails, neither the active state pointer nor the current Pages deployment is replaced. Candidate work is discarded. Unchanged prior cases are copied exactly rather than regenerated from lossy state.

### 7. Separate trusted processing, deployment, and promotion permissions

A pinned `.github/workflows/publish-pages.yml` runs on a nightly UTC cron and restricted `workflow_dispatch`, never on pull requests. It uses concurrency with `cancel-in-progress: false`, explicit timeouts, frozen dependencies, minimal artifact retention, and these permission boundaries:

1. **Build:** runs only on the dedicated `self-hosted` Spark runner with read-only repository access and a protected publication environment; checks out both branches with persisted credentials disabled, verifies loopback Ollama and the exact `qwen3.8:27b` model, processes in an ephemeral workspace, and uploads only a privacy-scanned Pages candidate. It receives no external model API key.
2. **Deploy:** runs on GitHub-hosted Ubuntu with `pages: write` and `id-token: write`, no source/model/storage secrets; deploys the exact validated candidate.
3. **Promote:** runs on GitHub-hosted Ubuntu after successful deployment, with `contents: write` and no secrets; compare-and-swap updates the generated-content active snapshot/state to the exact deployed release.

A sanitized cost receipt may need persistence after a paid call even when publication fails. It is isolated from the active projection and written with a compare-and-swap update by a no-secret step. A public release ID allows the next run's reconciliation command to detect the rare case where Pages deployment succeeds but branch promotion fails, and either promote the matching validated release or redeploy the branch's last active release.

The normal pull-request workflow remains fixture-only and receives no publication secrets or write permissions. Actions are pinned to commit SHAs, checkout does not persist credentials, and untrusted downloaded/source data is never executed.

### 8. Bound source use, model cost, and retries

Configuration adds hard maxima for cases/documents, downloaded bytes, private disk, runtime, extraction calls, brief calls, total model calls, input characters/tokens, and output tokens. Defaults permit a small nightly amount of changed work. The OpenAI Python client is used only as a protocol client for Ollama's loopback OpenAI-compatible endpoint; it sends no requests to OpenAI. Calls use explicit timeouts and bounded retries, and automatic SDK retries that could exceed the ledger are disabled. Startup validates that the endpoint is loopback-only and that Ollama reports the exact configured `qwen3.8:27b` model with JSON-schema completion support.

An opaque input fingerprint covers public document digests plus provider, endpoint identity, parser, extractor, policy, model, prompt, and relevant configuration versions. A previously attempted unchanged input is not run again unless source bytes or one of those versions changes or an operator explicitly authorizes replay. Budget exhaustion leaves safe pending work for a later run. A runner crash after Ollama accepts a request but before receipt persistence can cause one repeat, which is accepted as the unavoidable trade-off without durable private transactional storage.

### 9. Make licensing and repository hygiene part of the launch gate

The proposed defaults are Apache-2.0 for repository-authored software/documentation and CC BY 4.0 for original generated briefs, subject to owner confirmation before files are merged. `NOTICE` and a generated-content policy will state that official Court documents and third-party material are excluded, are not committed or redistributed, and remain governed by their own rights. Public brief provenance links to the Court.

The public release adds contribution and private security-reporting policies, synthetic-fixture labels, stricter ignore and Docker-context rules, secret scanning, dependency/license review, pinned Actions/tooling, and documented repository settings such as branch protection and protected environments. Production enablement fails closed until the selected licenses, source review, Pages origin/base path, and launch approval are configured.

## Risks / Trade-offs

- **[A changed case must be recomputed from scratch]** → Keep nightly case/model limits small, carry unchanged public JSON exactly, and provide a bounded manual backlog drain.
- **[Court servers omit or misuse cache validators]** → Rotate bounded digest rechecks and never remove prior content after one missing/failed response.
- **[Public state accidentally includes private evidence]** → Use allowlisted frozen schemas plus structural/textual whole-bundle scans; never upload an unvalidated bundle.
- **[Deploy and branch promotion cannot be one transaction]** → Use content-derived release IDs, compare-and-swap promotion, and explicit reconciliation against the live release marker.
- **[Persistent self-hosted runner retains private files or is exposed to untrusted workflows]** → Use a dedicated non-privileged repository runner, prohibit PR/arbitrary-ref execution, enforce pre/post cleanup under a mode-0700 workspace, and periodically audit the runner host and Ollama logs.
- **[Runner dies around a local model call]** → Disable implicit retries, enforce hard per-run limits, persist an opaque receipt when possible, and accept at most the documented crash-window replay risk.
- **[Generated-content branch grows indefinitely]** → Keep immutable public case revisions but avoid duplicate rendered trees and private/intermediate data; document an auditable compaction policy if size becomes material.
- **[JavaScript is disabled]** → Keep all archives and cases pre-rendered; only free-text search requires JavaScript.
- **[GitHub Pages cannot set arbitrary security headers]** → Avoid inline/untrusted HTML, use a restrictive meta CSP where supported, serve no secrets, and document that transport headers are controlled by GitHub Pages.
- **[An open-source license is chosen incorrectly for content]** → Keep publication disabled until the owner confirms licenses and the rights/source notice is reviewed.

## Migration Plan

1. Add versioned static/public-state contracts, remove internal IDs from public serialization, and create sanitized fixtures.
2. Implement deterministic static export, base-path-safe templates/assets, search, manifesting, privacy scans, and fixture-backed local preview.
3. Add incremental public state, conditional discovery, same-URL revision allocation, bounded one-shot processing, and cost controls.
4. Export any accepted legacy public projection through a private operator-only sanitizer, or bootstrap an empty fixture snapshot. Independently scan it before creating the orphan generated-content branch.
5. Install a dedicated self-hosted Actions runner on Spark, restrict it to the repository, verify loopback Ollama and `qwen3.8:27b`, and add the pinned nightly/manual workflow in dry-run mode. Configure the canonical origin, project base path, protected publication/Pages environments, concurrency, and branch protection; no external model secret is used.
6. Run fixture and live private dry runs without deployment; compare static routes/content with the MVP preview and verify no private data appears in logs, artifacts, branch state, or site files.
7. Enable GitHub Pages deployment manually, then enable the nightly schedule only after existing legal/source launch gates and licensing approval pass.
8. Remove the Kubernetes public Service/Ingress/Deployment and scheduled production pipeline from the active deployment; revoke its public database/object credentials. Keep migration/local tooling clearly marked dormant.
9. Roll back by redeploying the generated-content branch's previous release ID and resetting its active pointer; do not regenerate or edit the prior release in place.

## Open Questions

- Confirm Apache-2.0 for repository-authored software/documentation and CC BY 4.0 for original generated briefs, or select alternatives before implementation merges license files.
- Confirm the GitHub owner/repository name and whether launch uses project Pages (`/<repository>/`) or a custom domain; the implementation supports both.
- Select the nightly UTC time and initial per-run model/case budget after observing one dry-run case.
