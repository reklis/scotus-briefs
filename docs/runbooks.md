# SCOTUS Legal Briefs processing runbooks

Production publication/rollback/incident procedures are in
[`pages-operations.md`](pages-operations.md). Kubernetes, PostgreSQL, and object-store
steps below apply only to a still-unretired legacy installation or isolated migration;
GitHub Pages has no such runtime dependencies.

## Source contract, review, or polling failure

Check the `supreme_court` registry entry, review expiry, Court robots policy, configured term index, response status, redirects, host/path allowlist, ETag checkpoint, crawl delay, and recorded contract fixture. A redirect, host/path change, or access-method change is a review event—not a reason to follow the new destination automatically. Disable the source until reviewed.

## Official transcript delay

Confirm the argument detail page itself has an official transcript PDF link. An MP3-only argument remains `transcript_pending`; do not enable audio download or STT as a fallback. Distinguish normal Court publication delay from polling failure and record observed latency.

## Document failure or digest conflict

For retrieval failures, inspect status, MIME, size, PDF signature, encryption, page count, and private object-store availability. A digest conflict under one source identity is quarantined. Preserve both provenance records and create/approve a new document revision only after verifying the Court-hosted change; never overwrite canonical bytes silently.

## PDF parser failure

Compare file page count, extracted page order, printed page/line structure, repeated headers/footers, and official speaker labels against the PDF. Empty/ambiguous pages, unsupported encryption, missing pages, or malformed labels fail closed. Pin parser/config changes, replay privately, and compare every changed observation before making the new parse canonical.

## Model backlog or extraction failure

Inspect `collect`, `parse`, `extract`, `correlate`, `policy`, and `publish` queue depth/leases independently. Scale only the affected worker within database, model, and Court rate limits. Citation, quote, speaker, status, grounding, or sensitivity validation failures require evidence/model review rather than blind retry.

## Grounding or sensitive-data denial

Review only private source ranges and structured model output. Do not weaken zero-tolerance rules for invented citations, vote/outcome predictions, question-as-holding language, private names, addresses, medical facts, or sealed/redacted material. Correct the parser, extraction prompt/schema, or source observations and replay.

## Publication failure or correction

A failed static cycle leaves the prior Pages release and generated-content active
pointer unchanged. Disable the scheduled GitHub workflow until the cause is
understood. A transcript/order/opinion change produces an append-only public case
revision with a visible correction note. Never patch generated JSON directly. If
Pages and branch release IDs differ, stop normal publication and follow the
reconciliation procedure in `pages-operations.md`.

## Retention or private-access denial

The ephemeral job must remove its entire private workspace unconditionally and must
not cache or upload it. Preserve only validated public hashes, official URLs,
sanitary page labels, immutable public revisions, pending reasons, and opaque cost
receipts. Any retained PostgreSQL/object data belongs to an isolated legacy migration
and must never be exposed to Pages.

## Legacy backup and recovery

`scripts/backup-postgres.sh` is legacy operator tooling, not production Pages backup.
Use it only with a restricted DSN and encrypted volume during migration. Restore into
an isolated database, export only retained public projections through the sanitizer,
then destroy the restore and rotate credentials. Production recovery redeploys an
immutable validated generated-content release rather than restoring a database.

## Dormant legacy paths

Radio receiver, edge spool, and non-Supreme proceeding alerts are not launch signals for SCOTUS Legal Briefs. Keep their sources and schedules disabled unless a separate reviewed change reactivates them.
