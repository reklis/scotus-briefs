## ADDED Requirements

### Requirement: Static case pages
The system SHALL generate a static public page for every publishable normalized case.

#### Scenario: Complete decided case is rendered
- **WHEN** a case has accepted metadata and an accepted decided-case guide
- **THEN** its page SHALL show its title, docket numbers, term, status, relevant dates, overview, arguments, decision, significance, citations, and available documents

#### Scenario: Case guide is incomplete
- **WHEN** a normalized case lacks an accepted guide or lacks one or more guide sections
- **THEN** its page SHALL still show verified metadata and documents while visibly labeling pending or source-limited content

### Requirement: Case lifecycle display
The system SHALL represent whether a case is pending, scheduled, argued, awaiting decision, decided, or otherwise unresolved based on available metadata.

#### Scenario: Opinion has not been issued
- **WHEN** the case record does not contain a current disposition
- **THEN** the page SHALL clearly state the pending status and SHALL NOT present a decision summary

#### Scenario: Opinion becomes available
- **WHEN** an accepted current opinion and updated guide provide the disposition
- **THEN** the case page SHALL display the decision date and supported decision explanation

### Requirement: Term browsing
The system SHALL provide static browsing organized by Supreme Court term.

#### Scenario: Visitor selects a term
- **WHEN** a visitor opens a term browse page
- **THEN** the site SHALL list cases assigned to that term with title, docket number, and lifecycle status

#### Scenario: Multiple terms are available
- **WHEN** the catalog includes cases from more than one term
- **THEN** the site SHALL provide navigation among available terms and identify the current or latest represented term

### Requirement: Docket-aware organization
The system SHALL display and order cases by parsed Supreme Court docket numbers rather than raw lexical filename order.

#### Scenario: Standard docket numbers are sorted
- **WHEN** a term contains dockets such as `24-7`, `24-38`, and `24-304`
- **THEN** the browse list SHALL order them by their parsed docket components

#### Scenario: Application docket is displayed
- **WHEN** a case includes an application docket such as `24A884`
- **THEN** the site SHALL preserve its official form and place it according to an explicit docket sort rule

#### Scenario: Consolidated case is displayed
- **WHEN** a case has multiple docket numbers
- **THEN** its page and browse entry SHALL display all associated docket numbers without rendering duplicate case pages

### Requirement: Client-side search
The system SHALL provide search without requiring a server-side service.

#### Scenario: Visitor searches by docket number
- **WHEN** a visitor enters an indexed docket number
- **THEN** matching cases SHALL be returned with docket matches ranked prominently

#### Scenario: Visitor searches by title or topic
- **WHEN** a visitor enters words present in case titles, aliases, questions, or accepted guide content
- **THEN** the site SHALL return relevant case pages with enough context to identify the match

#### Scenario: Search has no matches
- **WHEN** no indexed case matches the query
- **THEN** the site SHALL present a clear empty result and a route back to term browsing

### Requirement: Source transparency
The system SHALL make primary-source support visible from each case page.

#### Scenario: Citation is selected
- **WHEN** a visitor follows a guide citation
- **THEN** the site SHALL identify the source document and cited page or page range

#### Scenario: Archived document is available
- **WHEN** a case record references an archived PDF
- **THEN** the page SHALL offer an official source link when known and an archived GitHub copy without embedding the PDF in the Pages artifact

### Requirement: Layered non-lawyer presentation
The system SHALL present guide content in layers that support both quick orientation and deeper reading.

#### Scenario: Visitor opens a case page
- **WHEN** the case has an accepted overview
- **THEN** the page SHALL present a concise explanation before detailed arguments, procedural material, and sources

#### Scenario: Guide uses defined legal terms
- **WHEN** a guide contains entries in its terms section
- **THEN** the page SHALL make those definitions available in context or in a clearly accessible terms area

### Requirement: Accessible static interface
The system SHALL provide a responsive, keyboard-operable, semantically structured site suitable for common desktop and mobile browsers.

#### Scenario: Visitor navigates without a pointer
- **WHEN** a visitor uses keyboard navigation
- **THEN** search, browse navigation, case sections, and source links SHALL remain reachable with visible focus

#### Scenario: JavaScript search is unavailable
- **WHEN** client-side JavaScript is disabled or fails
- **THEN** prerendered term and case pages SHALL remain navigable and readable even though interactive search is unavailable
