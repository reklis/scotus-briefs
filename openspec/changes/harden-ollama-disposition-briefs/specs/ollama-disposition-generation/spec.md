## ADDED Requirements

### Requirement: Disposition-specific local-model contract
The system SHALL use Ollama only for bounded structured observation extraction whenever an official disposition has no oral-argument session, and SHALL compile the public brief deterministically from approved claims without a generative brief request or fabricated argument-analysis fields.

#### Scenario: Zero-session disposition generation
- **WHEN** a case has one or more validated official dispositions and no oral-argument session
- **THEN** Ollama extracts grounded observations, deterministic code emits the source-supported public brief with zero argument analyses, and no generative disposition-brief call occurs

### Requirement: Deterministic official identity and action
The system SHALL derive the official caption, docket, publication date, status, and typed Court-action support deterministically from validated Court sources rather than permitting model prose to redefine those facts.

#### Scenario: Model changes the Court result
- **WHEN** model prose states a Supreme Court action that is not supported by a cited `COURT_HELD` or `COURT_ORDERED` claim
- **THEN** validation rejects the draft with a fixed sanitized code and publishes no replacement

### Requirement: Role-aware action grounding
The system SHALL validate each action statement against claims for its identified actor role, distinguishing requested relief, lower-court action, and Supreme Court action.

#### Scenario: Multiple supported actors in one brief
- **WHEN** a grounded draft describes a party's request, a lower-court ruling, and a different Supreme Court disposition
- **THEN** each action is compared only with approved claims carrying the corresponding legal status

#### Scenario: Action is attached to the wrong actor
- **WHEN** a supported action verb is reassigned from one role to another
- **THEN** validation fails even if that verb appears elsewhere in the case's approved claims

### Requirement: Accurate absent-proceeding validation
The system SHALL reject claims that an argument, transcript, counsel statement, or justice question occurred in a disposition-only case, while not misclassifying an explicitly negated statement as an occurrence.

#### Scenario: Truthful negated argument statement
- **WHEN** disposition prose states that the Court acted without oral argument and makes no positive argument claim
- **THEN** the prose is not rejected as invented oral argument

#### Scenario: Positive argument invention
- **WHEN** disposition prose claims that oral argument occurred or attributes an unsupported exchange to counsel or a justice
- **THEN** validation rejects the draft with a fixed sanitized code

### Requirement: Actionable sanitized corrections
The system SHALL provide corrective generation only for retryable structured extraction failures using fixed validator codes, SHALL never reuse rejected output, and SHALL keep every attempt within existing call, token, runtime, and case budgets.

#### Scenario: Correctable disposition extraction
- **WHEN** structured disposition extraction fails with a retryable fixed model-output code and another configured cycle fits all budgets
- **THEN** a fresh bounded extraction cycle receives only reviewed instructions and no rejected model output

### Requirement: Bounded automatic nightly retry
The system SHALL permit scheduled nightly retry of an unchanged failed local-model case only under a stable retry scope, after a cooldown, and within finite per-scope and per-run generation limits. Accepted cases and non-model failures SHALL NOT receive automatic model retries.

#### Scenario: Eligible scheduled retry
- **WHEN** a model-output failure remains pending for the same evidence and processor scope, its cooldown elapsed, and all retry budgets fit
- **THEN** one auditable automatic generation cycle is authorized without broad replay authority

#### Scenario: Retry exhaustion
- **WHEN** a retry scope has consumed its maximum automatic generation cycles
- **THEN** the case remains visibly pending but no further automatic model call occurs until evidence or a reviewed processor version changes or an owner authorizes an exact scope

#### Scenario: Scope-changing update
- **WHEN** validated source evidence or a reviewed parser, prompt, schema, policy, or model version changes
- **THEN** the system computes a new retry scope and may authorize a fresh initial generation

### Requirement: Representative disposition validation
The system SHALL maintain deterministic regression coverage for representative emergency dispositions and SHALL require a small retained-candidate validation before broad backlog processing.

#### Scenario: Pre-drain validation
- **WHEN** the corrected pipeline is ready for live use
- **THEN** `26A274`, `26A203`, and `26A124` are attempted under reduced bounds, artifacts and sanitized diagnostics are reviewed, and only fully validated cases may be deployed
