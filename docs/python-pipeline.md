# Python pipeline

Python 3.12+ and [uv](https://docs.astral.sh/uv/) are required. `uv.lock` is committed; use
`uv sync --frozen` to reproduce the environment.

| Command | Purpose |
|---|---|
| `make format` | Format Python with Ruff |
| `make lint` | Run Ruff checks |
| `make typecheck` | Run strict mypy |
| `make test` | Run pytest |
| `make schemas` | Rebuild canonical JSON Schemas |
| `make discover ARGS='--sources sources.json'` | Discover official metadata |
| `make ingest ARGS='--candidates reports/discovery.json'` | Validate/archive discovered PDFs |
| `make import ARGS='--jsonl manifest.jsonl --corpus-root official'` | Import historical PDFs |
| `make integrity` | Hash every manifest blob and report missing/orphan files |

`make generation` and `make backfill` are reserved and intentionally fail until the later
generation/orchestration phases are implemented. No foundation command contacts Ollama.

The archive defaults to `documents/<first-two-hash-characters>/<sha256>.pdf`, its provenance
manifest to `manifests/documents.json`, and operation reports to `reports/`. Source failures are
non-destructive. An empty discovery is an error unless an operator explicitly passes
`--allow-empty` to reconciliation. Import paths are constrained beneath the supplied corpus
root, and ambiguous groups become unpublished `unresolved-*` case records rather than receiving
invented metadata.
