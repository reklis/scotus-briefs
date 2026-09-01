# SCOTUS Legal Briefs

Ragchew is being focused into **SCOTUS Legal Briefs**: a transcript-first pipeline that turns complete official Supreme Court oral-argument transcripts into evidence-grounded case analysis. The MVP does not download argument audio or run speech-to-text.

## Active service boundaries

- `ragchew.scotus`: versioned case, docket, argument, transcript, legal-observation, claim, and brief contracts.
- `ragchew.proceedings.sources.supreme_court`: reviewed Court-hosted argument, transcript, docket, order, and opinion discovery.
- document collector/parser: private PDF retrieval, validation, page/line extraction, speaker turns, and immutable revisions.
- legal extraction/correlation: typed and attributed questions, contentions, authorities, requested dispositions, orders, and holdings.
- policy/generation: default-deny approved claims and grounded structured legal briefs.
- `ragchew.public`: read-only public projections for term, case, search, provenance, and correction pages.

Radio capture and non-Supreme government proceeding code remains dormant. All sources, including Supreme Court publication, remain disabled until their private validation gates pass.

## Local development

The checked-in Devbox environment provides Python 3.12, uv, Ruff, PostgreSQL 16 tools, Docker/Compose, kubectl, and the MinIO client:

```bash
devbox shell
devbox run sync
cp .env.example .env
devbox run check
```

A running Docker daemon is still required for container-backed integration and deployment checks.

`config/scotus.yaml` is the active non-secret product configuration. It explicitly disables audio download, STT, paid brief generation, and publication. Brief generation has a one-call-per-run default budget when explicitly enabled. `config/proceedings.yaml` and `config/mvp.yaml` retain dormant prior paths.

See [`docs/scotus-legal-briefs.md`](docs/scotus-legal-briefs.md) for architecture and workflow.

## Safety boundary

Public analysis is automated, delayed, incomplete, non-authoritative, not an official Court record, not legal advice, and not a prediction of any justice's vote or case outcome. Questions are not holdings; advocate assertions remain attributed; final Court action requires official order/opinion evidence. Copied PDFs and full extracted transcript text are private and are not redistributed.
