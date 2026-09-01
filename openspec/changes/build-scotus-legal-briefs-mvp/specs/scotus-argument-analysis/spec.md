## ADDED Requirements

### Requirement: Official transcript turns are parsed reproducibly
The analysis system SHALL consume only accepted official transcript revisions and SHALL record document digest, parser/version, page and line coordinates, raw/normalized private text, speaker label, confidence, and processing status.

#### Scenario: Complete official transcript is parseable
- **WHEN** a valid Court transcript PDF completes deterministic parsing
- **THEN** the system stores immutable page/line-grounded turns and queues legal extraction

#### Scenario: Transcript page is unreadable
- **WHEN** reading order or text cannot be reliably recovered
- **THEN** analysis records an explicit failure/gap and invents no wording

### Requirement: Transcript revisions preserve corrections
The system SHALL store a changed Court transcript as a new immutable revision, designate canonical text explicitly, and retain superseded evidence and analysis provenance.

#### Scenario: Court posts revised transcript bytes
- **WHEN** the same official transcript identity produces a new accepted digest
- **THEN** the system parses a new revision and compares affected observations without deleting the prior revision

### Requirement: Speaker identity requires official evidence
The system SHALL keep speakers anonymous unless official transcript labels, official argument metadata with reliable turn mapping, or explicit introductions support a justice or advocate identity and role.

#### Scenario: Official transcript labels a justice
- **WHEN** the Court transcript names the speaker for a turn
- **THEN** observations may use that identity with the transcript page/line turn as identity evidence

#### Scenario: Model infers identity from writing style
- **WHEN** only language style or contextual guesswork suggests identity
- **THEN** the speaker remains anonymous

### Requirement: Legal observations are typed and attributable
The extractor SHALL distinguish procedural posture, question presented, advocate contention, justice question, answer, concession, disputed premise, authority citation, doctrinal theme, requested disposition, lower-court action, order, and holding, with exact document page/line references and attribution.

#### Scenario: Advocate asserts a contested fact
- **WHEN** counsel states a fact disputed in the argument
- **THEN** the observation is attributed as that advocate's contention and not stored as established fact

#### Scenario: Justice asks a hypothetical
- **WHEN** a justice explores a hypothetical legal rule
- **THEN** the observation remains a question and does not become a holding, vote, or outcome prediction

### Requirement: Evidence types constrain legal status
The validator SHALL prevent argument transcripts from proving a Supreme Court holding or disposition and SHALL require an official opinion/order for those final statuses.

#### Scenario: Counsel asks the Court to reverse
- **WHEN** transcript evidence supports only a requested disposition
- **THEN** analysis records the request and not that reversal occurred

#### Scenario: Later opinion resolves the case
- **WHEN** an accepted official opinion supports a holding and disposition
- **THEN** analysis may add those observations while retaining argument as argument

### Requirement: Citations and quotations require exact support
The system SHALL reject quotations, case/statute citations, docket numbers, dates, holdings, and disposition terms not present in or deterministically supported by approved evidence.

#### Scenario: Model invents a precedent citation
- **WHEN** extracted output names an authority absent from bounded evidence
- **THEN** the entire unsupported observation is rejected before correlation or publication

### Requirement: Analysis revisions are replayable
The system SHALL version extraction schema, prompt, model, legal vocabulary, transcript/document revisions, and validation rules and SHALL support authorized deterministic replay.

#### Scenario: Revised transcript changes a pivotal exchange
- **WHEN** the case is reprocessed with the new canonical transcript revision
- **THEN** new observations are compared to prior output and corrections retain append-only provenance
