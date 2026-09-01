## ADDED Requirements

### Requirement: Public updates are produced on an hourly watermark
The publishing system SHALL evaluate changed incidents once per hour after a configurable late-arrival grace period and SHALL publish an atomic public projection.

#### Scenario: Eligible incident is ready at the watermark
- **WHEN** an incident satisfies publication policy before the hourly cutoff and remains eligible after the grace period
- **THEN** the hourly cycle publishes or updates its story in the new public projection

#### Scenario: Processing is incomplete at the cutoff
- **WHEN** required analysis for an incident is incomplete when the publication cycle evaluates it
- **THEN** that incident is deferred rather than published from partial unvalidated evidence

#### Scenario: Publication cycle fails
- **WHEN** generation or projection deployment fails
- **THEN** the previous known-good public projection remains available and the failure is reported operationally

### Requirement: Publication uses a conservative category allowlist
The policy engine SHALL publish only configured significant categories with category-specific evidence thresholds and SHALL default to non-publication for unknown categories.

#### Scenario: Confirmed significant fire is detected
- **WHEN** a fire incident has supported on-scene confirmation or configured escalation evidence and contains no mandatory suppression reason
- **THEN** it is eligible for a public story

#### Scenario: Routine medical response is detected
- **WHEN** an incident consists of a routine medical response without a separately eligible public-safety event
- **THEN** it is suppressed from stories, digests, maps, and public counts

#### Scenario: Unknown event category is detected
- **WHEN** an incident category has no explicit publication rule
- **THEN** it remains private by default

#### Scenario: Unconfirmed report is cancelled within the hour
- **WHEN** a reported incident is cancelled or determined unfounded before its publication watermark
- **THEN** the system does not publish it as an event that occurred

### Requirement: Sensitive information never reaches the public projection
The publishing system SHALL suppress or generalize patient information, names, exact residential unit numbers, behavioral-health events, suicide, overdose, juvenile details, and other configured sensitive content using deterministic policy.

#### Scenario: Eligible fire includes an apartment number and patient name
- **WHEN** an otherwise publishable incident contains an exact unit and personal identifier
- **THEN** the public claims omit those values and use no more precise a location than policy permits

#### Scenario: Incident has a mandatory suppression classification
- **WHEN** an incident is classified under a mandatory non-public category
- **THEN** no LLM-generated text, metadata, timeline entry, map feature, or aggregate public count reveals the incident

### Requirement: Generated prose is grounded in approved claims
The LLM SHALL receive only sanitized approved claims, and every factual output element SHALL reference supporting claim identifiers and preserve the permitted certainty level.

#### Scenario: LLM adds an unsupported cause or casualty count
- **WHEN** generated output includes a fact not supported by an approved claim
- **THEN** validation rejects the output and the incident is not published with that revision

#### Scenario: Evidence is only a dispatch report
- **WHEN** an approved claim says responders were dispatched to a reported condition
- **THEN** generated prose uses reported or dispatch language and does not state the condition as on-scene confirmed

#### Scenario: Generated output passes validation
- **WHEN** title, summary, and timeline are schema-valid, supported by approved claims, correctly qualified, and policy-safe
- **THEN** the revision becomes eligible for atomic publication

### Requirement: Stories evolve without duplication
The public system SHALL retain one story identity for an incident, append timestamped revisions, and reflect active, resolved, corrected, or retracted status.

#### Scenario: Active incident receives an update next hour
- **WHEN** an already published incident gains supported new facts
- **THEN** the system updates the existing story and records a new revision rather than publishing a duplicate article

#### Scenario: Published information is contradicted
- **WHEN** stronger later evidence invalidates a public claim
- **THEN** the next safe publication cycle visibly corrects or retracts the claim and retains revision history

#### Scenario: Incident resolves
- **WHEN** supported evidence indicates an active incident has concluded
- **THEN** the existing story is updated to resolved without inventing an outcome not present in approved claims

### Requirement: Public output excludes private source material
The public site SHALL NOT expose source audio, audio object locations, raw or normalized transcripts, source-radio identifiers, private observations, or cluster credentials.

#### Scenario: Reader views a story
- **WHEN** a public reader requests an incident page or hourly digest
- **THEN** the response contains only sanitized public fields and generated reporting

#### Scenario: Public client guesses a private identifier
- **WHEN** a public client requests a private capture, transcript, or object path
- **THEN** the public service does not retrieve or reveal the private material

### Requirement: The site communicates limitations
The public site SHALL disclose that reports are automatically generated from public DCFD radio communications, may be delayed, incomplete, or corrected, and are not an emergency service.

#### Scenario: Reader opens a story or digest
- **WHEN** public incident content is displayed
- **THEN** the site provides accessible source, automation, delay, incompleteness, and non-emergency context

### Requirement: Hourly and daily views use sanitized incidents
The public site SHALL provide an hourly digest and a current-day view of eligible active and resolved incidents using only the sanitized public projection.

#### Scenario: Hour contains no eligible incidents
- **WHEN** an hourly cycle publishes no new or changed eligible incidents
- **THEN** the digest indicates that no qualifying incidents were published without implying that no emergency calls occurred

#### Scenario: Current-day view is requested
- **WHEN** a reader opens the current-day view
- **THEN** eligible incidents are shown by public status and update time without including suppressed incidents in totals
