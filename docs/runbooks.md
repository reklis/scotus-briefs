# SCOTUS Legal Briefs operations runbooks

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

The prior active SCOTUS projection remains public when activation fails. Disable the SCOTUS publisher/collector CronJobs until the cause is understood. Transcript, order, or opinion changes produce append-only brief revisions with a visible correction/retraction note. Never patch public JSON directly.

Kill switches:

```bash
kubectl -n ragchew patch cronjob scotus-discovery -p '{"spec":{"suspend":true}}'
kubectl -n ragchew patch cronjob scotus-publisher -p '{"spec":{"suspend":true}}'
```

## Retention or private-access denial

Verify collector/parser object policy and PostgreSQL roles. The public workload must have no object or model credential and must read only `active_scotus_public_projection`. Run retention only after checking active job leases. Preserve hashes, official URLs, page/line provenance, case history, approved claims, brief revisions, and corrections.

## Backup and recovery

Run `scripts/backup-postgres.sh` with a restricted DSN and encrypted backup volume. Copied Court PDFs and full extracted transcript text intentionally follow short retention and are excluded from long-term database content after deletion. Restore into an isolated database, validate SCOTUS cases/documents/digests/observations/claims/brief revisions and the active projection, then rotate credentials before cutover. Test restore quarterly.

## Dormant legacy paths

Radio receiver, edge spool, and non-Supreme proceeding alerts are not launch signals for SCOTUS Legal Briefs. Keep their sources and schedules disabled unless a separate reviewed change reactivates them.
