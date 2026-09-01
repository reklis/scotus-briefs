# Private ingestion protocol

1. The edge sends an authenticated `POST /v1/receivers/{receiver}/captures` containing a versioned capture envelope.
2. The service returns a short-lived private object upload URL scoped beneath `receivers/{receiver}/`.
3. The edge uploads with the declared content type and `sha256` object metadata.
4. The edge commits through `POST /v1/receivers/{receiver}/captures/{capture}/commit`.
5. The service verifies digest, size, and content type, then atomically marks the capture ready and creates one transcription job.
6. Only after the commit acknowledgement may the edge age the spool item out.

Retries use the same capture ID. Matching retries return the existing state; conflicting content is rejected. Receiver credentials must be delivered over TLS and scoped to one receiver. Public workloads receive neither private bucket credentials nor ingestion routes.
