.PHONY: sync format lint typecheck test schemas ingest discover import integrity generation backfill
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
	uv run scotus-pipeline schemas
# Pass additional CLI options with ARGS='...'.
ingest:
	uv run scotus-pipeline reconcile $(ARGS)
discover:
	uv run scotus-pipeline discover $(ARGS)
import:
	uv run scotus-pipeline import-corpus $(ARGS)
integrity:
	uv run scotus-pipeline integrity $(ARGS)
# Reserved entry points for later OpenSpec phases; intentionally do not invoke an LLM yet.
generation:
	@echo 'Guide generation is implemented in tasks 5-6, not in the archive foundation.'; exit 2
backfill:
	@echo 'Generation backfill is implemented in task 8; use import for PDF backfill.'; exit 2
