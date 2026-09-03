# Contributing

Thank you for helping improve SCOTUS Legal Briefs.

## Setup and checks

Install Python 3.12 and [uv](https://docs.astral.sh/uv/), then run:

```bash
uv sync --frozen --dev
uv run ruff check .
uv run mypy
uv run pytest
uv run python scripts/check-public-repository.py
```

Keep changes small, add tests, preserve deterministic output, and explain privacy,
source-access, public-contract, or deployment effects in the pull request. Reviewers
must treat changes to public schemas, source access, prompts/models, licenses, privacy
scanners, workflows, and release manifests as security-sensitive.

## Public repository data rules

- Use only invented fixtures. Mark a fixture or its fixture directory explicitly as
  synthetic; never paste a real transcript excerpt, source body, model response, or
  production record into a test.
- Do not commit Court PDFs, audio/video, full transcripts, extracted source text,
  prompt/model dumps, database/object-store exports, credentials, private paths,
  stack traces containing evidence, or internal claim/document/observation IDs.
- Link to reviewed official Court sources instead of redistributing source documents.
- Do not weaken ignore rules or privacy scanners to make a test pass. Request review
  if a new fixture form is necessary.
- Generated-content is written only by protected automation after contract, link,
  integrity, and privacy validation. Do not edit its active release by hand.

Public pull requests and forks are fixture-only. They must not contact live Court or
model endpoints and never receive publication secrets.

## Contribution licensing

By submitting a contribution, you represent that you have the right to submit it and
agree to license repository-authored code and documentation under Apache-2.0. If a
contribution intentionally supplies original generated-brief content, you agree to
license that content under CC BY 4.0. Do not contribute third-party material unless
its inclusion and notice have been approved. Copyright remains with contributors.

All changes require passing checks and review under the ownership rules in
`docs/repository-governance.md`. Maintainers may require source/legal, security,
accessibility, or generated-output review before acceptance.
