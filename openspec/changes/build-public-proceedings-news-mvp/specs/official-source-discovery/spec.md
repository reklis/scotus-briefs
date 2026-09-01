## ADDED Requirements

### Requirement: Collection sources are explicitly authorized
The system SHALL maintain a source registry with authority, jurisdiction, official index URL, access method, access-basis review, host allowlist, polling limit, approval time, reviewer, and enabled state, and SHALL collect only from enabled sources with a recorded access basis.

#### Scenario: Approved source is enabled
- **WHEN** an operator records the approved access method and enables a source
- **THEN** the scheduler may create discovery and collection jobs within that source's configured limits

#### Scenario: Source has no approved access basis
- **WHEN** a source is discovered or configured without completed access review
- **THEN** the system keeps it disabled and retrieves no media

#### Scenario: Media redirects to an unapproved host
- **WHEN** an enabled source resolves media through a host outside its allowlist
- **THEN** collection fails closed, preserves diagnostics, and marks the source for review

### Requirement: Official proceedings are discovered idempotently
The system SHALL discover proceedings from approved official calendars, feeds, APIs, or pages and SHALL assign stable identities from authority and external identifiers.

#### Scenario: Scheduled proceeding is discovered twice
- **WHEN** repeated polls return the same authority and external proceeding identifier
- **THEN** the system updates one proceeding record without creating a duplicate

#### Scenario: Proceeding schedule changes
- **WHEN** an official source changes the time, title, location, or status
- **THEN** the system appends a metadata revision and retains the prior schedule

### Requirement: Proceeding lifecycle is source-aware
The discovery system SHALL represent scheduled, live, completed, postponed, cancelled, archive-pending, and unavailable states without inferring completion from silence.

#### Scenario: Scheduled stream has not started
- **WHEN** the scheduled time arrives but the official source reports no active media
- **THEN** the proceeding remains delayed or unavailable and is not represented as completed

#### Scenario: Official event is cancelled
- **WHEN** the authority marks a proceeding cancelled
- **THEN** the system records the cancellation and creates no live collection job

### Requirement: Source health is independently observable
The system SHALL report source polling success, schedule freshness, authorization failures, host changes, media availability, capture gaps, and archive wait age.

#### Scenario: No proceeding is scheduled
- **WHEN** successful polls find no event during an expected quiet period
- **THEN** health reports a healthy source with no scheduled proceeding

#### Scenario: Repeated polls fail
- **WHEN** an official endpoint repeatedly times out or changes incompatibly
- **THEN** health reports a degraded source and alerts without treating the period as quiet success

### Requirement: Generic platform scraping is prohibited
The system SHALL NOT use arbitrary URL downloaders, access-control bypasses, or undocumented platform extraction as an enabled source method.

#### Scenario: Official page embeds an unapproved platform video
- **WHEN** discovery finds a platform embed but no approved machine-access method
- **THEN** the system records the proceeding metadata and leaves media collection disabled or waits for an official archive
