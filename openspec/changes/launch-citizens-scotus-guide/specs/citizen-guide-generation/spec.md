## ADDED Requirements

### Requirement: Page-aware source extraction
The system SHALL extract text from archived PDFs while retaining document identity and source page boundaries.

#### Scenario: PDF contains an embedded text layer
- **WHEN** an archived PDF provides extractable text
- **THEN** the system SHALL produce normalized text segments carrying the PDF hash and page number

#### Scenario: Page has no usable text
- **WHEN** a required PDF page lacks usable embedded text
- **THEN** the system SHALL either apply the configured OCR fallback or mark that page as unavailable rather than inventing its contents

### Requirement: Document classification
The system SHALL classify source documents and distinguish party submissions, transcripts, Court opinions, concurrences, dissents, and orders when the source supports that distinction.

#### Scenario: Opinion contains multiple authored parts
- **WHEN** an opinion PDF contains a majority or plurality opinion plus separate concurrences or dissents
- **THEN** extracted evidence SHALL retain the opinion-part and author attribution needed to distinguish those views

#### Scenario: Classification is uncertain
- **WHEN** the system cannot confidently determine a document or section type
- **THEN** it SHALL mark the classification uncertain and SHALL NOT use it as a definitive holding or party position

### Requirement: Evidence-first generation
The system SHALL derive structured, cited evidence from source text before generating case-level explanatory prose.

#### Scenario: Material claim is extracted
- **WHEN** the model extracts a fact, argument, procedural event, holding, or stated consequence
- **THEN** the claim SHALL identify its source document hash and page or page range

#### Scenario: Source support is absent
- **WHEN** the supplied documents do not support a requested fact or section
- **THEN** the generated record SHALL state that the information is unavailable or source-limited instead of relying on unstated model knowledge

### Requirement: Structured citizen guide
The system SHALL generate a schema-validated guide with lifecycle-appropriate plain-language sections.

#### Scenario: Supporting materials are available
- **WHEN** source evidence supports the relevant content
- **THEN** the guide SHALL provide a short overview, background and question presented, each side's position, oral-argument explanation when applicable, decision when applicable, why it matters, terms to know, and sources

#### Scenario: Case is not yet decided
- **WHEN** no current merits disposition supports a final decision
- **THEN** the decision section SHALL be marked pending and the guide SHALL NOT state or predict a holding

#### Scenario: Only some guide sections are supported
- **WHEN** evidence is sufficient for some sections but not others
- **THEN** the system SHALL publish supported sections and mark unsupported sections pending or source-limited

### Requirement: Accurate attribution
The system SHALL preserve distinctions among factual background, allegations, party arguments, amicus positions, justice questions, holdings, concurrences, and dissents.

#### Scenario: Party advances a disputed claim
- **WHEN** a statement comes from a party's position rather than an adjudicated finding
- **THEN** the guide SHALL attribute the statement to that party

#### Scenario: Justice asks a question at oral argument
- **WHEN** evidence consists of a justice's question or hypothetical
- **THEN** the guide SHALL describe it as a question and SHALL NOT characterize it as the justice's vote or settled view

#### Scenario: Dissent disagrees with majority
- **WHEN** a cited proposition comes from a dissent
- **THEN** the guide SHALL identify it as dissenting reasoning rather than the Court's holding

### Requirement: Plain and accessible language
The system SHALL explain legal material in concise language suitable for a general civic audience without removing material qualifications.

#### Scenario: Legal term is necessary
- **WHEN** a precise legal term cannot be replaced without changing meaning
- **THEN** the guide SHALL define it in plain language near its use or in the terms section

#### Scenario: Simplification would overstate the law
- **WHEN** a source includes an exception, uncertainty, procedural limitation, or narrow scope material to the explanation
- **THEN** the guide SHALL preserve that qualification in understandable language

### Requirement: Bounded-context processing
The system SHALL process documents within the configured 32,768-token model context and SHALL support documents or case records larger than one model request.

#### Scenario: Source exceeds one request budget
- **WHEN** page-aware source text exceeds the configured prompt budget
- **THEN** the system SHALL process bounded chunks and synthesize from their cited evidence records

#### Scenario: Multiple documents exceed one request budget
- **WHEN** combined case materials do not fit in one synthesis request
- **THEN** the system SHALL synthesize from staged document-level evidence rather than truncating sources without notice

### Requirement: Automated guide verification
The system SHALL verify candidate guides before accepting them for publication.

#### Scenario: Candidate passes verification
- **WHEN** a candidate conforms to schema, all material citations resolve, attribution is consistent, and lifecycle assertions match case metadata
- **THEN** the system SHALL mark it accepted and eligible for publication

#### Scenario: Candidate fails verification
- **WHEN** a candidate has an unsupported citation, malformed structure, attribution conflict, or unsupported decision claim
- **THEN** the system SHALL reject the candidate, report the reason, and retain any prior accepted guide

### Requirement: Generation provenance and disclosure
The system SHALL retain generation provenance and disclose automated authorship to readers.

#### Scenario: Guide is accepted
- **WHEN** the system accepts a generated guide
- **THEN** it SHALL record source hashes, model name and digest, generation parameters, prompt and schema versions, extractor version, validation result, and generation time

#### Scenario: Guide is displayed publicly
- **WHEN** a visitor views generated explanatory content
- **THEN** the site SHALL identify it as an AI-generated independent plain-language summary, link primary sources, and state that it is not legal advice
