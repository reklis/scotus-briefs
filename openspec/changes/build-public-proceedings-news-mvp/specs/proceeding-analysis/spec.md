## ADDED Requirements

### Requirement: Long proceedings are transcribed privately and reproducibly
The analysis system SHALL transcribe normalized media chunks with proceeding-relative timing and SHALL record media revision, model, configuration, language, hints, diarization settings, confidence, and processing status.

#### Scenario: Chunk contains intelligible speech
- **WHEN** a valid private media chunk is analyzed
- **THEN** the system stores an immutable private transcript revision with word or segment timing

#### Scenario: Chunk is silent or unavailable
- **WHEN** a chunk contains no intelligible speech or represents a known gap
- **THEN** analysis records an explicit status and invents no transcript

### Requirement: Chunk boundaries are reconciled deterministically
The system SHALL reconcile overlapping adjacent transcripts, preserve discontinuities, and map accepted text to exact media time ranges.

#### Scenario: Sentence crosses a chunk boundary
- **WHEN** overlapping chunks produce duplicate boundary words
- **THEN** the reconciler emits one accepted sequence while retaining both source revision references

#### Scenario: Adjacent chunks have a capture gap
- **WHEN** media timestamps are discontinuous
- **THEN** the transcript preserves a gap marker and does not join words as continuous speech

### Requirement: Speaker identity requires affirmative evidence
The system SHALL use anonymous speaker labels unless identity is supported by authoritative participant metadata plus reliable turn mapping, authoritative captions/transcript metadata, or an explicit introduction in evidence.

#### Scenario: Public official is reliably identified
- **WHEN** an official roster and evidence support the speaker turn
- **THEN** analysis may attach that official identity and records its identity evidence

#### Scenario: Voice resembles a known official
- **WHEN** only vocal similarity or model inference suggests identity
- **THEN** the speaker remains anonymous

### Requirement: Spoken and documentary evidence remain distinct
Each observation SHALL identify whether support comes from spoken media, an official transcript, agenda, docket, bill, amendment, vote record, order, release, or other approved document and SHALL include an exact time or page/range reference.

#### Scenario: Agenda lists a planned vote
- **WHEN** an agenda schedules a vote but no vote record or spoken result exists
- **THEN** extraction records only a scheduled item and not that the vote occurred

#### Scenario: Official roll call is published
- **WHEN** an approved vote record provides the result and totals
- **THEN** extraction may record the official vote action with document evidence

### Requirement: Procedural and legal status is preserved
The extraction system SHALL distinguish questions, arguments, testimony, proposals, announcements, introductions, amendments, motions, adopted actions, chamber passage, orders, signatures, effective actions, implementation, denials, and withdrawals.

#### Scenario: Justice asks a hypothetical question
- **WHEN** oral argument contains a justice's question
- **THEN** extraction records a question and does not represent it as a holding or predicted vote

#### Scenario: House passes a bill
- **WHEN** an official vote record shows House passage
- **THEN** extraction records passage by one chamber and does not state that the bill became law

#### Scenario: Mayor announces a planned program
- **WHEN** a briefing announces future intent without implementation evidence
- **THEN** extraction preserves announcement and planned status

### Requirement: Unsupported model output fails closed
The system SHALL reject observations with unsupported identity, quotations, vote totals, dates, legal outcomes, policy effects, or stronger certainty and SHALL emit no partial accepted evidence from invalid output.

#### Scenario: Model converts debate into enacted policy
- **WHEN** extraction claims final action from evidence showing only discussion
- **THEN** validation rejects the claim and prevents correlation or publication
