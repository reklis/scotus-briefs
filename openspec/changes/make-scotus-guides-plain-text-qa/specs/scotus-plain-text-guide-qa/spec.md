## ADDED Requirements

### Requirement: Citizen’s Guides use direct question-and-answer generation
The system SHALL generate Citizen’s Guide prose by asking deterministic high-level questions directly against authorized official Court text and SHALL NOT require structured legal-observation extraction or model-generated JSON for this Guide path.

#### Scenario: Structured extraction would be incomplete
- **WHEN** official Court text is available but the legacy extractor would omit a required observation category
- **THEN** the Guide questions remain eligible for direct plain-text generation from the authorized text

#### Scenario: Non-Guide extraction remains installed
- **WHEN** another legal-analysis path still uses structured observations
- **THEN** that path does not supply, approve, or block the direct Citizen’s Guide answers

### Requirement: Questions and headings are deterministic
The system SHALL own the question identities, wording, applicability, public headings, and order outside model control.

#### Scenario: Decided or ordered case
- **WHEN** deterministic source state establishes a supported Supreme Court disposition
- **THEN** the ordered questions are exactly “What is this case about?”, “What does each side want?”, “What has happened in the case so far?”, “What did the Supreme Court decide?”, and “Why might this matter to ordinary people?”

#### Scenario: Pending case
- **WHEN** deterministic source state does not establish a Supreme Court disposition
- **THEN** the Court question is “What is the Supreme Court being asked to decide?” and the model cannot substitute an outcome question or heading

#### Scenario: Model returns prose
- **WHEN** an answer completion succeeds
- **THEN** deterministic code binds that text to the one question that produced the request and does not parse model-selected headings or delimiters

#### Scenario: One question lacks a packet or answer
- **WHEN** any of the five questions lacks an authorized source packet, exceeds a hard bound, or returns blank final content
- **THEN** the whole Guide remains failed or pending rather than omitting that question

### Requirement: Each answer uses a minimal plain-text protocol
The system MUST make one bounded completion correspond to one question, request plain text rather than a response schema, and retain only nonblank final message content.

#### Scenario: Ordinary answer
- **WHEN** the model returns nonblank final text
- **THEN** the system preserves the answer unchanged apart from surrounding transport whitespace and does not parse it as JSON

#### Scenario: JSON-looking or awkward answer
- **WHEN** nonblank final text looks like JSON, contains poor prose, or does not follow preferred sentence guidance
- **THEN** it remains an unchanged manual-review answer rather than triggering automated repair or semantic rejection

#### Scenario: Empty answer
- **WHEN** the completion has no final content or only whitespace
- **THEN** that question fails structurally without manufacturing an answer

#### Scenario: Reasoning is returned separately
- **WHEN** the provider returns reasoning metadata in addition to final content
- **THEN** only final content is read and reasoning is not logged, persisted, receipted, diagnosed from, or published

### Requirement: Questions use simple explanatory instructions
The system SHALL instruct the model to answer for a person with no legal training, use everyday words, explain unavoidable legal terms, rely only on supplied official material, avoid guessing, and state when the material does not answer the question.

#### Scenario: Source material lacks an answer
- **WHEN** the supplied official excerpts do not answer the deterministic question
- **THEN** the prompt directs the model to say so plainly rather than infer or predict

#### Scenario: Legal terminology is unavoidable
- **WHEN** an answer requires a legal term
- **THEN** the prompt asks for an ordinary-language explanation in the answer

### Requirement: Official source packets are deterministic and question-specific
The system MUST construct each question’s source packet locally from authorized Court documents using versioned deterministic document, block, source-label, ordering, and truncation rules.

#### Scenario: Case has an opinion or order
- **WHEN** an authorized opinion or order is available
- **THEN** the relevant question packets include bounded controlling-opinion or operative-order text and distinguish reporter-prepared syllabus text from the Court’s own text

#### Scenario: Pending argued case has no disposition
- **WHEN** the case has a docket and complete argument transcript but no disposition
- **THEN** packets use bounded docket, case-introduction, question-presented, and attributed advocate material without implying a Court result

#### Scenario: Separate opinion exists
- **WHEN** an opinion document contains concurrence or dissent material
- **THEN** controlling-answer packets exclude that material unless a separately defined deterministic question expressly requests it

#### Scenario: Source provenance is assembled
- **WHEN** an answer is bound to a question
- **THEN** its immutable private provenance contains the question ID and ordered document revision, kind, official URL, source role, page/line label, and block order for every supplied range, while public links deterministically expose only authorized role, URL, and page/line labels

#### Scenario: Public Guide is assembled
- **WHEN** all five answers succeed
- **THEN** the `about` answer becomes the labeled public dek, the remaining four answers become ordered sections, title and answer sources derive from their packets, and revision metadata is assembled without fabricated claims

### Requirement: Long official materials are handled within reviewed bounds
The system SHALL keep every request within the reviewed context, character, token, call, disk, and runtime limits and SHALL use a bounded plain-text map-and-synthesize protocol when the relevant packet cannot fit safely in one request.

#### Scenario: Packet fits the reviewed envelope
- **WHEN** the question-specific official text fits within the configured request bound
- **THEN** the system makes one answer call for that question

#### Scenario: Packet exceeds the reviewed envelope
- **WHEN** relevant official text exceeds the request bound
- **THEN** deterministic windows receive the same question and one bounded synthesis call produces the final answer from transient window answers

#### Scenario: Window budget is exhausted
- **WHEN** all relevant windows cannot be processed within case or run limits
- **THEN** the question fails or remains pending without silently treating an early truncated window as complete evidence

#### Scenario: Synthesis completes
- **WHEN** a final answer is assembled from window answers
- **THEN** intermediate answers, prompts, and source text are deleted and only the final review answer may enter the private candidate

### Requirement: Status and maturity do not depend on model observations
The system MUST derive Guide status, maturity, and eligibility from deterministic discovery, session, disposition, correction, prior-state, and complete-document metadata rather than structured model extraction.

#### Scenario: Application docket has a disposition without argument
- **WHEN** an `A` docket has an official disposition and no argument session
- **THEN** deterministic metadata classifies it as `order_issued` with `post_order` maturity

#### Scenario: Argued case has a disposition
- **WHEN** a case has a complete argument session and an official disposition
- **THEN** deterministic metadata classifies it as `decided` with `post_opinion` maturity

#### Scenario: Pending case has a complete argument
- **WHEN** a case has complete argument metadata and no official disposition
- **THEN** deterministic metadata classifies it as `argued` or `reargued` with `official_transcript` maturity

#### Scenario: Disposition is corrected
- **WHEN** official discovery supplies a reviewed disposition revision date
- **THEN** deterministic metadata classifies the new revision as `corrected` with `corrected` maturity and retains prior history

### Requirement: Plain-text answers receive manual rather than automated semantic review
The system SHALL classify an assembled Q&A Guide as generated for review and SHALL NOT infer grounding, completeness, readability, improvement, or factual approval from nonblank answer generation.

#### Scenario: All applicable questions produce answers
- **WHEN** a Guide is assembled successfully
- **THEN** sanitized accounting records `manual_review_required` and structural success only

#### Scenario: Answer may be wrong or incomplete
- **WHEN** a generated answer survives the nonblank transport boundary
- **THEN** a human reviewer compares it with the official materials before recording any candidate-bound decision

#### Scenario: No approved reviewer decision exists
- **WHEN** a generated Q&A candidate has not received explicit candidate-bound approval
- **THEN** publication and release promotion remain denied

### Requirement: Operational safety remains mandatory
The system MUST preserve exact local-model tag, digest, and context verification; source authorization; local-only evidence handling; final-content-only processing; request and run budgets; transient cleanup; privacy scans; deterministic public metadata; publication-disabled generation; release validation; compare-and-swap protection; and explicit approval.

#### Scenario: Model identity changes during Q&A generation
- **WHEN** the installed tag, full digest, or context capacity differs before or after a completion
- **THEN** the answer is rejected and no alternate or hosted model is used

#### Scenario: Candidate fails privacy or release validation
- **WHEN** a generated Q&A candidate fails an existing boundary check
- **THEN** it is not uploaded, promoted, or published

#### Scenario: Publication-disabled experiment completes
- **WHEN** plain-text answers are generated for inspection
- **THEN** the active public release remains unchanged and private source material, prompts, reasoning, and intermediate answers are removed
