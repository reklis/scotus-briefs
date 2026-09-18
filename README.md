# SCOTUS Briefs — A Citizen's Guide to Supreme Court Cases

This repository archives primary Supreme Court PDFs and publishes plain-language, source-cited case guides at [scotusbriefs.us](https://scotusbriefs.us/).

The guides are independent, AI-generated explanations for civic education. They can be incomplete or mistaken, are **not legal advice**, and do not replace the Court's opinions, orders, transcripts, or filings. Every published guide exposes its primary sources so readers can verify it.

## Repository layout

- `documents/` — immutable, content-addressed PDF archive (not included in Pages)
- `manifests/` — source provenance and revision records
- `data/cases/` — normalized case metadata
- `data/evidence/` — page-cited extracted evidence
- `data/guides/` — verified, accepted guide records
- `data/checkpoints/` — resumable batch progress
- `reports/` — pipeline diagnostics
- `pipeline/` — downloader, archive, extraction, and generation tooling
- `schemas/` — shared data contracts
- `site/` — static SvelteKit publication

See [`docs/operations.md`](docs/operations.md) for setup, common commands, Ollama and ARM64 `spark` runner configuration, GitHub Pages operation, policy, and recovery. Python archive commands are covered in [`docs/python-pipeline.md`](docs/python-pipeline.md), and authoritative discovery sources in [`docs/official-sources.md`](docs/official-sources.md). The implementation plan is under [`openspec/changes/launch-citizens-scotus-guide/`](openspec/changes/launch-citizens-scotus-guide/).
