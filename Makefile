.PHONY: sync format lint typecheck test schemas ingest discover import integrity generation backfill validate
sync:
	uv sync --frozen
format:
	uv run ruff format .
lint:
	uv run ruff check .
typecheck:
	uv run mypy
test:
	uv run pytest
test-all: lint typecheck test
schemas:
	uv run scotus-guide schemas
# Pass additional CLI options with ARGS='...'.
ingest:
	uv run scotus-guide ingest --mode incremental --batch-size 25 $(ARGS)
discover:
	uv run scotus-guide discover $(ARGS)
import:
	uv run scotus-guide import-corpus $(ARGS)
integrity:
	uv run scotus-guide integrity $(ARGS)
generation:
	uv run scotus-guide run --mode incremental --batch-size 25 $(ARGS)
backfill:
	uv run scotus-guide run --mode backfill --batch-size 10 $(ARGS)
validate:
	uv run scotus-guide run --mode validate --batch-size 1 $(ARGS)
