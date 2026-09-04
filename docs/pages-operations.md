# GitHub Pages operations runbook

Production repository: `reklis/scotus-briefs`  
Canonical site: <https://scotusbriefs.us/>
Nightly schedule: 03:17 UTC (`17 3 * * *`)

## Current launch status

The validated empty-state site was first deployed on 2026-09-03. The same day, the
accepted local POC corpus was recovered through the read-only sanitizer and deployed:
1,710 active articles, 1,875 immutable revisions, 26 terms, and 2,974 HTML pages. Live
Pages release `90df36da587af2ebd277111ebf8b056bacf6c3aef55f7ac2620a458fbed7478e`
matches the protected `generated-content` branch. The root home page is the searchable
case interface, initially showing results 1–20 of 1,710 in deterministic site order.
DNS is valid at GitHub; custom-domain
certificate issuance and HTTPS enforcement are still pending.

The owner enabled generated-case processing on 2026-09-03 and removed the one-case
throughput throttle after launch testing. A run may now drain up to 100 cases and 1,100
zero-cost local-model calls, continuing past individual case validation failures. Court
rate controls, source authorization, a five-hour processing bound, private-disk and
retrieval limits, and every per-case publication validator remain mandatory. The shorter
application bound reserves time for receipt upload, candidate validation, and runner cleanup
inside the 5-hour-30-minute build-job limit. Accepted
legacy imports with no logical-document checkpoint are excluded from processor-migration
and rotating redownload work; they re-enter processing only when current Court discovery
reports a metadata change or an owner explicitly requests a backfill. Prior dry runs
confirmed the Court and loopback
Ollama paths but failed closed on extraction/runtime validation. Those same grounding,
privacy, completeness, static, and release validators remain mandatory: an unsuccessful
run cannot replace the migrated accepted POC corpus or advance its active release. A
schedule-equivalent cycle (`33789697197`) completed successfully through pre-TLS
reconciliation and checkpoint promotion. The first cycle to select model work after the
legacy-checkpoint compatibility fix (`33790480565`) downloaded three documents in 18
requests (2,344,867 bytes) and made two zero-cost extraction calls. Grounding validation
rejected the result before the brief stage, so it did not deploy or promote public content.
An explicit reviewed replay (`33796212480`) produced the same fail-closed result after
three documents, 18 requests, 2,362,990 bytes, and two extraction calls. A later bounded
run reduced the stale queue from 1,716 entries to 29 genuine new/changed cases and reached
brief generation, but no attempted case passed final validation before runtime exhaustion.
Extraction now supplies exact source identity fields, deterministically derives provenance
from the referenced evidence block, uses smaller evidence windows, and reports only fixed
safe failure codes. Brief generation uses a simplified strict local-model schema without
manufacturing claim coverage, and one fully budgeted retry is allowed only for retryable
loopback transport failures. One-case live dry run `33912374845` then completed end to end
in 29 minutes, including one fixed-code brief correction, and produced a privacy-scanned
1,711-case/2,976-page candidate release without deployment.

## One-time owner settings

1. Protect the default branch: require review, dismiss stale reviews, require CI and
   static/workflow policy checks, block force pushes/deletion/direct pushes.
2. Enable Actions with read-only default `GITHUB_TOKEN`; allow only reviewed actions.
   Enable dependency/security updates, secret scanning/push protection, and private
   vulnerability reporting.
3. Create protected `scotus-publication` environment, restrict it to the default
   branch, and require owner approval for manual jobs. Add no model secret.
4. Register the aarch64 Spark runner with the repository. The installed runner is
   `spark`, pinned at version `2.337.0`, runs as the non-root `gdxspark` system service,
   and advertises `self-hosted`, `Linux`, `ARM64`, and `spark`; the workflow assumes only
   `self-hosted`. Run Ollama as a host service listening only on `127.0.0.1:11434`,
   install exact model `qwen3.8:27b`, and do not expose the port to a network.
5. Configure Pages for GitHub Actions with custom domain `scotusbriefs.us`, enforce
   HTTPS after GitHub accepts the domain, and protect `github-pages`. It has no secrets.
   DNS is managed separately by the owner; this repository change makes no DNS changes.
6. Protect `generated-content` against human/direct updates while permitting the
   workflow's guarded `contents:write` promotion.
7. Keep the processing, model-generation, generated-case launch approval, and live
   publication switches fail-closed through fixture and live-no-deploy review. Record
   foundational source/license/origin/runtime approvals independently. Enabling new
   generated-case publication is a separate final owner action.

These repository/environment/secret/protection operations cannot be completed by a
local checkout.

## Bootstrap the generated-content branch

Create the first candidate independently of its future branch. Empty bootstrap is
preferred:

```bash
umask 077
uv run python scripts/create-empty-generated-bootstrap.py \
  --output "$PWD/bootstrap-candidate" \
  --source-commit "$(git rev-parse HEAD)" \
  --config-sha256 "$(sha256sum config/scotus.yaml | cut -d' ' -f1)" \
  --build-epoch 2026-08-28T03:17:00Z
```

For a legacy migration instead, use a read-only DSN that can select retained
`scotus_public_projections` only:

```bash
umask 077
RAGCHEW_DATABASE_DSN='postgresql://…' uv run python \
  scripts/export-scotus-legacy-bootstrap.py \
  --output "$PWD/bootstrap-candidate" \
  --source-commit "$(git rev-parse HEAD)" \
  --config-sha256 "$(sha256sum config/scotus.yaml | cut -d' ' -f1)" \
  --build-epoch 2026-08-28T03:17:00Z
uv run ragchew-scotus-static validate \
  --state-dir bootstrap-candidate --privacy-scan
```

The legacy exporter reads public projections, not source/claim/model tables. It refuses
to invent missing historical case-revision bodies.

If and only if the local POC has **no rows** in `scotus_public_projections` but has
accepted `scotus_brief_revisions`, use the separate operator recovery path. Give it a
read-only local DSN and a clean checkout of the existing generated-content parent:

```bash
umask 077
RAGCHEW_DATABASE_DSN='postgresql://…' uv run python \
  scripts/export-scotus-poc-briefs.py \
  --parent-state /absolute/path/generated-content-parent \
  --output "$PWD/poc-recovery-state" \
  --site-output "$PWD/poc-recovery-site" \
  --config config/scotus.yaml \
  --source-commit "$(git rev-parse HEAD)" \
  --config-sha256 "$(sha256sum config/scotus.yaml | cut -d' ' -f1)" \
  --build-epoch 2026-08-28T03:17:00Z
```

This command starts a repeatable-read, read-only transaction and fails if any public
projection exists. Legacy claim UUIDs embedded in prose are replaced with the neutral
text `official source`; this is required by the current public privacy contract and does
not change official provenance links. Its SQL allowlist reads only accepted brief public fields, approved
claim URL/label/page provenance, public-relevant case metadata, complete argument
session official URLs, status history, and ready/parsed canonical disposition official
URLs. It does not read transcript text, source or model bodies, observations, object
keys, credentials, or claim evidence/private values. The candidate retains the
parent's opaque ledger and checkpoint state and appends every reconstructed case
revision. It renders the static site first, attaches that exact file manifest with the
parent release ID, and cross-validates site and state before returning. Record the
printed parent digest and release IDs.

Independently inspect every candidate file and, from a separate clean checkout, run:

```bash
uv run ragchew-scotus-static validate \
  --output "$PWD/poc-recovery-site" \
  --state "$PWD/poc-recovery-state" \
  --config config/scotus.yaml --privacy-scan
```

Before merging or promoting, verify the generated-content checkout still has the
printed parent digest and release ID; otherwise discard both candidates and rerun.
The orphan-branch instructions below apply only to an initial bootstrap, not this POC
recovery. For an initial bootstrap, preserve exactly the candidate bytes:

```bash
git switch --orphan generated-content
git rm -rf .
cp -a /absolute/path/bootstrap-candidate/. .
git add snapshot/v1 state/v1 release/v1
git diff --cached --check
git commit -m 'Initialize sanitized generated content'
git push origin generated-content
```

Protect the branch immediately. Do not run these commands with an unscanned candidate
or from a working tree containing private inputs.

## Routine and manual runs

The pinned workflow cannot run its build for pull requests. On the protected default
branch it cleans the persistent self-hosted workspace, preflights local Ollama and the
exact `qwen3.8:27b` model, reconciles live and branch release IDs, creates a mode-0700
temporary workspace, and runs
`uv run ragchew-scotus-static`. `nightly` uses routine current/recent/rotating limits;
`bootstrap` manually drains a bounded historical slice; `fixture` uses invented local
data and can never become publication-ready.

A manual `deploy=true` requests deployment but cannot override configuration gates.
For focused launch validation, `maximum_cases` may only lower the configured case bound.
A successful manual `deploy=false` run uploads only the privacy-scanned Pages tree,
sanitized generated-state candidate, opaque receipts, and fixed handoff metadata for one
day; it does not deploy or mutate `generated-content`.

To publish that exact candidate without repeating Court downloads, extraction, or brief
model calls, manually run `deploy-validated-candidate.yml` from the protected default
branch with the successful source `candidate_run_id`. The GitHub-hosted workflow verifies
that the source run was a successful protected `publish-pages.yml` run, checks that its
source commit is an ancestor of the current protected branch, downloads and revalidates
the exact retained site/state, verifies release and parent identities, CAS-persists its
opaque receipts, deploys the Pages artifact, and CAS-promotes the matching state. Any
expired/missing artifact, changed generated-content parent, or validation mismatch stops
before deployment. An owner may set `authorized_replay=true` only on an explicit manual
`nightly` run after reviewing a prior failure; replay does not bypass any grounding,
privacy, completeness, or publication validator. Restrict bootstrap/deploy/replay and
retained-candidate deployment to owners. Never add PR/fork or arbitrary-ref triggers.

Possible outcomes:

- **no-op:** no public bytes changed; Pages deploy is skipped and validated discovery
  checkpoints may advance;
- **pending:** a bounded budget or transient source/processing failure preserved the
  existing case and recorded only a coarse reason;
- **candidate denied:** validation/privacy/integrity failure; no deploy or promotion;
- **deployed:** exact Pages artifact succeeded, then exact state was CAS-promoted;
- **split:** Pages deploy succeeded but promotion failed; normal publication stops for
  reconciliation.

Monitor workflow conclusion/duration, Court request and byte counts, selected/pending
case counts, model attempted calls/tokens (with zero estimated local cost), pre/post
cleanup status, release ID,
and Pages availability. Logs and summaries must remain sanitized. Artifacts retain
only scanned public candidates/opaque receipts for one day; private workspace is never
uploaded.

## Reconciliation and rollback

Before every run, compare `release/v1/release.json` on Pages with the branch active
release. If IDs match, continue. If live matches a retained independently validated
candidate, CAS-promote that exact state. If branch active is the last known-good,
redeploy its exact site artifact. Stop for an unknown live ID; do not generate over a
split.

Rollback means select a prior immutable validated release, validate its manifest and
file digests, including the exact root `CNAME`, redeploy those exact bytes, then
CAS-update active/previous pointers. Never regenerate, patch, or amend an old release.
Verify root/SCOTUS/case/archive/search/correction/404 routes, canonical URLs, official
links, disclosure, release ID, custom-domain routing, and branch/Page agreement.

## Incidents and legacy retirement

Pause scheduled publication for a privacy finding, credential/action compromise,
unexpected source/robots/terms/host/redirect change, Ollama availability/model identity
change, unexpected nonzero model cost, unknown
release, or repeated validation failure. Preserve only sanitized IDs/digests/counts,
rotate any affected legacy credentials, invalidate candidate artifacts, and use GitHub
private vulnerability reporting where appropriate.

After first verified static launch, operators must delete old public/analyzer/CronJob
Kubernetes resources using `deploy/k8s/dormant/README.md`, revoke reader PostgreSQL,
object-store, and legacy model credentials, and verify no DNS/Ingress routes remain.
Deletion, credential rotation, branch protection, Pages deployment, live-source/model
dry runs, and launch approval are owner-only actions and cannot be done locally.
