## ADDED Requirements

### Requirement: Segments and documents form durable proceedings
The correlation system SHALL associate media chunks, transcripts, documents, participants, and observations with one authority-scoped proceeding using official external identifiers and schedule revisions.

#### Scenario: Live and archive media describe one session
- **WHEN** both revisions share the same authority and proceeding identity
- **THEN** they remain revisions of one durable proceeding

#### Scenario: Two hearings have similar titles
- **WHEN** sessions have different official identifiers or scheduled instances
- **THEN** the system keeps them as separate proceedings

### Requirement: Related evidence forms evidence-backed topics
The system SHALL group segments into topics using official agenda/docket/document references, normalized subjects, participants, and temporal continuity and SHALL not merge on generic rhetorical similarity alone.

#### Scenario: Hearing moves between agenda items
- **WHEN** evidence identifies a new bill or agenda item
- **THEN** subsequent supported observations attach to the corresponding topic

#### Scenario: Similar phrases concern different bills
- **WHEN** discussion uses similar language but references distinct official identifiers
- **THEN** topics remain separate

### Requirement: Government events evolve across proceedings
The system SHALL correlate later votes, orders, briefings, hearings, corrections, and implementation evidence into durable government events when official identifiers or sufficient evidence link them.

#### Scenario: Bill advances after an earlier hearing
- **WHEN** a later official action references the same bill identifier
- **THEN** the existing government event gains an append-only action rather than a duplicate event

#### Scenario: Oral argument later receives an opinion
- **WHEN** an official opinion references the same docket
- **THEN** it updates the case event while preserving oral argument as argument rather than decision

### Requirement: Disagreement and corrections are append-only
The system SHALL preserve conflicting statements, revised documents, corrected vote records, postponements, and superseded statuses without deleting historical evidence.

#### Scenario: Official corrects a vote total
- **WHEN** a revised authoritative roll call changes the recorded count
- **THEN** the system appends a correction and marks the current value without erasing the prior record

#### Scenario: Announced action is later withdrawn
- **WHEN** later official evidence withdraws a proposal
- **THEN** the event retains the announcement and records withdrawal as current status

### Requirement: Correlation is deterministic and replayable
The system SHALL version correlation rules, process each observation revision idempotently, and support rebuilding proceedings, topics, and events for comparison.

#### Scenario: Observation job is retried
- **WHEN** the same observation revision is correlated twice
- **THEN** it links at most once and creates no duplicate transition

### Requirement: Incomplete evidence cannot become final action
The correlation system SHALL not derive a ruling, enacted law, adopted Council action, completed implementation, or other final status solely from questions, testimony, debate, schedules, or announcements.

#### Scenario: Debate ends without an official result
- **WHEN** the proceeding contains extensive discussion but no supported action
- **THEN** the event remains debated or unknown rather than final
