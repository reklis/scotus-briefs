## Context

The project is being rebuilt from scratch in the existing public `reklis/scotus-briefs` repository. The repository already owns the `scotusbriefs.us` GitHub Pages site and has an online ARM64 self-hosted runner labeled `spark`. The runner can perform nightly work and reach the private Ollama service at `http://192.168.1.41:11434`, where `ragchew-gpt-oss:120b-32k` is available with a configured 32,768-token context window.

The available backfill contains about 2,162 PDFs (roughly 1 GiB) arranged into opaque case groups, primarily oral-argument transcripts with some opinions and orders. PDFs have content hashes but the archive does not contain a complete normalized case catalog. The new system must both recover/import this corpus and acquire new official documents incrementally.

The public audience is non-lawyers. Accuracy, attribution, lifecycle awareness, and traceability to primary sources are therefore more important than producing fluent but unsupported prose. At the same time, the initial product must remain simple enough to launch without an editorial queue, branch review, or dynamic backend.

## Goals / Non-Goals

**Goals:**

- Preserve every downloaded source PDF in ordinary Git so a repository checkout contains the primary materials required for regeneration.
- Maintain normalized, schema-validated case and document records independent of storage filenames.
- Generate approachable citizen guides with source-backed sections for the dispute, each side's position, the decision, and significance.
- Publish useful partial guides for cases that have not been decided or whose source set is incomplete.
- Provide static term/docket browsing and client-side search on `scotusbriefs.us`.
- Run resumable backfill and incremental nightly updates using the existing `spark` runner and local GPT-OSS model.
- Keep the Pages artifact small by excluding archived PDFs while linking to both official sources and repository copies.
- Record enough provenance to audit and selectively regenerate generated content.

**Non-Goals:**

- Providing legal advice or predicting how the Court will decide a pending case.
- Building a user account system, database-backed API, content-management interface, or server-side search service.
- Reproducing every filing from every cert petition in the first launch; source adapters can expand after the argued/decided-case pipeline works.
- Requiring human editorial approval before MVP publication.
- Guaranteeing byte-for-byte repeatability from a nondeterministic LLM; exact published outputs are retained in Git instead.
- Serving the entire PDF archive as part of the GitHub Pages deployment artifact.

## Decisions

### 1. Use one repository and ordinary Git for immutable PDFs

PDFs will be stored under a content-addressed archive such as `documents/<sha256-prefix>/<sha256>.pdf`. A manifest will map each blob to its official URL, official filename, retrieval time, document type, checksum, size, case associations, and supersession information. A changed document at the same upstream URL becomes a new blob and manifest revision rather than replacing history.

The current maximum PDF is under 5 MiB, so ordinary Git is simpler and more self-contained than Git LFS. Site workflows will avoid transferring PDF blobs when possible through sparse/filtered checkout, and the Pages artifact will never include `documents/`.

Alternatives considered:

- **Git LFS:** reduces normal clone size but introduces quota, bandwidth, and availability dependencies that weaken self-contained regeneration.
- **Object storage only:** scales better but fails the explicit requirement that downloaded PDFs be checked into the project repository.
- **Human-readable duplicate paths per case:** easier to browse in Git but duplicates files shared by consolidated cases; case metadata will provide the human view instead.

### 2. Separate source documents, normalized records, generated guides, and presentation

The repository will use four conceptual layers:

```text
documents/             immutable PDF blobs
manifests/             retrieval and provenance records
data/cases/            normalized case records
data/guides/           generated citizen guides
site/                   SvelteKit static application
```

A case record will include a stable internal ID, title, Supreme Court term, one or more docket numbers, lifecycle status, relevant dates, party labels, aliases, and document references. Docket numbers are modeled as an array to support consolidated and companion cases. Sorting uses parsed docket components rather than lexical filename order.

A guide record will include structured sections, citations, completeness/status indicators, source hashes, prompt/schema version, extractor version, model name and digest, generation parameters, generation time, and validation outcome.

Alternatives considered:

- **Markdown as the canonical data format:** convenient to author but harder to validate and update section-by-section. Structured JSON will be canonical; SvelteKit controls presentation.
- **Deriving metadata during every build:** creates slow, fragile builds and entangles presentation with legal-document parsing.

### 3. Implement ingestion and generation tooling in Python, with the site in SvelteKit/TypeScript

Python will handle source discovery, downloading, PDF validation, page-aware extraction, schema validation, Ollama requests, backfill checkpoints, and generation orchestration. Dependencies and tool versions will be locked. SvelteKit with `adapter-static` will render the public site, and TypeScript will consume validated generated data.

This split uses mature PDF and automation libraries while retaining the requested SvelteKit frontend. The boundary between them is versioned JSON Schema rather than process-specific imports.

Alternative considered:

- **All TypeScript:** reduces language count but offers less convenient PDF/OCR and batch-processing tooling for this workload.

### 4. Treat ingestion as an idempotent append-and-reconcile process

Each discovery run will:

1. Fetch source indexes or docket metadata.
2. Normalize candidate case and document identities.
3. Skip an exact URL/content hash already recorded.
4. Download candidates to temporary storage.
5. Validate HTTP status, PDF signature, nonempty size, and SHA-256.
6. Move valid PDFs to their content-addressed paths.
7. Update manifests and affected case records atomically.
8. Mark changed cases for generation.

A failed or partial source fetch must not remove existing documents or cases. Corrections are linked through `supersedes`; obsolete documents remain reproducible but are excluded from current-guide synthesis unless explicitly selected.

The existing corpus importer will use its JSONL manifest and case grouping, then recover docket, title, term, and dates from the best available upstream metadata or document text. Ambiguous groups will be recorded as unresolved rather than assigned invented metadata.

### 5. Generate guides through evidence extraction, synthesis, and verification

GPT-OSS will not receive an undifferentiated pile of case documents. The generation pipeline will be staged:

```text
PDF pages
  -> document classification and page-aware chunks
  -> structured evidence/claims with page citations
  -> per-document plain-language notes
  -> case-level guide synthesis
  -> adversarial citation/attribution verification
  -> schema and deterministic validation
```

Prompts will instruct the model to treat document content as untrusted source material, use only supplied evidence, preserve the distinction between allegations, party arguments, justice questions, and Court holdings, and return schema-constrained JSON. Long documents will be chunked below the 32K context limit with overlap and page identifiers. The synthesis pass will consume extracted evidence rather than every raw page.

The public guide will use layered sections:

- A short plain-language overview
- Background and question before the Court
- What each side argues
- What happened at oral argument, when available
- What the Court decided, only when supported by a current opinion
- Why it matters, constrained to supported direct effects, stated stakes, and unresolved questions
- Terms to know
- Sources and generation disclosure

Unavailable sections remain visibly pending or source-limited. Oral-argument questions are never represented as votes or holdings. Every material generated claim must reference a known document and page or be rejected by validation.

Alternative considered:

- **One-pass case summary:** faster, but more likely to conflate positions, exceed context, and produce untraceable claims.

### 6. Publish automatically but fail closed at the case level

The MVP has no human editorial gate. Automated publication is allowed only for guide records that pass JSON/schema checks, citation target checks, lifecycle consistency checks, and a model verification pass. A failed candidate is recorded in a report and the last valid guide remains public. Newly discovered cases may publish factual metadata and source links while their guide is pending.

All pages will identify the explanations as AI-generated plain-language summaries, link primary sources, show generation/update time, and state that the site is independent and not legal advice.

Alternatives considered:

- **Publish every model response:** fastest but unacceptable for unsupported legal claims.
- **Require manual review:** safer but conflicts with the goal of getting an automated MVP live quickly.

### 7. Use fully static SvelteKit pages and a generated browser search index

SvelteKit will prerender the home page, term indexes, case pages, and supporting informational pages. A post-build indexer will index public case content and metadata. Search will run entirely in the browser and prioritize title and docket matches over body text. Browse pages will organize cases by Supreme Court term and parsed docket number, with status labels and consolidated docket display.

The site build consumes only normalized cases and accepted guides. Each case page links to the official PDF and an immutable archived GitHub copy; the PDF itself is not copied into the static output.

Alternative considered:

- **Hosted search/API:** unnecessary for the expected catalog size and incompatible with a simple GitHub Pages deployment.

### 8. Use a direct, resumable GitHub Actions workflow on `spark`

The clean-slate repository will initially retain `dev` as its default branch to minimize Pages configuration changes. Branch protections are not required for the MVP. Scheduled and manually dispatched workflows will run on `[self-hosted, spark]`, with write permission to commit downloaded PDFs, updated manifests, normalized records, accepted guides, and checkpoint information directly to `dev`.

The workflow will use concurrency control, will not execute untrusted pull-request code on `spark`, and will commit source preservation before depending on successful LLM generation. It will then build and deploy the current working tree through GitHub Pages. Backfill mode will process bounded batches and persist a checkpoint so it can resume across workflow runs; nightly mode will process only changed cases.

### 9. Launch incrementally

The first deployment will use a small representative set, including the curated fixture cases, to validate design and generation quality. The remaining corpus will appear incrementally as normalized records and guides pass validation. The site will remain useful for incomplete cases by showing metadata, lifecycle status, available documents, and pending guide sections.

This avoids making completion of the entire historical backfill a launch dependency.

## Risks / Trade-offs

- **LLM produces fluent but incorrect legal explanations** → Require evidence-first generation, page citations, attribution checks, lifecycle checks, a verification pass, and retention of the last accepted guide.
- **Only transcripts and opinions are available for many backfill cases** → Mark sections as source-limited; never present transcript excerpts as a complete account of merits briefs; expand source adapters to briefs after launch.
- **Opaque historical grouping cannot be mapped confidently to a docket** → Preserve the PDFs and unresolved metadata without publishing an invented case identity.
- **Repository growth degrades clone and Actions performance** → Use content addressing, immutable additions, persistent runner checkout, filtered/sparse site checkout, and monitor repository size before expanding to every docket filing.
- **A Pages build accidentally includes the PDF archive** → Add explicit build exclusions and an artifact-size validation.
- **Ollama or the LAN runner is unavailable** → Archive already-downloaded sources independently, retain existing guides, fail without destructive changes, and allow manual resume.
- **Public repository workflows expose the self-hosted runner to untrusted code** → Do not run `spark` jobs on public pull-request events and pin workflow permissions to the minimum needed.
- **Automated nightly commits conflict or overlap** → Use workflow concurrency and pull/reconcile immediately before committing.
- **Model output changes after prompt or model upgrades** → Record model digest and prompt/schema versions, retain exact guide output in Git, and regenerate only when intentionally requested.
- **Plain language removes necessary nuance** → Use layered explanations, define unavoidable legal terms, preserve uncertainty and qualifications, and expose linked source evidence.

## Migration Plan

1. Preserve the GitHub repository, runner registration, Pages/custom-domain settings, and a reference to the prior repository state.
2. Remove branch protections needed only by the previous workflow and replace repository contents with the clean-slate scaffold on `dev`.
3. Establish schemas, validation, immutable document layout, and import tooling.
4. Import and commit the existing manifest and PDF corpus in manageable commits.
5. Normalize and generate the curated fixture cases; validate guide quality and deploy the initial site.
6. Enable resumable backfill batches until the historical corpus is processed.
7. Enable nightly discovery, generation, commit, and deployment.
8. Monitor generation failures, repository growth, workflow duration, and Pages artifact size.

Rollback consists of redeploying a previously successful Pages artifact or reverting application/data commits. Immutable PDF commits are retained even if their associated metadata or guide must be corrected.

## Open Questions

- Which official index or docket endpoint will be the first authoritative discovery source for new cases and briefs?
- Is source metadata corresponding to the opaque UUIDs available from the system that created the existing S3 export, or must all historical identity be recovered from PDFs?
- Which document classes beyond transcripts, opinions, and orders are required immediately after MVP launch: merits briefs, reply briefs, amicus briefs, or lower-court opinions?
- What measured readability target and generation-quality threshold will be adopted after evaluating the curated cases?
