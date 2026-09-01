# Private object policy

Apply `deploy/s3/private-bucket-policy.json` after replacing role ARNs and the bucket name. Block all public access and object ACLs.

- The SCOTUS collector can write/read only `official/us_supreme_court/supreme_court/*` objects.
- The analyzer can read only that Court-document prefix.
- Retention can read/delete only that prefix.
- The publisher and public site receive no bucket principal, credential, or presigned-document capability.

The application additionally scopes every key by case and document kind/revision and rejects host/path changes before upload. If using MinIO, translate the statements into equivalent per-user policies and require TLS at the endpoint or service mesh. Copied PDFs are short-lived private processing material; retain only approved digests and official provenance after deletion.
