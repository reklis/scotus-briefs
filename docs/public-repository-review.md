# Public repository content review

Review scope includes tracked fixtures, validation reports, `.pi`/OpenSpec planning
artifacts, dormant radio/proceeding code, deployment examples, and operator scripts.

- `tests/fixtures/` is governed by its README: all content is invented/synthetic;
  binary Court/media fixtures are prohibited.
- Validation reports describe synthetic/private-preview methodology and aggregate
  acceptance results. They are documentation, not production exports, and must not
  gain source text, model payloads, credentials, private paths, or internal IDs.
- `.pi/` and `openspec/` contain reusable local agent instructions and product design
  records. They are intentionally public development documentation and contain no
  session transcripts, credentials, or private workspace output.
- DCFD radio and non-Supreme proceeding code, edge files, and configuration are
  dormant legacy product paths. They are not built into the Pages artifact and may
  not be reactivated without a separate source/privacy review.
- `deploy/k8s/dormant/`, database backup scripts, dynamic public code, and Compose are
  isolated migration/local-test material, not supported production operations.

`scripts/check-public-repository.py` continuously checks required policy files,
prohibited binary/private document classes, common credential forms, immutable action
references, Docker-context exclusions, immutable release images, and fail-closed
launch approvals. Human review remains required because pattern scanning cannot prove
that prose is non-sensitive or establish third-party rights.
