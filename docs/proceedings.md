# Official proceedings MVP

## Pivot

The Leesburg receiver cannot reliably decode the District public-safety radio system. The launch-critical source path therefore uses approved official government proceedings: Supreme Court oral arguments, House floor sessions, DC Council hearings/meetings, and DC mayoral briefings. The existing HackRF/Pi code is retained but dormant.

## Data flow

1. A fail-closed registry authorizes a source-specific adapter and host allowlist.
2. Discovery creates one authority-scoped proceeding and immutable schedule revisions.
3. Approved live media is checkpointed into bounded private chunks; approved archives become separate media revisions. Official documents are independently versioned.
4. Shared normalization and STT infrastructure creates private proceeding-relative transcript segments. Overlap is reconciled deterministically and capture gaps remain explicit.
5. Schema-constrained extraction creates evidence-linked observations. Spoken and documentary evidence remain distinct, and speaker identity requires affirmative official evidence.
6. Correlation groups evidence into proceeding topics and durable government events without converting debate or announcements into final action.
7. Default-deny policy creates sanitized approved claims. Grounded generation writes an hourly national or District story, and the public service reads only an atomic public projection.

## Shared and separate components

Object storage, PostgreSQL jobs, model clients, evidence validation, replay concepts, grounded generation, projection activation, metrics, and Kubernetes operations are reused. Proceedings have separate contracts and tables because dockets, bills, agendas, participants, motions, votes, orders, and legal status cannot be represented safely as radio calls or emergency incidents.

## Privacy and access boundaries

Collectors may access only enabled sources and authority-scoped private object prefixes. Analysis workers can read copied media, documents, and transcripts. Publishers read structured proceeding/event evidence and write sanitized claims and projections. The public role can read only the active public projection; it cannot read media, transcripts, extracted documents, private participant names, prompts, credentials, or bypass media URLs.

Official authorship does not automatically authorize automated capture from an embedded platform. The system does not use `yt-dlp`, arbitrary URL scraping, undocumented extraction, or access-control bypass. Unsupported media remains disabled while official metadata/documents may be used only through separately approved methods.

## Status and identity rules

A question is not a holding; argument and testimony are not adopted policy; House passage is not enactment; a Council committee action is not necessarily full-Council action; and an announcement is not implementation. Public officials can be named only from an official roster with reliable turn mapping, authoritative captions/transcripts, or an explicit introduction. Private witnesses and members of the public are unnamed by default, and sensitive personal testimony is sanitized or suppressed.

## Local workflow

```bash
uv sync --dev
cp .env.example .env
uv run pytest
uv run ruff check .
uv run mypy
```

Use `config/proceedings.yaml` for non-secret source and editorial defaults. Keep all sources disabled in development unless using recorded fixtures or an explicitly reviewed endpoint. PostgreSQL environments apply `migrations/001_initial.sql`, `002_roles.sql`, then `003_proceedings.sql`.
