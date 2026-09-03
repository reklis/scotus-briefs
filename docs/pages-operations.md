# GitHub Pages operations runbook

Production repository: `reklis/scotus-briefs`  
Canonical site: <https://reklis.github.io/scotus-briefs/>  
Nightly schedule: 03:17 UTC (`17 3 * * *`)

## One-time owner settings

1. Protect the default branch: require review, dismiss stale reviews, require CI and
   static/workflow policy checks, block force pushes/deletion/direct pushes.
2. Enable Actions with read-only default `GITHUB_TOKEN`; allow only reviewed actions.
   Enable dependency/security updates, secret scanning/push protection, and private
   vulnerability reporting.
3. Create protected `scotus-publication` environment, restrict it to the default
   branch, and require owner approval for manual jobs. Add no model secret.
4. Register the aarch64 Spark runner with the repository. The only assumed label is
   `self-hosted`. Run Ollama as a host service listening only on `127.0.0.1:11434`,
   install exact model `qwen3.8:27b`, and do not expose the port to a network.
5. Configure Pages for GitHub Actions and protect `github-pages`. It has no secrets.
6. Protect `generated-content` against human/direct updates while permitting the
   workflow's guarded `contents:write` promotion.
7. Keep every config launch approval, model generation, and publication switch false
   through fixture and live-no-deploy review. Enabling a schedule is a separate final
   owner action.

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

The exporter reads public projections, not source/claim/model tables. It refuses to
invent missing historical case-revision bodies. Independently inspect every file and
scan from a separate clean checkout. Then create an orphan branch, preserving exactly
the candidate bytes:

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
A manual `deploy=false` run still builds, privacy-scans, and validates the candidate and
opaque receipts locally, but uploads neither candidate state nor receipts and persists
nothing to `generated-content`. This prevents a discarded candidate from making its
model inputs unreplayable. The tradeoff is deliberate: a runner crash or later dry-run
rerun can repeat model calls, though local configured cost is zero, because no durable
receipt survives. Restrict
bootstrap/deploy dispatch to owners. Never add PR/fork or arbitrary-ref triggers.

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
file digests, redeploy those exact bytes, then CAS-update active/previous pointers.
Never regenerate, patch, or amend an old release. Verify root/SCOTUS/case/archive/
search/correction/404 routes, canonical URLs, official links, disclosure, release ID,
and branch/Page agreement.

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
