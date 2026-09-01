# SCOTUS fault-injection baseline — 2026-08-28

Command: `scripts/validate-scotus-faults.sh`

Result: **56 focused tests passed**, followed by successful Kubernetes rendering and shell validation.

Covered failures and invariants:

- source authorization expiry, access-method changes, unapproved hosts, and redirects;
- quiet source versus endpoint failure, retry backoff, conditional requests, and backfill caps;
- MP3-only argument creates no audio/STT job;
- interrupted document stream commits no object;
- false MIME/PDF signature, malformed PDF, encrypted PDF, and response-size controls fail closed;
- same digest is idempotent, same revision with changed bytes is quarantined, and explicit new revision is accepted;
- transcript page/line coordinates, header/footer artifacts, empty pages, malformed labels, and anonymous fallback;
- bounded model context, quote/citation/speaker validation, attribution, sensitive facts, and replay idempotency;
- transcript questions/requests cannot finalize a case; opinion evidence can;
- vote/outcome predictions, question-as-holding language, ideology/tone claims, quotations, unsupported citations, and personalized legal advice are rejected;
- public route/private identifier denial, disclosure and source-label presence, correction history, and failed projection rollback;
- end-to-end transcript fixture through parse, extraction, case state, approved claims, brief, projection, revised-transcript correction, and last-known-good rollback.

This is a deterministic fixture baseline, not the required deployed seven-day private preview. It does not enable the source or public launch.
