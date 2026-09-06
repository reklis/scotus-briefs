## ADDED Requirements

### Requirement: Incremental official case record
The system SHALL discover official Supreme Court case activity incrementally, download only unseen or changed official documents, and make every available accepted docket, transcript, order, and opinion document part of case analysis.

#### Scenario: Unchanged document
- **WHEN** a previously accepted official document has unchanged validators and content identity
- **THEN** the system reuses its accepted state without downloading or parsing it again

#### Scenario: New case document
- **WHEN** the official case record contains an unseen or changed document
- **THEN** the system downloads, validates, transiently parses, and includes that document in the next case analysis

### Requirement: Coherent citizen guide
The system SHALL build one plain-English citizen guide from approved claims derived from the complete available case record rather than publishing independently sorted claim fragments.

#### Scenario: Disposition-only case
- **WHEN** a case has an official order or opinion but no oral-argument session
- **THEN** the system performs a bounded structured writing pass over its approved case claims and emits a coherent guide with no invented argument analysis

#### Scenario: Writing failure
- **WHEN** the writer cannot produce a complete valid citizen guide within configured budgets
- **THEN** the system retains the last-known-good public release and records only sanitized fixed failure diagnostics

### Requirement: Reader-oriented disposition structure
A disposition-only guide MUST explain what the case concerns, why it reached the Supreme Court, the legal issue, what the Supreme Court did, and why the Court did it under exact unique headings in that order.

#### Scenario: Complete emergency-order guide
- **WHEN** approved claims include case background, procedural history, legal doctrine, and an operative Supreme Court action
- **THEN** each required section answers its named reader question and cites the corresponding approved claims

#### Scenario: Grounded but irrelevant fragment
- **WHEN** a paragraph is source-grounded but its claim types or legal roles do not answer the section heading
- **THEN** validation rejects the guide with a fixed correction code

### Requirement: Complete operative relief
The guide SHALL paraphrase the Supreme Court's operative action and immediate procedural effect in plain English using typed Court-action claims, while keeping requested relief and lower-court actions assigned to their actual actors.

#### Scenario: Stay pending appeal
- **WHEN** the Court grants a stay of a lower-court injunction while an appeal continues
- **THEN** the guide identifies the Supreme Court as granting the stay, identifies the lower-court injunction as the object, and explains that the relief is interim rather than a final merits judgment

#### Scenario: Actor confusion
- **WHEN** generated prose presents requested relief or a lower-court action as the Supreme Court's completed action
- **THEN** role-aware validation rejects the guide

### Requirement: Majority and separate-opinion distinction
The guide MUST NOT use explicitly identified dissent, concurrence, or separate-opinion claims as support for the Court's action, controlling legal issue, or Court reasoning. It MAY summarize such claims only in a separately labeled section.

#### Scenario: Dissent observations are available
- **WHEN** approved claims explicitly describe a dissent or concurrence
- **THEN** those claims appear, if used, only under `What separate opinions said` and are attributed as a separate opinion

#### Scenario: Dissent-led main explanation
- **WHEN** a required main section relies on an explicitly marked separate-opinion claim
- **THEN** validation rejects the guide

### Requirement: Corrected publication at the stable URL
A regenerated guide SHALL publish as a new revision of the existing case at its canonical URL and preserve sanitized source provenance.

#### Scenario: Correcting docket 26A124
- **WHEN** the corrected `Trump v. California` guide passes generation, grounding, privacy, and release validation
- **THEN** it replaces the active prose at the existing canonical URL as revision 2 without retracting the page
