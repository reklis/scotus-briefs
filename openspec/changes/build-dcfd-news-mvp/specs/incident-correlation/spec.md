## ADDED Requirements

### Requirement: Related observations form durable incidents
The correlation system SHALL associate observations with durable incidents using configured evidence including normalized location, time proximity, assigned incident talkgroup, unit overlap, incident type, and explicit radio references.

#### Scenario: Multiple calls describe one response
- **WHEN** observations close in time share a normalized location and compatible incident context
- **THEN** the system links them to one incident with one durable identifier

#### Scenario: Similar calls refer to different locations
- **WHEN** observations have similar incident types but distinct normalized locations without linking evidence
- **THEN** the system keeps them in separate incidents

#### Scenario: Incident continues across an hour boundary
- **WHEN** new observations match an active incident created in a prior publication hour
- **THEN** they update the existing incident rather than creating an hourly duplicate

### Requirement: Weak evidence remains a candidate
The correlation system SHALL NOT promote an incident to publishable solely from a routine call, a single low-confidence extraction, or dramatic wording without category-specific corroboration.

#### Scenario: Single uncertain dispatch is received
- **WHEN** the only evidence is a low-confidence initial report and no corroborating call arrives
- **THEN** the system retains or closes the incident as a non-publishable candidate

#### Scenario: Strong on-scene evidence arrives
- **WHEN** a trusted on-scene observation confirms a candidate's location and incident type
- **THEN** the system recalculates its evidence state and may promote it according to configured thresholds

### Requirement: Incident state preserves evidence modality
The incident system SHALL distinguish reports, dispatches, unit responses, on-scene observations, escalations, cancellations, containment, resolution, and corrections.

#### Scenario: Units are dispatched but have not arrived
- **WHEN** an incident has dispatch evidence but no on-scene evidence
- **THEN** its facts and status do not state that responders confirmed the reported condition

#### Scenario: Incident is escalated
- **WHEN** supported observations request additional alarms or configured major resources
- **THEN** the incident records the escalation with its evidence and time

#### Scenario: Incident is cancelled or downgraded
- **WHEN** later supported observations cancel or downgrade the original report
- **THEN** the incident retains the original report and records the newer disposition as the current state

### Requirement: Contradictions and corrections are append-only
The incident system SHALL preserve conflicting and superseded observations and SHALL derive current state without deleting historical evidence.

#### Scenario: Later evidence contradicts a published fact
- **WHEN** a stronger later observation contradicts information in the current incident state
- **THEN** the system records the contradiction, updates current state, and marks the incident as requiring a corrected public revision if already published

#### Scenario: Candidate proves unfounded before publication
- **WHEN** an initial reported event is explicitly unfounded or cancelled before reaching publication eligibility
- **THEN** the incident closes without a public story while retaining private provenance

### Requirement: Incident operations are idempotent and replayable
The correlation system SHALL produce the same logical associations when the same versioned observations are replayed and SHALL not duplicate incidents due to retried jobs.

#### Scenario: Observation is processed twice
- **WHEN** the same observation revision is submitted more than once
- **THEN** it is linked at most once and does not duplicate incident state transitions

#### Scenario: Correlation rules are upgraded
- **WHEN** authorized reprocessing uses a new correlation-rule version
- **THEN** the system records the rule version and preserves enough provenance to compare or rebuild derived state

### Requirement: Sensitivity follows the incident
The incident system SHALL aggregate sensitivity classifications from linked observations and SHALL prevent lower-sensitivity later calls from erasing an existing suppression reason.

#### Scenario: Incident includes sensitive medical evidence
- **WHEN** any linked observation triggers a mandatory suppression category
- **THEN** the incident remains suppressed unless a deterministic policy explicitly permits a sanitized public category independent of that detail
