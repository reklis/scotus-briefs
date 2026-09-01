# Security boundaries

- The active SCOTUS collector is fail-closed to reviewed `www.supremecourt.gov` hosts, paths, access methods, response bounds, and no-redirect retrieval.
- Audio download and STT are disabled. Copied Court PDFs, full extracted transcript text, parser artifacts, legal observations, rejected claims, prompts, and model credentials are private.
- Private object keys are authority/source/case/document scoped. Digest conflicts are quarantined rather than silently overwritten.
- The public deployment receives only a read-only PostgreSQL credential restricted to the active public projection views and has no S3 or LLM secret. Access to cases, documents, extracted lines/turns, observations, and claims is revoked.
- Containers run as UID/GID 10001, drop all capabilities, use a read-only root filesystem, disable privilege escalation, and do not mount service-account tokens.
- Namespace traffic is default-deny. SCOTUS analyzer/publisher HTTPS egress is required for the official OpenAI API; production CNI FQDN policy must narrow it to `api.openai.com`. Court collection remains limited to reviewed Supreme Court destinations.
- Only sanitized, bounded evidence windows or approved claim ledgers are sent to OpenAI. Copied PDFs, uncontrolled full case records, object keys, and credentials are never included. OpenAI API keys exist only in analyzer/publisher secrets.
- Production overlays must narrow egress to actual external endpoint CIDRs/FQDNs where feasible and alert on source host/path or access-review changes.
- CI performs lint, strict type checks, tests, dependency audit, secret scanning, image build, and HIGH/CRITICAL image vulnerability scanning.
- Never commit real credentials in example secrets. Use an external secret controller or sealed secret in production.
