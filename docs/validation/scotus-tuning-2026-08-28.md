# SCOTUS fixture tuning and replay — 2026-08-28

Documented failures found during the bounded validation pass and their corrections:

1. **Encrypted PDF coverage gap:** ingestion rejected encryption in code, but fault injection did not exercise it. Added an encrypted `pypdf` fixture and confirmed fail-closed rejection with no committed object.
2. **Collector privilege mismatch:** the collector attempted to provision a missing source registry row despite its reviewed least-privilege role. Provisioning is now operator-only; collection fails closed when the reviewed row is absent or its adapter differs.
3. **Runtime backlog scaling gap:** the analyzer deployment lacked the planned autoscaling object. Added a bounded 1–4 replica HPA with conservative scale-down stabilization.
4. **Launch kill-switch gap:** source collection was disabled, but the public runtime did not independently enforce a publication gate. Added typed `publication.enabled: false`; the runtime returns 503 until explicitly enabled.
5. **Correction/rollback integration gap:** individual tests existed, but revised-transcript correction and failed projection activation were not replayed as one path. Added an end-to-end fixture test preserving the prior projection.

Affected discovery, ingestion, parsing, extraction, correlation, generation, publication, deployment, and launch-validation tests were rerun after these changes. No threshold was relaxed and no failed evidence was edited into a passing result.
