## Context

The original candidate, `mistral-medium-3.5:128b`, was rejected after invalid schema output and an unsafe native-context allocation that caused host OOM and reboot. That outcome remains recorded in `docs/validation/scotus-mistral-medium-3.5-qualification-2026-09-15.md`.

Spark now has a locally derived 32K GPT-OSS artifact, `ragchew-gpt-oss:120b-32k`, whose installed Ollama digest is `820a68f9c4f7253846f5d82d225bc45ca93faa8cfe2a1009bff6efb15d662863`. Its Modelfile pins `num_ctx 32768`; Ollama reports an Apache-2.0 license and approximately 65 GB model size. Bounded probes completed without host pressure and returned strict JSON, but broad prose validation and an experimental Granite reviewer both produced unacceptable false positives. A later Citizen’s Guide probe showed that GPT-OSS can write readable 96- and 103-word summaries, while also demonstrating why the planner must constrain each section: it moved lower-court history into the outcome field and changed the actor that issues a court order.

The current reader-guide architecture already lets deterministic code own case identity, section order, citations, claim IDs, and publication eligibility. This change narrows the model’s job further: GPT-OSS receives field-specific approved evidence and canonical action slots and translates them into short ordinary-language text. There is no reviewer model in the release path.

## Goals / Non-Goals

**Goals:**

- Qualify the exact local 32K GPT-OSS artifact for SCOTUS extraction and Citizen’s Guide writing without hosted fallback.
- Generate a high-level guide of at most 180 words using short, direct language and only essential case facts.
- Preserve exact actor, action, object, court level, attribution, polarity, timing, legal status, and request-versus-ruling distinctions.
- Bind each generated field to only the approved claims and canonical action slots applicable to that field.
- Improve process-local diagnostics and paraphrase coverage so valid ordinary-language synonyms are not rejected as contradictions.
- Preserve privacy, grounding, budget, static-release, explicit-review, and publication-disabled rollout controls.

**Non-Goals:**

- Producing a comprehensive legal brief, procedural chronology, transcript recap, citation essay, or prediction.
- Using Granite, MiniCheck, or another model as a factual or publication gate.
- Letting the model choose identity, headings, section order, sources, claim IDs, legal status, or publication eligibility.
- Relaxing a demonstrated contradiction, unsupported factual addition, missing required outcome, privacy failure, or release failure.
- Pulling models in protected automation, using mutable tags, increasing the five-hour workflow ceiling, or sending private evidence off Spark.

## Decisions

### Pin the local 32K GPT-OSS artifact

Configuration will use `ragchew-gpt-oss:120b-32k` with full digest `820a68f9c4f7253846f5d82d225bc45ca93faa8cfe2a1009bff6efb15d662863`, never `latest` or the uncapped native-context tag. Preflight and post-completion checks must verify both tag and digest. The request also sets the reviewed context and generation controls explicitly so a mutable runtime default cannot enlarge context or sampling behavior.

The derived tag is independently reviewable even though it references GPT-OSS model blobs already present on Spark. Its Modelfile, upstream provenance, license, tag, digest, and 32K parameter must be recorded together.

Alternative: use native `gpt-oss:120b`. Rejected because the approved operating envelope is the explicit 32K artifact.

Alternative: retain Mistral. Rejected because safe memory and schema compatibility were not demonstrated.

### Make the product a bounded Citizen’s Guide

For decided cases, deterministic planning exposes four conceptual fields:

1. `what_it_is_about`
2. `what_the_sides_say`
3. `what_the_court_did`
4. `why_it_matters`

Each field contains one or two short sentences. The complete guide may not exceed 180 words. Pending or sparsely evidenced cases use a planner-approved status statement or omit an inapplicable outcome field according to existing maturity rules; GPT-OSS may not infer a result.

The prompt forbids lawyer names, justice-by-justice questions, citations in prose, detailed procedural history, unexplained specialist vocabulary, rhetorical flourishes, and predictions. An unavoidable term must be explained immediately in ordinary language. Deterministic code owns the public title, headings, order, source links, and citations.

Alternative: ask for a general legal brief and shorten it afterward. Rejected because excess detail creates more opportunities for role drift and unsupported synthesis.

### Give every field a separate evidence and action boundary

The planner assigns approved claims and canonical action slots to each field before the model call. A field may use only those inputs:

- `what_it_is_about` receives the approved issue and essential background.
- `what_the_sides_say` receives only attributed party positions and requests.
- `what_the_court_did` receives only canonical lower-court or Supreme Court actions explicitly labeled by court, with Supreme Court outcome slots kept distinct from history.
- `why_it_matters` receives only approved impact claims and may not predict consequences absent from them.

The writer output cannot choose, reorder, or add claim IDs or action-slot IDs. Assembly restores deterministic metadata after parsing. Missing evidence produces an existing planner-controlled fallback or omission, never model improvisation.

Alternative: give every section the full case packet. Rejected because the probe moved correct facts into incorrect sections and conflated which institution acted.

### Use a versioned role-explicit prompt

The new prompt profile requests only strict schema output and gives field-specific rules. It requires the writer to:

- name the actor rather than use ambiguous phrases such as “the Court agreed,” “it ordered,” or “that decision”;
- use `argues`, `says`, `asks`, or `wants` for party positions and requests;
- use `ruled`, `granted`, `denied`, `affirmed`, `reversed`, or `sent back` only for the court identified by the supplied action slot;
- distinguish an agency seeking or receiving an order from a court issuing one;
- preserve negation, uncertainty, interim effect, and final effect;
- omit nonessential details instead of compressing distinct actions into one ambiguous sentence; and
- return one or two plain sentences for each applicable field within the total word limit.

The literal prompt profile, schema version, model tag, digest, context ceiling, and generation controls participate in processor and request fingerprints. Any semantic change requires a new profile version and fresh qualification.

Alternative: rely on free-form prompting plus a reviewer model. Rejected because Granite both rejected faithful paraphrases and approved changed action roles.

### Recalibrate action validation around canonical slots without weakening hard safety

Schema, privacy, unknown-reference, forbidden-field, severe-bound, required-status, release, and demonstrated contradiction checks remain hard failures. Action validation compares prose against the planner’s canonical slot for the same field. A hard action failure requires either a demonstrated conflicting actor/action/object/polarity/court/status or omission of a required action slot. A lexical synonym or an ambiguous regex match without a demonstrated conflict is recorded as a process-local diagnostic and cannot by itself claim that the prose is factually wrong.

Diagnostics include field path, detected actor/action/object/role, expected canonical slot, and contradiction or omission reason. They remain private and transient. A repository-authored paraphrase corpus includes both accepted equivalents and known role-changing counterexamples, including `receiving` versus `issuing` an order, a party request versus a holding, and lower-court action versus Supreme Court action.

Alternative: preserve regex rejection for every unrecognized phrase. Rejected because measured false positives make the validator an unreliable quality signal.

### Qualify before any production switch

Qualification proceeds from synthetic to protected measurement:

1. Verify the exact installed tag, digest, license record, Modelfile, 32K context, idle runtime, and safe memory headroom.
2. Run strict extraction and Citizen’s Guide probes over synthetic action-role and paraphrase fixtures.
3. Run fixture-backed publication-disabled end-to-end generation and verify private cleanup.
4. Run a fixed publication-disabled ten-case canary from one immutable parent and evidence snapshot.
5. Manually compare every hard-valid guide with approved evidence and the active public page.

The existing threshold remains at least eight of ten accepted and manually judged improved, zero accepted correctness errors, no degraded legacy page, passing privacy/release checks, and explicit candidate-bound approval. Synthetic or two-case probes cannot authorize promotion.

## Risks / Trade-offs

- **[The compact guide omits context a lawyer would prefer]** → Treat brevity as an explicit product requirement while retaining the issue, positions, actual outcome/status, and approved impact.
- **[GPT-OSS moves facts between fields]** → Use field-specific packets, deterministic field ownership, role-explicit prompts, and canonical-slot omission/contradiction checks.
- **[The writer changes who performs an action]** → Test known actor/action counterexamples and reject demonstrated slot conflicts.
- **[Validator recalibration admits unsupported prose]** → Keep grounding and demonstrated contradiction hard; add labeled positive and negative paraphrase fixtures before changing rejection behavior.
- **[A 65 GB model affects Spark headroom or latency]** → Keep one exact 32K model loaded at a time, measure cold/warm operation, enforce cleanup, and retain existing request and five-hour bounds.
- **[Prompt-only improvements overfit two dockets]** → Qualify against a diverse synthetic corpus and fixed ten-case canary, not the probe examples alone.
- **[Historical change names mention Mistral]** → State the retarget explicitly in every artifact and preserve the Mistral rejection record rather than rewriting history.

## Migration Plan

1. Record the GPT-OSS upstream provenance, Apache-2.0 license, derived Modelfile, exact tag/digest, and approved 32K operating envelope.
2. Add the versioned Citizen’s Guide prompt and field-specific request contracts without changing production publication settings.
3. Bind planner-approved claims and canonical action slots to each applicable guide field; keep identity and metadata outside model control.
4. Recalibrate action diagnostics and add positive paraphrase and negative role-change tests before using acceptance rates for model decisions.
5. Update exact model configuration, allowlists, fingerprints, preflight, drift checks, and no-pull workflow policy.
6. Run focused tests, full tests, lint, typing, strict OpenSpec validation, workflow/repository policy checks, and privacy scans.
7. Run fixture-backed and fixed ten-case publication-disabled qualification with transient prose and explicit cleanup.
8. Promote only the exact reviewed candidate after thresholds and explicit approval; otherwise leave the current immutable release unchanged.

Rollback before approval is configuration-only because publication remains disabled. After approval, rollback redeploys the prior immutable release and requires a validated processor migration; no deployed release is edited in place.

## Open Questions

- What smallest labeled paraphrase corpus is sufficient to demonstrate that action-validator false positives fell without increasing false negatives?
