## ADDED Requirements

### Requirement: Publication is default-deny and source-aware
The policy engine SHALL publish only configured consequential proceeding categories with sufficient source-specific evidence and SHALL suppress routine procedure, ceremonial content, repetition, and unknown categories.

#### Scenario: Official ruling is released
- **WHEN** an approved court document supports a consequential ruling and no suppression rule applies
- **THEN** the event is eligible for a source-linked story

#### Scenario: Hearing contains routine opening procedure
- **WHEN** evidence consists only of introductions, recesses, or administrative instructions
- **THEN** it remains excluded from stories and public counts

### Requirement: Public names follow role and privacy policy
The system SHALL permit supported identities of public officials acting officially and SHALL omit private witnesses and members of the public by default, along with personal contact details, home addresses, medical details, minors, immigration status, and sensitive testimony.

#### Scenario: Councilmember makes an official statement
- **WHEN** official metadata and evidence identify the Councilmember
- **THEN** approved claims may name the official in that public role

#### Scenario: Resident gives sensitive testimony
- **WHEN** a private witness states a name and personal circumstances
- **THEN** public claims omit identity and sensitive details or suppress the topic

### Requirement: Every public fact is grounded in an approved claim
The generator SHALL receive only sanitized approved claims carrying source authority, official page URL, jurisdiction, proceeding time, evidence kind, allowed status, and source observation identifiers.

#### Scenario: Generated story adds a vote count
- **WHEN** no approved claim supports that count
- **THEN** validation rejects the revision and leaves it unpublished

#### Scenario: Generated statement is fully supported
- **WHEN** each factual element references an approved claim and preserves status
- **THEN** the revision may enter the next atomic public projection

### Requirement: Government status language cannot be overstated
The validator SHALL enforce source-appropriate distinctions among questions, arguments, testimony, proposals, announcements, motions, adopted actions, one-chamber passage, rulings, signatures, effectiveness, and implementation.

#### Scenario: House floor debate mentions a proposal
- **WHEN** evidence does not include an official passage result
- **THEN** public prose may describe debate but cannot say the House passed the proposal

#### Scenario: Supreme Court hears argument
- **WHEN** no opinion or order has been released
- **THEN** public prose states that argument occurred and does not predict or announce a ruling

### Requirement: Public stories provide official provenance
Every story and digest SHALL identify jurisdiction and source authority, link to the approved official proceeding or document page, state that reporting is automated and delayed, and disclose that summaries are not official records or legal advice.

#### Scenario: Reader opens a story
- **WHEN** generated proceeding coverage is displayed
- **THEN** the page includes official provenance, timing, automation, incompleteness, correction, and non-authoritative disclaimers

### Requirement: Hourly projections are atomic and correction-capable
The publishing system SHALL evaluate changed events hourly after a grace period, retain one story identity across updates, append revisions, and keep the previous known-good projection when a cycle fails.

#### Scenario: Archive corrects live-derived evidence
- **WHEN** validated reprocessing changes an already published fact
- **THEN** the next safe cycle appends a visible correction or retraction to the existing story

#### Scenario: Publication cycle fails
- **WHEN** generation, validation, or projection creation fails
- **THEN** the previous projection remains active and the failure is reported

### Requirement: Public output excludes private source material
The public service SHALL NOT expose copied media, private object locations, raw transcripts, private document extraction, hidden participant metadata, model prompts, credentials, or disabled-source data.

#### Scenario: Public client guesses a transcript identifier
- **WHEN** a reader requests a private transcript or media asset
- **THEN** the public service cannot retrieve or reveal it
