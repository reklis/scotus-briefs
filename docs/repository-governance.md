# Repository governance and publication controls

## Ownership and review

`CODEOWNERS` requests review from the repository owner. Require at least one approving
review and dismissal of stale approvals on the default branch. Require CI, workflow
policy, static contract/privacy, and deterministic export checks. Prohibit force
pushes, branch deletion, and direct pushes except documented break-glass recovery.
Workflow files, source access, public contracts, licenses, and privacy scanners need
owner review.

Enable Dependabot security updates, secret scanning, push protection, dependency
graph/review, code scanning where available, and **private vulnerability reporting**.
Actions must be restricted to reviewed actions pinned to full commit SHAs. Keep the
default `GITHUB_TOKEN` read-only; grant write permissions only per job.

## Protected publication boundaries

Create these protected environments:

- `scotus-publication`: trusted self-hosted build only; contains no model secret;
  require owner approval for manual bootstrap/publication and prevent untrusted branches.
- `github-pages`: deployment only; contains no secrets; allow only the pinned Pages
  workflow and require the desired deployment review policy.

Restrict `workflow_dispatch` publication to repository owners/maintainers. Protect the
orphan `generated-content` branch from direct pushes, require the workflow's guarded
compare-and-swap update, and retain branch history. The build job has read-only
contents and loopback-only Ollama access; deploy has only `pages:write` and
`id-token:write`; receipt/promotion/checkpoint jobs remain GitHub-hosted with only the
required `contents:write` and no source/model settings or secrets.

Keep all launch gates false until source, privacy, license, legal-status, grounding,
accessibility, canonical-origin, model-runtime, and launch approvals are recorded. A manual
“deploy” input is a request, not a bypass.

## Changes and incidents

Do not edit a deployed release or active generated snapshot in place. Roll back by
redeploying a previously validated release and reconciling its pointer. Pause the
workflow for unexplained source changes, privacy findings, compromised actions or
credentials, Ollama/model-identity changes, unexpected nonzero model cost, or a
Pages/branch release split. Follow
`docs/pages-operations.md` and preserve only sanitized release IDs, digests, timings,
and counts in incident notes.
