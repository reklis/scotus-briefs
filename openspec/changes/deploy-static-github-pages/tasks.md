## 1. Release Decisions and Configuration

- [x] 1.1 Confirm and record the GitHub owner/repository, initial canonical origin/project base path, nightly UTC schedule, per-run processing/model budget, Apache-2.0 software/docs license, and CC BY 4.0 generated-brief license (or approved alternatives).
- [x] 1.2 Extend typed SCOTUS configuration with static output, canonical origin, project/section paths, generated-state schema, active/recheck windows, bootstrap limits, runner resource limits, model-call/token/cost limits, and explicit license/launch approvals.
- [x] 1.3 Add configuration validation that normalizes base paths, rejects external/runtime API dependencies for Pages, enforces theoretical budget maxima, and keeps live publication disabled until source, license, origin, secret, and launch gates are approved.
- [x] 1.4 Update `config/scotus.yaml` with fail-closed static publication defaults, a current-term nightly scope, rotating historical rechecks, and separately bounded manual bootstrap settings.
- [x] 1.5 Add configuration tests for project/custom-domain paths, malformed origins, unsafe output/state paths, incompatible budgets, disabled gates, and valid dry-run/live combinations.

## 2. Sanitized Public and State Contracts

- [x] 2.1 Version the static public projection/case contracts and remove internal claim UUIDs from `PublicSourceLink` and all serialized public provenance while retaining official URL, evidence type, label, and page label.
- [x] 2.2 Add frozen strict contracts for logical source/document state, conditional validators, content digests/counts, case revision pointers, pending work, bounded cursors, processor fingerprints, and the complete publication state.
- [x] 2.3 Add frozen strict contracts for immutable public case revision records, opaque model-attempt/cost receipts, active/previous release pointers, and per-file release-manifest integrity metadata.
- [x] 2.4 Implement normalized deterministic JSON serialization with explicit schema versions, UTC timestamps, sorted keys/collections, UTF-8, and trailing newlines.
- [x] 2.5 Add recursive allowlist/denylist validation for state and public payloads that rejects source/transcript bodies, private text, observations, claim ledgers, prompts, raw model output, object keys, credentials, stack traces, and private/internal identifiers.
- [x] 2.6 Create sanitized static fixtures for empty bootstrap, one case, multiple terms, reargument, correction, prior release, pending work, and search, with every fixture explicitly marked synthetic.
- [x] 2.7 Add contract tests for schema compatibility, unknown-field rejection, official Court URL restrictions, digest/validator validation, stable logical case keys, immutable revision numbering, and absence of claim/document/observation UUIDs.

## 3. Public Generated-Content State Store

- [x] 3.1 Implement deterministic loading and validation for `snapshot/v1`, `state/v1`, and `release/v1` from an empty bootstrap directory or checked-out generated-content branch.
- [x] 3.2 Implement atomic candidate-directory writes that never mutate the checked-out active snapshot in place and remove incomplete candidates on failure.
- [x] 3.3 Implement exact carry-forward of unchanged public case JSON plus merge/replace logic for newly accepted cases without regenerating unchanged bytes.
- [x] 3.4 Implement append-only per-case public revision storage, stable term/docket case identity, active revision pointers, and legacy slug redirect metadata.
- [x] 3.5 Implement publication-state updates for HTTP validators, digests, cursors, processor/config fingerprints, pending work, and coarse sanitized error categories.
- [x] 3.6 Implement opaque model input fingerprints and compare-and-swap cost receipt updates that cannot advance the active public release.
- [x] 3.7 Implement release-parent compare-and-swap and live/branch release-ID reconciliation primitives for interrupted Pages deployment or branch promotion.
- [x] 3.8 Add state-store tests for deterministic round trips, incompatible schema refusal, carry-forward identity, append-only revisions, CAS conflicts, interrupted writes, and reconciliation choices.

## 4. Deterministic Static Site Export

- [x] 4.1 Extract shared case sorting/date/slug behavior from the dynamic app and implement one URL helper for canonical origin, project base path, section path, assets, data, pagination, redirects, and official external links.
- [x] 4.2 Refactor the SCOTUS base, index, and case templates to render without a FastAPI request context or hardcoded root paths and to preserve disclosures, semantic structure, accessibility, provenance, and revision history.
- [x] 4.3 Implement generation of the root landing page, SCOTUS homepage, bounded pagination, every term archive, every argument-session date archive, topic/status navigation, stable case pages, corrections/revisions index, and legacy path/slug redirects.
- [x] 4.4 Implement generated `404.html`, `.nojekyll`, `robots.txt`, canonical tags, sitemap, release metadata links, and copied/fingerprinted local CSS/JavaScript assets.
- [x] 4.5 Emit versioned sanitized projection and per-case JSON files and verify that no dynamic `/api/scotus/projection` dependency remains.
- [x] 4.6 Add a minimal search-index builder limited to path, title, caption, docket, term, argument date, status, and topics.
- [x] 4.7 Add dependency-free client-side search with whitespace/case normalization, deterministic ordering, status/topic filters, bounded pagination, empty/no-result states, and DOM text rendering that cannot execute indexed/query markup.
- [x] 4.8 Add static search, corrections, redirect, empty-state, and not-found templates/styles while keeping all non-search browsing useful without JavaScript.
- [x] 4.9 Build a content-derived release manifest with source commit, prior release ID, schema/config/tool versions, projection digest, file digests/sizes, and aggregate case/page counts.
- [x] 4.10 Add CLI commands to export, validate, and fixture-preview a static tree with an explicit reproducible build epoch and a plain local static server.
- [x] 4.11 Replace dynamic-route tests with golden static-output tests for byte determinism, project/custom-domain paths, pagination, archives, stable case URLs, redirects, JSON, metadata, disclosure, provenance, and empty output.
- [x] 4.12 Add JavaScript tests for search matching/order/filter/pagination behavior, safe markup handling, and strict search-index fields.

## 5. Incremental Court Discovery and Revisions

- [x] 5.1 Refactor SCOTUS discovery into a reusable one-shot operation that accepts and returns per-resource conditional/public checkpoint state instead of always using an empty `ConditionalRequest`.
- [x] 5.2 Remove volatile retrieval values from source fingerprints and ensure unchanged index/detail descriptors create no processing work.
- [x] 5.3 Implement nightly selection for the active term and configured recent transcript/correction/opinion window with `new_transcript_priority` applied to newly available material.
- [x] 5.4 Implement bounded rotating historical resource rechecks and a separately invoked bounded bootstrap cursor so routine nightly runs do not scan every term/case.
- [x] 5.5 Add conditional document retrieval using saved ETag/Last-Modified values and bounded streamed digest comparison when reliable validators are absent.
- [x] 5.6 Separate logical case/session/document identity from content revision and allocate revision `N+1` for accepted changed bytes at the same or a new official URL while preserving true same-revision conflict quarantine.
- [x] 5.7 Preserve a published case and mark sanitized retry/pending state when a current source is missing, unavailable, redirected, malformed, or fails integrity checks; never infer deletion/retraction from one poll.
- [x] 5.8 Add discovery/document tests for 304 responses, missing validators, stable fingerprints, nightly bounds/priorities, rotating cursors, same-URL changes, new-URL revisions, duplicates/conflicts, corrections, and temporary source failure.

## 6. Ephemeral Bounded Processing Pipeline

- [x] 6.1 Add a permission-restricted run workspace abstraction rooted under runner temporary storage with run-scoped paths for downloads, extracted text, temporary database/object data, and candidate output.
- [x] 6.2 Refactor collector, parser, extractor, correlator, policy, and publisher stage logic behind reusable protocols so selected cases can run once against ephemeral stores without a permanent daemon.
- [x] 6.3 Add `--once` and `--drain` worker modes with stage filters, lease-safe shutdown, maximum idle/runtime controls, and a successful exit only when no selected runnable job or active lease remains.
- [x] 6.4 Implement the static batch orchestrator to load public state, discover changes, select bounded work, start/use run-scoped PostgreSQL/MinIO or in-memory adapters, and fully re-download/recompute every required argument session for each changed case.
- [x] 6.5 Merge only complete validated changed-case projections with exact unchanged prior cases and record incomplete/budget-deferred work solely as sanitized pending state.
- [x] 6.6 Require both brief-generation and static-publication gates before any paid generation or candidate activation, fixing the publisher path that currently ignores `publication.enabled`.
- [x] 6.7 Enforce per-run limits for selected cases/documents, HTTP requests/bytes, private disk, runtime, extraction calls, brief calls, total model calls, input size/tokens, output tokens, and estimated spend.
- [x] 6.8 Configure explicit OpenAI endpoint policy, timeouts, and bounded SDK retries; count every attempted extraction and brief call against one shared budget before sending it.
- [x] 6.9 Deny repeated paid processing for an unchanged evidence/parser/extractor/policy/model/prompt/config fingerprint unless an authorized replay or changed input is recorded.
- [x] 6.10 Sanitize operational logs/exceptions to public case key, stage, status, safe counts/digests/timings, and coarse failure category with no response bodies, transcript text, model payloads, signed URLs, secrets, or private stack traces.
- [x] 6.11 Add unconditional success/failure/signal cleanup that removes the private workspace and terminates run-scoped data services without caching or uploading their contents.
- [x] 6.12 Add pipeline tests for unchanged no-op, one new case, full changed-case recomputation, multiple argument sessions, budget deferral, drain completion, stage failure, retry bounds, repeated-input denial, cleanup, and sanitized logging.

## 7. Candidate Privacy, Integrity, and Rollback Validation

- [x] 7.1 Implement whole-bundle contract validation that reparses every public/state JSON file and cross-checks unique cases, active revisions, state/projection pointers, official URL allowlists, and release-parent consistency.
- [x] 7.2 Implement manifest verification for every generated file's digest/size and prove discovery-only checkpoint changes do not change public content release IDs.
- [x] 7.3 Implement a static HTML crawler that checks internal links, base-path confinement, canonical URLs, pagination, redirects, sitemap coverage, titles, semantic landmarks, disclosures, corrections, and official source labels.
- [x] 7.4 Implement structural and textual privacy scanning for forbidden keys/routes, credential patterns, PDF/media signatures, source/transcript payloads, prompts/model output, private paths, and internal UUIDs across candidate site, state, logs, and upload lists.
- [x] 7.5 Make deployable artifact creation contingent on every existing legal/sensitivity gate plus all static contract, link, accessibility, privacy, state-consistency, and release-integrity validators.
- [x] 7.6 Add fault-injection tests proving source, parser, extraction, correlation, policy, generation, rendering, scan, manifest, deployment, and promotion failures leave prior active snapshot/site bytes unchanged.

## 8. GitHub Actions and Pages Publication

- [x] 8.1 Pin existing CI actions/tools to reviewed immutable versions, disable persisted checkout credentials, use frozen lockfile installs, and retain read-only fixture-only behavior for pull requests/forks.
- [x] 8.2 Add a pinned nightly/manual Pages workflow with protected-default-branch checks, serialized non-cancelling concurrency, explicit job timeouts, safe summaries, and no pull-request trigger.
- [x] 8.3 Implement the read-only build job using protected publication environment secrets, ephemeral services/workspace, static batch/export/validation, unconditional cleanup, and upload of only the scanned Pages candidate with minimal retention.
- [x] 8.4 Implement the no-secret Pages deployment job with only `pages: write` and `id-token: write`, deploying the exact candidate release through the protected Pages environment.
- [x] 8.5 Implement the post-deploy no-secret generated-content promotion job with minimal `contents: write`, expected-parent compare-and-swap, and exact release/state/snapshot matching.
- [x] 8.6 Implement isolated `always()` persistence for privacy-scanned opaque cost receipts without advancing the active public projection after a failed run.
- [x] 8.7 Add a no-public-change path that avoids unnecessary Pages deployment while safely advancing only successful discovery/recheck checkpoint state.
- [x] 8.8 Add preflight reconciliation between live Pages and generated-content release IDs and stop normal publication until an interrupted deployment/promotion split is resolved.
- [x] 8.9 Add workflow-structure tests for triggers, branch checks, concurrency, timeouts, immutable actions, frozen installs, artifact retention, environment separation, exact per-job permissions, secret isolation, cleanup, deployment ordering, and promotion guards.

## 9. Open-Source Repository Hardening

- [x] 9.1 Add the approved OSI software/documentation license, generated-brief license text, and `NOTICE` with copyright policy, generated-content terms, official Court/third-party exclusions, and no implied Court affiliation.
- [x] 9.2 Add `CONTRIBUTING.md` covering setup/tests/style, contribution licensing, synthetic fixture rules, prohibited raw/private data, generated-content automation, and review expectations.
- [x] 9.3 Add `SECURITY.md` with supported scope, a private reporting channel, response expectations, and instructions not to put vulnerabilities, secrets, or sensitive evidence in public issues.
- [x] 9.4 Add generated-content/source-rights documentation distinguishing code, docs, original generated briefs, provenance metadata, official Court works, third-party content, fixtures, logos, and prohibited redistributed documents.
- [x] 9.5 Strengthen `.gitignore` and add a strict `.dockerignore` for environment variants, keys/certificates, databases/dumps, backups, PDFs/audio/media, source/extracted text, model/prompt dumps, private workspaces, reports, caches, and temporary static candidates, with explicit reviewed fixture exceptions.
- [x] 9.6 Add dependency/action update automation and license inventory/scanning, and remove mutable `master`/`latest`/unlocked tool references from release-relevant CI and image configuration.
- [x] 9.7 Review tracked fixtures, validation reports, `.pi`/OpenSpec artifacts, dormant radio code, and internal operational details; mark synthetic data and remove or document anything unsuitable for a public repository.
- [x] 9.8 Add repository governance/runbook guidance for CODEOWNERS or ownership rules, protected branches, required reviews/status checks, protected environments, restricted manual publication, and least-privilege workflow settings.
- [x] 9.9 Add automated tests that scan tracked files and Docker context for prohibited secrets/private document classes and verify all required governance/license files and launch approvals.

## 10. Migration and Retirement of Dynamic Production

- [x] 10.1 Add a private operator-only legacy exporter that reads the active PostgreSQL public projection/revision metadata, strips internal identifiers, emits only versioned sanitized static state, and fails on every forbidden field.
- [x] 10.2 Add migration tests comparing legacy and static public case content/provenance/revision history while proving no copied PDF, extracted text, prompt, rejected claim, credential, or private identifier is exported.
- [x] 10.3 Add tooling/documentation to create and protect the orphan generated-content branch from an independently scanned empty or legacy bootstrap release.
- [x] 10.4 Remove the production FastAPI public entry point from deployment commands while retaining only a clearly named fixture/static local preview compatibility path if needed.
- [x] 10.5 Remove the public Deployment, Service, Ingress, public database secret, SCOTUS analyzer deployment, and SCOTUS CronJobs from the active Kubernetes kustomization; mark remaining manifests as dormant local/migration infrastructure.
- [x] 10.6 Update environment examples and revoke/remove production reader/database/object-store requirements from the GitHub Pages path while keeping the workflow OpenAI secret scoped to the protected build job.
- [x] 10.7 Add deployment tests proving no active production manifest exposes FastAPI/PostgreSQL/MinIO/Kubernetes to readers and no Pages job receives obsolete backend credentials.

## 11. Documentation, Dry Run, and Launch

- [x] 11.1 Rewrite the README and architecture/configuration/security docs around the static-only production boundary, public generated state, ephemeral changed-case recomputation, model/source privacy, and fixture-backed local preview.
- [x] 11.2 Add a Pages operations runbook covering repository settings, environments/secrets, generated branch layout, cron/manual/bootstrap use, budgets, monitoring, no-op/pending outcomes, reconciliation, rollback, and incident response.
- [x] 11.3 Update source-review documentation for the GitHub-hosted nightly user agent, conditional requests, rate/bounds, transient retention, rotating rechecks, and change conditions that force `review_required`.
- [x] 11.4 Run Ruff, strict mypy, the full unit/integration suite, dependency/secret/license scans, deterministic static builds, HTML/link/privacy validation, and workflow policy tests; record and resolve all failures.
- [ ] 11.5 Perform a fixture-only GitHub Actions dry run and verify the Pages artifact, generated-content candidate, logs, summaries, and retained artifacts contain no private or forbidden material.
- [ ] 11.6 Perform an authorized one-case live dry run with deployment disabled, measure Court requests/bytes/runtime/model use/cost, inspect every public/state file and log, and tune the initial nightly budgets.
- [ ] 11.7 Bootstrap the protected generated-content branch, manually deploy the validated first release, verify every public route/base path/canonical/source link/search/disclosure, and exercise release-ID reconciliation and rollback.
- [ ] 11.8 Enable the nightly schedule only after license/source/privacy/legal-status/grounding/accessibility/Pages launch gates pass, then revoke legacy production credentials and document the first successful nightly publication.
