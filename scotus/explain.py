"""Turn Supreme Court docket proceedings into plain-English citizen explanations via any OpenAI-compatible LLM."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .docket import CaseDocket
from .extract import build_case_briefing

DEFAULT_BASE_URL = "http://192.168.1.41:8080/v1"
DEFAULT_MODEL = "qwen3.8-flash-next-iq3_s"

SYSTEM_PROMPT = """\
You write a "Citizen's Guide to the Supreme Court" for everyday people who are not lawyers.

Rules:
- Use clear, plain, everyday English. Explain any legal term the first time you use it.
- Be strictly neutral and fair to all sides.
- Base every fact ONLY on the docket timeline and filing excerpts provided. If something is not yet decided or not in the filings, say so plainly.
- Do not invent holdings, vote counts, dates, or quotes.

Format your response in Markdown with these exact sections:
## In Plain English
A 2-3 sentence summary of what this case is about and why a regular person should care.

## The Backstory
How this dispute started and how it reached the Supreme Court.

## What Each Side Argues
Bullet points explaining the main arguments on each side in plain language.

## Where the Case Stands
What has happened on the docket so far, and what the Court decided (or what happens next if still pending).

## Legal Terms Explained
A short bullet list defining 2-5 legal terms that appear in this case, in plain English.
"""


@dataclass
class LLMConfig:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: str = ""
    temperature: float = 0.2
    max_tokens: int = 6000
    timeout: float = 240.0

    @classmethod
    def from_env(cls) -> LLMConfig:
        return cls(
            base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
            api_key=os.environ.get("LLM_API_KEY", ""),
            temperature=float(os.environ.get("LLM_TEMPERATURE", "0.2")),
            max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "6000")),
            timeout=float(os.environ.get("LLM_TIMEOUT", "240")),
        )


@dataclass
class CaseExplanation:
    docket: str
    title: str
    term: int
    status: str
    docket_url: str
    summary: str
    markdown: str
    model: str
    generated_at: str
    source_hashes: list[str] = field(default_factory=list)
    proceeding_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CaseExplanation:
        return cls(
            docket=raw["docket"],
            title=raw["title"],
            term=int(raw["term"]),
            status=raw.get("status", "pending"),
            docket_url=raw.get("docket_url", ""),
            summary=raw.get("summary", ""),
            markdown=raw.get("markdown", ""),
            model=raw.get("model", ""),
            generated_at=raw.get("generated_at", ""),
            source_hashes=list(raw.get("source_hashes", [])),
            proceeding_count=int(raw.get("proceeding_count", 0)),
        )


def extract_summary(markdown: str) -> str:
    """Pull the first paragraph under '## In Plain English' (or first paragraph overall)."""
    m = re.search(
        r"##\s+In Plain English\s*\n+(.*?)(?:\n\s*##|\Z)",
        markdown,
        flags=re.IGNORECASE | re.DOTALL,
    )
    block = m.group(1).strip() if m else markdown.strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", block) if p.strip()]
    first = paragraphs[0] if paragraphs else ""
    return re.sub(r"\s+", " ", first).strip()


def call_llm(
    prompt: str,
    config: LLMConfig,
    *,
    client: httpx.Client | None = None,
) -> str:
    """Call an OpenAI-compatible /chat/completions endpoint and return the assistant text."""
    url = f"{config.base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    payload = {
        "model": config.model,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }

    owns_client = client is None
    http = client or httpx.Client(timeout=config.timeout)
    try:
        resp = http.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return str(data["choices"][0]["message"]["content"]).strip()
    finally:
        if owns_client:
            http.close()


def needs_update(case: CaseDocket, existing: CaseExplanation | None, *, force: bool = False) -> bool:
    if force or existing is None:
        return True
    if existing.source_hashes != case.pdf_hashes():
        return True
    return existing.proceeding_count != len(case.proceedings)


def explain_case(
    case: CaseDocket,
    config: LLMConfig,
    *,
    root: Path = Path("."),
    force: bool = False,
    client: httpx.Client | None = None,
) -> CaseExplanation | None:
    """Generate (or reuse if unchanged) a plain-English explanation for a CaseDocket."""
    out_dir = root / "data" / "explanations"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{case.docket}.json"

    existing: CaseExplanation | None = None
    if out_path.exists():
        existing = CaseExplanation.from_dict(json.loads(out_path.read_text(encoding="utf-8")))

    if not needs_update(case, existing, force=force):
        return existing

    briefing = build_case_briefing(case, root=root)
    markdown = call_llm(briefing, config, client=client)

    explanation = CaseExplanation(
        docket=case.docket,
        title=case.title,
        term=case.term,
        status=case.status,
        docket_url=case.docket_url,
        summary=extract_summary(markdown),
        markdown=markdown,
        model=config.model,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        source_hashes=case.pdf_hashes(),
        proceeding_count=len(case.proceedings),
    )
    out_path.write_text(
        json.dumps(explanation.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return explanation


def load_all_explanations(root: Path = Path(".")) -> list[CaseExplanation]:
    exp_dir = root / "data" / "explanations"
    if not exp_dir.exists():
        return []
    items = [
        CaseExplanation.from_dict(json.loads(p.read_text(encoding="utf-8")))
        for p in sorted(exp_dir.glob("*.json"))
    ]
    items.sort(key=lambda e: (e.term, e.generated_at, e.docket), reverse=True)
    return items
