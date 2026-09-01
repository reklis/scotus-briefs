## ADDED Requirements

### Requirement: Calls are transcribed privately with reproducible metadata
The analysis system SHALL create a private transcript revision for each processable call and SHALL record the STT model, configuration, input version, timing, and available confidence signals.

#### Scenario: Clear call is successfully transcribed
- **WHEN** an analysis worker receives a valid call with intelligible speech
- **THEN** it stores a private transcript revision and reproducibility metadata linked to the source capture

#### Scenario: Call is silent or unintelligible
- **WHEN** a call contains insufficient intelligible speech or fails quality thresholds
- **THEN** the worker records a non-transcribable status and does not invent transcript content

#### Scenario: A new STT model reprocesses a call
- **WHEN** an authorized reprocessing job uses a different model or configuration
- **THEN** the system creates a distinct transcript revision without overwriting prior provenance

### Requirement: Radio-domain context informs transcription
The analysis system SHALL support configured DCFD unit vocabulary, talkgroup context, and DC geographic vocabulary without treating those hints as evidence that a word was spoken.

#### Scenario: Talkgroup and location hints are available
- **WHEN** a call is transcribed with configured radio-domain hints
- **THEN** the transcript records which hint-set version was used and retains uncertainty where audio does not support a confident choice

### Requirement: Structured observations remain grounded in source evidence
The extraction stage SHALL produce schema-valid observations linked to a source capture and transcript revision, and each observation SHALL include type, value, confidence, epistemic status, and supporting evidence range.

#### Scenario: Dispatch reports a possible fire
- **WHEN** a transcript states that units are dispatched for a possible fire
- **THEN** extraction creates a dispatch or reported-event observation and does not classify the fire as on-scene confirmed

#### Scenario: On-scene unit confirms smoke showing
- **WHEN** a transcript attributes an observation of smoke to an arriving unit
- **THEN** extraction records an on-scene observation with its source and does not add an unsupported cause, casualty, or outcome

#### Scenario: Transcript contains a negated condition
- **WHEN** a transcript says there is no smoke or no fire
- **THEN** extraction preserves the negation and does not create a positive smoke or fire observation

### Requirement: Uncertainty is preserved
The analysis system SHALL represent low-confidence, ambiguous, corrected, and contradictory details explicitly rather than silently selecting a definitive value.

#### Scenario: Street quadrant is uncertain
- **WHEN** the audio supports a street name but not a reliable NE, NW, SE, or SW quadrant
- **THEN** the location observation marks the quadrant unknown or low confidence instead of guessing one

#### Scenario: Address is corrected in a later call
- **WHEN** a later transcript explicitly corrects an earlier address
- **THEN** the system emits a correction observation linked to both evidence items and preserves the superseded value in history

### Requirement: Analysis identifies privacy and routine-content signals
The extraction stage SHALL classify observations needed by deterministic publication policy, including routine acknowledgements, medical content, personal identifiers, exact residential unit details, behavioral-health events, suicide, overdose, juvenile involvement, and other configured sensitivities.

#### Scenario: Call is a routine acknowledgement
- **WHEN** a transcript contains only an acknowledgement, status check, or other non-incident communication
- **THEN** it is marked routine and does not independently create a publishable incident claim

#### Scenario: Call contains patient-identifying details
- **WHEN** a transcript includes a person's name, apartment number, medical condition, or other configured sensitive detail
- **THEN** the analysis marks the relevant text and observations sensitive for suppression or generalization

### Requirement: Analysis fails closed on invalid model output
The analysis system SHALL reject malformed, unsupported, or policy-incompatible structured output and SHALL route it for retry or diagnostic review rather than promoting it to incident evidence.

#### Scenario: Model returns an unsupported fact
- **WHEN** extracted output contains a claim without a traceable supporting source range
- **THEN** the claim is rejected and cannot affect incident or publication state

#### Scenario: Model output violates its schema
- **WHEN** the model returns missing, invalid, or unparseable required fields
- **THEN** the stage records a validation failure and does not emit partial observations as accepted evidence
