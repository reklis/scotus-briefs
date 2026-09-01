## ADDED Requirements

### Requirement: Public briefs use the official OpenAI API
The SCOTUS legal extraction and brief-generation path SHALL use the official OpenAI API with an explicitly configured OpenAI model and SHALL NOT silently route these jobs to a local or merely OpenAI-compatible endpoint.

#### Scenario: Brief generation starts
- **WHEN** an eligible claim ledger is ready for generation
- **THEN** the publisher invokes the configured OpenAI model with structured output and records that model on the immutable brief revision

### Requirement: Public briefs explain the case to non-lawyers
The generator SHALL write for readers without legal training using direct everyday language, short sentences and paragraphs, descriptive headings, and contextual explanations for unavoidable legal concepts. Deterministic validation SHALL reject unexplained legalese and configured readability-limit violations.

#### Scenario: Draft repeats lawyer-facing terminology
- **WHEN** a draft uses archaic or unexplained terms such as “arguendo,” “inter alia,” “hereinafter,” or “the instant case”
- **THEN** validation rejects the revision instead of publishing inaccessible prose

#### Scenario: A legal concept is necessary
- **WHEN** the evidence requires a concept such as statutory authority or standing
- **THEN** the brief explains what the concept means for this case in ordinary language

### Requirement: Each brief follows a complete citizen-facing case schema
An eligible case brief SHALL contain what the case is about, how it reached the Court, the main question, what each side wants, each side's reasoning, what the justices tested, points of agreement/disagreement, why the dispute matters, uncertainties, and what happens next, omitting unsupported sections rather than filling them speculatively.

The brief SHALL synthesize the durable case rather than only the latest argument. It SHALL contain a chronological, separately grounded analysis for every accepted argument or reargument transcript and SHALL explain supported changes between sessions.

#### Scenario: Case has an argument and a reargument
- **WHEN** complete accepted transcripts exist for both sessions
- **THEN** one case brief includes both sessions in order, gives each session its own source-grounded breakdown, and explains what the later session revisited without treating either argument as a decision

#### Scenario: Evidence lacks a supported question presented
- **WHEN** neither approved Court metadata nor evidence supports that section
- **THEN** the brief marks it unavailable or omits it and does not invent a legal question

### Requirement: Generation uses only approved claims
The generator SHALL receive sanitized approved claims with official source URL, evidence kind/range, attribution, legal status, certainty, and claim ID, and every factual or legal characterization SHALL cite supporting claim IDs.

#### Scenario: Draft adds unsupported procedural history
- **WHEN** generated prose contains a lower-court step absent from approved claims
- **THEN** deterministic validation rejects the revision and it remains unpublished

### Requirement: Oral argument is never represented as a decision
The validator SHALL prohibit language that treats questions, tone, argument, or apparent interest as a holding, vote, judgment, or factual outcome prediction.

#### Scenario: Draft predicts a five-to-four result
- **WHEN** no official opinion supports that result
- **THEN** the revision is rejected rather than softened or partially published

#### Scenario: Draft accurately describes a question
- **WHEN** a supported claim records a justice asking about a doctrine
- **THEN** prose may state that the justice asked or explored it but not that the justice adopted it

### Requirement: Positions and disputed facts remain attributed
The brief SHALL attribute legal/factual contentions to advocates, parties, lower courts, or official documents and SHALL preserve disagreement and uncertainty.

#### Scenario: Petitioner and respondent dispute the record
- **WHEN** approved claims contain competing accounts
- **THEN** the brief presents both with attribution and does not resolve the dispute itself

### Requirement: Public legal actors and sensitive facts follow policy
The system SHALL permit supported names/roles of justices and advocates and public case captions while minimizing or suppressing unnecessary names and personal details involving minors, victims, medical information, sealed/redacted facts, or other sensitive matters.

#### Scenario: Argument discusses a minor by full name
- **WHEN** the name is unnecessary to explain the legal issue
- **THEN** public claims use a role/generalized reference or suppress the detail

### Requirement: Brief maturity and corrections are explicit
The system SHALL label revisions as official-transcript, post-order, post-opinion, corrected, or retracted and SHALL retain one case/argument story identity with append-only revisions.

#### Scenario: Official transcript changes a quoted exchange
- **WHEN** a revised official transcript invalidates previously published wording
- **THEN** the next revision visibly corrects or removes it and links the correction to changed evidence

### Requirement: Analysis is not personalized legal advice
The policy engine SHALL reject instructions or recommendations directed at a reader's individual legal situation and SHALL frame output as general analysis of the public case record.

#### Scenario: Draft tells readers how to litigate a similar claim
- **WHEN** generated text gives personalized procedural or strategic advice
- **THEN** validation rejects the brief revision
