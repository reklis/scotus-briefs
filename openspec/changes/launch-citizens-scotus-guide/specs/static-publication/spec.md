## ADDED Requirements

### Requirement: Fully static site build
The system SHALL produce a prerendered SvelteKit site that can be hosted by GitHub Pages without an application server.

#### Scenario: Site build succeeds
- **WHEN** normalized case data and accepted guides pass validation
- **THEN** the build SHALL emit static home, browse, case, informational, and search assets

#### Scenario: Dynamic server dependency is introduced
- **WHEN** a route cannot be prerendered for the configured GitHub Pages deployment
- **THEN** the build SHALL fail rather than silently publish a broken route

### Requirement: PDF exclusion from Pages artifact
The system SHALL exclude repository-archived PDF files from the deployed static artifact.

#### Scenario: Pages artifact is assembled
- **WHEN** the static site has been built
- **THEN** the artifact SHALL contain links to archived PDFs but SHALL NOT contain the `documents` archive itself

#### Scenario: Artifact exceeds configured size limit
- **WHEN** generated output exceeds the project's configured Pages artifact threshold
- **THEN** validation SHALL fail before deployment and report the unexpected growth

### Requirement: Existing GitHub infrastructure reuse
The system SHALL deploy from `reklis/scotus-briefs` using the existing GitHub Pages configuration, `scotusbriefs.us` domain, and `spark` self-hosted runner.

#### Scenario: Valid build is deployed
- **WHEN** the publication workflow completes successfully
- **THEN** the resulting artifact SHALL be deployed to the repository's GitHub Pages site and remain available at `https://scotusbriefs.us/`

#### Scenario: Application is rebuilt from scratch
- **WHEN** the clean implementation replaces prior repository contents
- **THEN** runner registration, repository identity, Pages configuration, and custom-domain operation SHALL be preserved

### Requirement: Manual and nightly operation
The system SHALL support both manual dispatch and scheduled nightly incremental operation.

#### Scenario: Nightly run finds changes
- **WHEN** the scheduled workflow discovers valid new or revised source documents
- **THEN** it SHALL archive and commit them, regenerate affected case data, build the site, and deploy accepted results

#### Scenario: Nightly run finds no changes
- **WHEN** discovery produces no new document hashes or metadata changes
- **THEN** the workflow SHALL avoid empty content commits and MAY still verify the current build

#### Scenario: Operator requests a manual run
- **WHEN** an authorized operator dispatches the workflow
- **THEN** the workflow SHALL accept an operational mode and bounded workload appropriate to incremental processing or backfill

### Requirement: Resumable backfill
The system SHALL process the historical corpus in bounded batches with durable progress.

#### Scenario: Backfill batch completes
- **WHEN** a configured batch of unresolved cases has been processed
- **THEN** accepted records, guides, failure reports, and the next checkpoint SHALL be committed before the job exits

#### Scenario: Backfill run is interrupted
- **WHEN** a job stops after a prior checkpoint was committed
- **THEN** the next run SHALL resume from durable state without regenerating every completed case

### Requirement: Source preservation independent of generation
The system SHALL preserve successfully downloaded documents even when later extraction, Ollama generation, validation, build, or deployment stages fail.

#### Scenario: Ollama is unavailable after download
- **WHEN** valid documents have been archived but the generation service cannot be reached
- **THEN** the workflow SHALL commit the source archive update, report guide generation as pending, and retain the currently published guide

#### Scenario: New guide fails validation
- **WHEN** document archival succeeds but a guide candidate is rejected
- **THEN** the new documents and factual case metadata SHALL remain durable while the prior accepted guide remains eligible for publication

### Requirement: Non-destructive publication failure
The system SHALL leave the last successful public site available when a nightly run fails.

#### Scenario: Static build fails
- **WHEN** tests, schema checks, link checks, or the SvelteKit build fail
- **THEN** the workflow SHALL not deploy the failed artifact

#### Scenario: Deployment fails
- **WHEN** GitHub Pages rejects or cannot complete a deployment
- **THEN** the workflow SHALL report failure without deleting the prior Pages deployment

### Requirement: Workflow concurrency and runner isolation
The system SHALL prevent overlapping publication jobs and SHALL not execute untrusted pull-request code on the LAN-connected self-hosted runner.

#### Scenario: Second scheduled run starts during an active run
- **WHEN** a workflow in the same publication concurrency group is active
- **THEN** the workflow configuration SHALL prevent both runs from modifying archive and guide state concurrently

#### Scenario: Public pull request is opened
- **WHEN** untrusted pull-request code requires validation
- **THEN** no workflow triggered by that pull request SHALL execute repository code on the `spark` runner

### Requirement: Rebuild provenance
The repository SHALL retain the inputs and version identifiers required to audit or repeat a guide build.

#### Scenario: Guide is regenerated
- **WHEN** an operator requests regeneration for a case
- **THEN** the system SHALL be able to select the checked-in source PDFs and recorded model, prompt, schema, extractor, and generation settings associated with the requested run

#### Scenario: Exact prior prose is needed
- **WHEN** a prior published guide must be inspected or restored
- **THEN** its checked-in generated record SHALL be recoverable from Git history without rerunning the model
