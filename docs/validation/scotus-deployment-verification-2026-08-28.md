# SCOTUS local deployment verification — 2026-08-28

Command: `scripts/validate-scotus-deployment.sh`

Result: **passed** using the repository's PostgreSQL 16 container path and rendered Kubernetes deployment.

Verified:

- all migrations apply from an empty database;
- a non-empty case, transcript digest, parse, observation, case-history entry, approved claim, two-revision correction history, and active public projection survive custom-format backup and restore;
- backup/restore preserves ACLs as well as data;
- the public role can read the active sanitized projection view and cannot read private case tables;
- retention can remove a copied-object location while preserving digest/provenance;
- correction provenance remains append-only after restore;
- SCOTUS alerts render in the Kubernetes manifest;
- source and public publication kill switches remain false;
- Kubernetes resources and network policies render successfully.

During this verification, the prior backup scripts' `--no-privileges` option was found to strip restored public/private ACLs. Backup and restore now preserve ACLs while continuing to omit environment-specific ownership. The failed check was rerun from a fresh database and passed.
