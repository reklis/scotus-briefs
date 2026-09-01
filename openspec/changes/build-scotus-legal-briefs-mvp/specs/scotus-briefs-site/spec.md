## ADDED Requirements

### Requirement: Readers can browse and find case briefs
The public site SHALL provide term, argument-date, docket, case-status, and supported topic browsing/search with stable case and argument URLs.

#### Scenario: Reader selects a Court term
- **WHEN** briefs exist for the selected term
- **THEN** the site lists cases with caption, docket, latest official Court argument-document date, and brief maturity

#### Scenario: Briefs are generated or corrected after the Court event
- **WHEN** article revision timestamps are newer than the official Court argument-document dates
- **THEN** the site orders cases by the Court document dates and does not use article, correction, or projection timestamps as sort keys

#### Scenario: Search has no result
- **WHEN** no public brief matches a query
- **THEN** the site returns an empty public result without searching private transcripts

### Requirement: One case page exposes the full case, every argument, and provenance
Each durable case SHALL have one public page. That page SHALL display the validated citizen-facing case overview, maturity/current legal status, chronological case history, every accepted argument and reargument session, a separate plain-language breakdown for each session, supported cross-session changes, revision/correction history, and links to approved official Court detail, docket, transcript, order, and opinion pages when available.

#### Scenario: Multiple argument sessions exist
- **WHEN** a case has more than one accepted official transcript
- **THEN** search and archives resolve to one case URL that shows every session in chronological order rather than publishing duplicate case pages

#### Scenario: Official transcript is unavailable
- **WHEN** an argument has no accepted full official transcript
- **THEN** no analytical brief is published for that argument

### Requirement: Disclosures appear on every analysis surface
Every index, search result, and case page SHALL state that analysis is automated, delayed, incomplete, non-authoritative, not an official Court record, not legal advice, and not a prediction of a justice's vote or case outcome.

#### Scenario: Reader opens a case directly
- **WHEN** the case page is rendered without visiting the homepage
- **THEN** the required disclosures remain visible and accessible

### Requirement: Public output excludes private source material
The public service SHALL NOT expose copied PDFs, object keys, full extracted transcripts, raw document extraction, private parser data, prompts, credentials, rejected claims, or unpublished cases.

#### Scenario: Reader guesses a document or transcript identifier
- **WHEN** a public request targets private source material
- **THEN** the public role cannot retrieve it and reveals no storage detail

### Requirement: Public projections and revisions are atomic
The site SHALL activate only fully validated projections, retain the prior known-good projection when publication fails, and display visible correction/retraction notes from append-only revisions.

#### Scenario: Generation fails during a case update
- **WHEN** a new projection cannot be completely validated
- **THEN** the previous public brief remains active and the failure is reported privately

### Requirement: The site is accessible and source-first
The public interface SHALL use semantic headings, keyboard-accessible navigation, sufficient contrast, human-readable dates/status, and descriptive labels for every official source link.

#### Scenario: Screen-reader user reviews sources
- **WHEN** the user navigates a case's provenance section
- **THEN** each link identifies its evidence type and official Court destination rather than displaying an ambiguous URL
