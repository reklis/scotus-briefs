# Security and privacy boundaries

## Production reader boundary

GitHub Pages serves inert validated files. Readers receive no application server,
runtime API, database, object store, Kubernetes endpoint, model call, or secret. The
retired manifests are under `deploy/k8s/dormant/` and are not part of the active
Kustomization. GitHub controls Pages transport headers, so the site avoids inline or
untrusted HTML and uses no reader-specific data.

## Trusted build boundary

Only the protected default-branch nightly/manual build can contact reviewed Court
sources or the loopback Ollama runtime. The build job can never run for pull requests;
forks and pull requests remain fixture-only in CI with read-only permissions. Actions
and container images are immutable-pinned, installs use
`uv.lock` with `--frozen`, concurrency is serialized without cancellation, and jobs
have explicit timeouts.

The protected `scotus-publication` build runs on the persistent self-hosted aarch64
Spark runner. It receives no model secret and can configure only the validated
`http://127.0.0.1:11434/v1` endpoint. Pre- and post-build cleanup remove prior source,
candidates, and the mode-0700 private workspace; none is cached or uploaded. No Docker
services are started. Deploy receives
only `pages:write` and `id-token:write`; promotion receives only `contents:write`.
Neither receives build secrets or obsolete reader credentials.

## Source and model privacy

Source access is fail-closed to reviewed HTTPS `www.supremecourt.gov` hosts/paths,
no-redirect retrieval, conditional requests, one-second minimum pacing, and bounded
responses. Changed access conditions set `review_required`. Missing or malformed
material cannot remove an existing public case.

Only bounded evidence windows or sanitized approved claims may be sent to the exact
local Ollama model `qwen3.8:27b`. The OpenAI SDK targets Ollama's compatible `/v1`
interface with a non-secret placeholder key, disabled environment proxies/redirects,
and preserved JSON-schema chat completions. Both workflow and adapter verify the exact
installed model before model input is sent. Attempt and token limits are checked before
sending; configured local model rates and maximum cost are zero. Logs and summaries
contain only public case keys, stages, coarse
status/error categories, safe counts/digests/timings, and release IDs—never response
bodies, transcript text, prompts/model payloads, signed URLs, credentials, or private
stack traces.

## Public-state boundary

The Pages candidate and generated-content candidate receive strict schema,
allowlist/denylist, integrity, link, and textual privacy scans. Forbidden content
includes PDFs/media signatures, source HTML/text, extracted transcripts, observations,
claim ledgers/internal UUIDs, prompts/model output, object keys/private paths,
credentials, and stack traces. Opaque cost receipts cannot advance the active release.
All publication and branch updates use expected-parent compare-and-swap checks.

Repository policy scans tracked files and Docker context exclusions. Secret scanning,
push protection, dependency updates/audits, image scanning, and private vulnerability
reporting should be enabled in repository settings. Report concerns through the
private process in `SECURITY.md`; never put sensitive evidence in a public issue.
