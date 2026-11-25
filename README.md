# SCOTUS Briefs — A Citizen's Guide to the Supreme Court

A simple pipeline that monitors the U.S. Supreme Court docket, downloads primary filings, passes them to an LLM to produce plain-English explanations for regular people, and builds a zero-JavaScript static site for [scotusbriefs.us](https://scotusbriefs.us/).

## How it works

Five small Python modules under `scotus/`:

- `scotus/docket.py` — Discovers active dockets from `supremecourt.gov`, parses full docket proceedings (petitions, briefs, orders, transcripts, opinions), and archives PDFs to `documents/`.
- `scotus/extract.py` — Extracts clean text from archived PDFs and builds a concise case briefing.
- `scotus/explain.py` — Calls any OpenAI-compatible LLM (Ollama, Gemini, OpenAI, etc.) to write a plain-English citizen's guide saved in `data/explanations/`.
- `scotus/site.py` — Renders a fast, zero-JS static HTML site + Atom feed into `dist/`.
- `scotus/cli.py` — Four commands: `monitor`, `explain`, `build`, and `run`.

## Quick start

```bash
# Install dependencies
uv sync

# Configure your LLM (defaults to Spark at 192.168.1.41:8080; works with any OpenAI-compatible server)
export LLM_BASE_URL="http://192.168.1.41:8080/v1"
export LLM_MODEL="qwen3.8-flash-next-iq3_s"
export LLM_API_KEY=""                              # required for cloud providers

# Run everything for a specific docket (or omit the docket to monitor the current term)
uv run scotus run 24-297

# Or run each step individually
uv run scotus monitor --term 2024 --limit 10
uv run scotus explain
uv run scotus build
```

Open `dist/index.html` in a browser to preview the generated site.

## Tests

```bash
uv run pytest
uv run ruff check scotus tests
```

## Disclaimer

Explanations are AI-generated for civic education, are **not legal advice**, and always link directly to the official Supreme Court docket and filings on `supremecourt.gov`.
