## Context

The current Guide path has two model-mediated abstractions. First, official docket, opinion, order, and transcript text is converted into strict JSON legal observations. Second, a planner converts approved observations into field-specific JSON packets and asks the writer for another strict JSON object. The first stage blocked the Qwen `26A124` experiment because no `case_background` observation survived, while the GPT-OSS Guide that did survive was shaped by terse machine-role packets rather than a natural explanatory task.

The Citizen’s Guide is for a person trying to understand the Court materials, not for downstream legal analytics. Public case identity, dates, status, headings, official links, revision history, and publication eligibility are already deterministic and must remain so. Source text, prompts, reasoning, and intermediate responses remain private and transient. Generated answers remain publication-disabled and require candidate-bound human approval.

## Goals / Non-Goals

**Goals:**

- Ask simple questions directly against bounded official Court text and receive plain-text answers.
- Remove structured legal-observation extraction and model-generated JSON as dependencies of Citizen’s Guide generation.
- Make each model call correspond to exactly one deterministic public question and heading.
- Preserve the first nonblank final answer unchanged for manual review.
- Support opinions, orders, argued cases, and pending cases within the reviewed 32,768-token envelope and existing run budgets.
- Keep exact-model, source, privacy, cleanup, release, dry-run, and approval controls.

**Non-Goals:**

- Removing structured extraction from unrelated legal-analysis or legacy compatibility paths.
- Letting the model choose questions, headings, case status, source links, identity, dates, or publication state.
- Automatically deciding that an answer is complete, grounded, readable, or correct.
- Sending an entire unbounded docket, transcript, or opinion to the model.
- Retaining chunk answers, reasoning, prompts, source text, or transport bodies as public or durable private artifacts.

## Decisions

### Ask one deterministic question per completion

The Guide uses these question identities and public labels:

1. `about`: “What is this case about?”
2. `sides`: “What does each side want?”
3. `history`: “What has happened in the case so far?”
4. `court`: “What did the Supreme Court decide?” for a supported disposition, otherwise “What is the Supreme Court being asked to decide?”
5. `importance`: “Why might this matter to ordinary people?”

Every candidate must contain all five answers in this exact order. Each packet contains at least one authorized source range; when that material cannot answer a question, the model is instructed to say so. A missing packet, blank answer, or exhausted bound fails the whole case rather than silently omitting a question. One call per question keeps headings and ordering deterministic, avoids parsing multiple answers out of prose, and makes failures case-local and diagnosable without exposing content.

Alternative: ask for all answers in one free-form completion. Rejected because delimiters or headings would become another model-controlled parsing protocol.

### Use a minimal plain-text prompt

The system instruction is intentionally short:

> You explain Supreme Court cases to people with no legal training. Answer only the question using the official Court material below. Use one to three short sentences and everyday words. Explain unavoidable legal terms. If the material does not answer the question, say so; do not guess. Return plain text only—no heading, bullets, citations, or JSON.

The user message contains the deterministic question followed by labeled official source excerpts. There is no `response_format`, observation vocabulary, claim-role schema, action schema, or repair feedback. Temperature remains zero, reasoning remains bounded low, and only final message content crosses the transport boundary. A small per-answer output-token ceiling bounds runaway output; it is a resource control, not a prose acceptance rule.

Alternative: retain structured extraction and only make the final response plain text. Rejected because extraction already prevents otherwise reviewable cases from reaching the writer and forces the explanatory prompt to consume machine-oriented claims instead of legal documents.

### Build question-specific source packets deterministically

The source-packet builder operates on authorized, locally parsed Court documents and never calls a model. It preserves official URL, document kind, page/line identity, and exact text internally while exposing only the text needed for the completion.

Packet priorities are question-specific:

- `about`: docket identity plus opinion/order syllabus or opening case description; for pending argued cases, the transcript’s case introduction and question-presented material.
- `sides`: attributed requests and arguments from the docket, opinion/order description of the parties’ positions, and bounded advocate opening material when available.
- `history`: docket procedural entries and the opinion/order’s procedural history.
- `court`: operative opinion/order passages and current docket status; pending cases receive the question presented instead of disposition language.
- `importance`: controlling opinion reasoning or, for pending cases, bounded source passages describing the legal issue’s practical reach without inviting outcome prediction.

Document order and block order are stable by document kind, official URL, page, line, and block ID. Docket blocks are labeled `docket`; transcript blocks with advocate identity are labeled `advocate`, with other transcript blocks labeled `transcript`; opinion pages containing the reporter’s syllabus marker are labeled `reporter_syllabus`; opinion pages attributed to a concurrence or dissent are labeled `separate_opinion`; remaining opinion pages are `controlling_opinion`; order pages are `order`. Syllabus text is labeled as reporter-prepared rather than the Court’s holding. Separate opinions are excluded from every packet in this five-question profile.

### Use bounded map-and-synthesize only when a packet cannot fit

If relevant text exceeds the reviewed request envelope, deterministic windows are processed with the same plain-text question. Window answers are transient and each is explicitly allowed to say that its excerpt lacks the answer. One final plain-text synthesis call receives the question, source labels, and window answers and may use the union of facts stated in those answers while adding no new fact. Window count, call count, characters, tokens, and runtime remain bounded. If every relevant block cannot fit within the maximum window count, the question fails instead of truncating the corpus.

The direct single-packet path is preferred. The multi-window path exists to avoid silently dropping late operative or party-position material in long documents. Intermediate answers are never published, logged, receipted as content, or retained after final assembly.

Alternative: truncate every document at the first context window. Rejected because holdings, responses, or procedural events can occur later and truncation would systematically hide relevant source material.

### Treat responses as opaque final text

A response succeeds structurally when final content exists, is a string, and contains at least one non-whitespace character within the transport byte/token limits. Code strips only transport-level surrounding whitespace; it does not parse, rewrite, repair, score, or classify the answer. JSON-looking, awkward, incomplete, or inaccurate text remains a manual-review issue rather than triggering an automated rewrite.

Each immutable answer carries its question ID, text, and the ordered source ranges used in its packet. Each range carries document revision ID, document kind, official URL, source role, page/line label, block order, and private text; only the role, official URL, and page/line label cross into `PublicSourceLink`. The Q&A draft contains exactly five ordered answers, deterministic caption title, maturity, creation time, model name, and `manual_review_required`.

The first `about` answer becomes the public `dek`; the case template renders the deterministic “What is this case about?” heading above it. The remaining four answers become ordered `PublicBriefSection`s. Title and dek sources come from the `about` packet; each section receives only its packet’s deduplicated official links; oral-argument metadata receives deterministic transcript links. Revision number, correction note, maturity, and creation time populate the existing public revision summary. Internal legacy `LegalBriefDraft` claim requirements are bypassed through a dedicated direct public-case builder and no placeholder claims are fabricated.

### Derive status and maturity without observations

Guide eligibility, status, and maturity use discovery and official-document metadata, never model observations. A corrected disposition revision yields `corrected`/`corrected`; a disposition with an argument session yields `decided`/`post_opinion`; a disposition without a session on an application (`A`) docket yields `order_issued`/`post_order`; another disposition yields `decided`/`post_opinion`; a case with a reargument session yields `reargued`/`official_transcript`; and another complete argument case yields `argued`/`official_transcript`. Prior immutable history is retained and a changed status appends one deterministic event. Complete required documents and successful parsing remain prerequisites.

### Keep manual review and publication controls authoritative

Every assembled Q&A Guide records `manual_review_required`. `accepted_count` continues to mean structural processing success only. Live generation remains dry-run-only. Deployment and promotion require an approved report bound to the exact candidate after a person reviews every generated answer against its official sources. No answer is described as grounded or accepted merely because transport succeeded.

### Fingerprint the protocol transition

The processor fingerprint changes for the plain-text prompt version, deterministic question set, packet-builder version, window/synthesis protocol, output bounds, reasoning level, exact model identity, and context envelope. A candidate created by the JSON pipeline cannot be mistaken for one created by direct Q&A.

## Risks / Trade-offs

- **[A source packet omits decisive context]** → Use question-specific priorities, stable source-range metadata, long-document fixtures, and bounded map-and-synthesize rather than first-window truncation.
- **[Plain text is harder to machine-check]** → That is intentional; deterministic code owns structure while people review meaning. Retain only nonblank/type/resource checks.
- **[Five questions increase model calls]** → Use one bounded call per question on the common path, strict per-case/run budgets, and measure fixed-canary runtime before promotion.
- **[Chunk synthesis compounds model errors]** → Keep source labels, preserve all window answers transiently for the synthesis call, prohibit new facts, and require human comparison with official materials.
- **[“Explain simply” becomes patronizing or inaccurate]** → Ask for everyday words for an adult with no legal training, not childlike tone, and let reviewers reject oversimplification.
- **[Pending cases invite prediction]** → Use a distinct deterministic pending-case question and explicitly prohibit guessing.
- **[Legacy assembly expects claim IDs]** → Add a dedicated Q&A assembly contract rather than fabricating model-derived claims or weakening public source provenance.

## Migration Plan

1. Add deterministic question, packet, plain-text transport, and Q&A draft contracts behind a new processor/prompt version.
2. Add source-packet fixtures covering a disposition-only order, a full opinion, an argued pending case, a decided-after-argument case, long documents, and separate opinions.
3. Wire Q&A assembly into the publication-disabled Guide path and remove that path’s dependency on `LegalExtractionService`, `ReaderGuidePlanner`, strict Guide JSON schema, and targeted repair.
4. Run local exact-model samples and manually compare each answer with its source ranges and active page.
5. Run fixture-backed and fixed ten-case publication-disabled qualification with privacy, cleanup, budget, release, and candidate-bound approval checks.
6. Promote only an explicitly approved exact candidate. Rollback restores the prior processor and leaves the active immutable release unchanged.

## Open Questions

- Fixed-canary measurements will determine whether the long-document map-and-synthesize ceiling can remain within current call/runtime budgets or needs a lower deterministic window limit. This measurement cannot authorize larger global budgets without a separate reviewed change.
