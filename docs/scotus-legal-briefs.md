# SCOTUS Legal Briefs architecture

## Transcript-first source path

The collector polls only reviewed `www.supremecourt.gov` term and argument-detail paths. Current discovery uses linked argument pages; bounded historical discovery uses official term transcript indexes from October Term 2000 forward. Earlier terms are out of scope. A case becomes analysis-ready when the Court links a complete official transcript PDF. MP3 links may remain as provenance but create no download, object, normalization, or STT job.

1. Discover a term/docket/caption and deterministic argument session.
2. Queue one approved transcript-document job when the full PDF appears.
3. Retrieve without redirects, validate host/path/MIME/PDF structure, compute SHA-256, and store privately.
4. Parse file/printed pages, line coordinates, reading order, and official speaker labels into immutable private revisions.
5. Extract attributed legal observations with exact page/line evidence.
6. Correlate transcript, docket, orders, and opinions into durable case state.
7. Send only sanitized approved claims to the official OpenAI API and generate a structured plain-language brief.
8. Atomically expose only the validated public projection.

## Legal evidence boundaries

- A transcript can establish what a labeled speaker said, asked, or requested; it cannot establish how a justice voted or what the Court held.
- A docket can establish filed/public procedural metadata but not spoken wording.
- A lower-court action remains a lower-court action.
- Only an official Supreme Court order/opinion can establish a final Court order, holding, judgment, or disposition.
- Advocate facts and law remain attributed when disputed or not independently established.

## Brief structure and audience

The site is for regular readers, not lawyers. The public unit is one durable case page, not one page per argument. Generation waits until every discovered argument or reargument session has a complete accepted transcript parse, then synthesizes observations from the entire case.

Each page uses everyday sections such as “What this case is about,” “How the case got here,” “What each side wants,” “What each side says,” “What the justices asked,” “Why it matters,” and “What happens next.” A chronological argument timeline gives every session its own grounded explanation and identifies supported changes in a later reargument. Archives for any session date resolve to the same case page. Case indexes sort by the newest official Court argument-document date, never by brief-generation, correction, or projection timestamps.

Briefs use direct language, short sentences and paragraphs, and explain any unavoidable legal concept in context. Each editorial brief has a specific title, seven non-repeating citizen-facing sections, and a compact argument summary. Unsupported sections are omitted rather than invented. Deterministic checks reject schema instructions, internal identifiers in prose, generic titles, duplicate headings, repeated articles, unexplained legalese, overlong prose, missing sessions, out-of-order sessions, and claims attributed to the wrong argument. Every characterization still resolves to approved claim IDs and official page/line provenance.

The active SCOTUS publication path uses the official OpenAI API and the model pinned in `config/scotus.yaml`. It does not route production legal jobs to a local or merely compatible endpoint. Only bounded evidence windows are used for extraction and only sanitized approved claims are used for public brief generation.

A separate private-bootstrap path may be used for an explicitly authorized initial dataset. It accepts only a literal private-network IP, refuses to run when publication is enabled, records the actual generator model, validates output with the same grounding and safety rules, and never activates a public projection. Historical transcripts may first use the private deterministic observation extractor to build conservative speaker-attributed claims without model inference; production extraction remains unchanged. Opinion PDFs must name the exact correlated docket in their extracted text before they can update case status or support a disposition summary. Private-preview disposition prose is stored as append-only, page-grounded holding claims from the verified official opinion.

```bash
RAGCHEW_DATABASE_DSN=postgresql://... \
  devbox run -- .venv/bin/python scripts/generate-scotus-private-bootstrap.py \
  --base-url http://192.168.1.41:8081/v1 \
  --model glm-5.3-flash-iq1_s --workers 1
```

Large local models should run sequentially unless the server has proven capacity for concurrent requests. Generation attempts are durable, so accepted or denied unchanged inputs are not silently repeated.

## Privacy and public boundaries

Collectors can write only Court-scoped private document objects. Parser/analyzer workloads can read copied PDFs and private extracted text. Publishers can read structured evidence and write sanitized claims/briefs. The public database role reads only the active SCOTUS public projection and receives no object-storage or model credential.

Case captions, justices, and advocates may be named from official evidence. Unnecessary names/details involving minors, victims, medical information, home addresses, or sealed/redacted material are minimized or suppressed.

## Configuration and development

`config/scotus.yaml` selects terms, backfill cap/priority, source delay, document/parser bounds, retention, the OpenAI model, plain-language limits, site settings, and launch gates. It keeps source collection, brief generation, and publication independently disabled and rejects any configuration enabling audio or STT. Brief generation also defaults to one paid API call per run and stops on the first validation denial. A durable generation-attempt record prevents unchanged case evidence from purchasing the same model/prompt attempt again. Replaying a denied input requires an explicit prompt-version change or new case evidence. Supply `RAGCHEW_OPENAI_API_KEY` through the deployment secret store; never commit it.

```bash
uv sync --dev
.venv/bin/python -m pytest
.venv/bin/python -m mypy src/ragchew
```

Apply migrations in numeric order through `migrations/006_scotus_generation_cost_controls.sql`. Use only recorded fixtures unless a reviewed source entry is explicitly enabled.
