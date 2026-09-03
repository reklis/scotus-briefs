## ADDED Requirements

### Requirement: Explicit open-source and content licensing
Before public launch, the repository MUST include an owner-approved OSI license for repository-authored software, identify the license for repository-authored documentation and generated briefs, and explicitly exclude official Court and third-party source materials from those grants.

#### Scenario: User inspects repository licensing
- **WHEN** a user opens the public repository
- **THEN** license and notice files SHALL identify what is licensed, the applicable license, the copyright holder/year policy, generated-content terms, and source-material exclusions without implying ownership of official Court documents

#### Scenario: License approval is absent
- **WHEN** the configured owner approval or required license files are missing
- **THEN** production publication SHALL remain disabled

### Requirement: Source and generated-content policy
The repository SHALL document that Court documents are retrieved transiently from reviewed official links, are not redistributed in the repository or site, and are distinct from the project's generated summaries and provenance metadata.

#### Scenario: Contributor adds source-derived material
- **WHEN** contribution guidance is consulted before adding a fixture, document, quotation, logo, or generated case artifact
- **THEN** it SHALL state the allowed synthetic/sanitized forms, attribution requirements, prohibited raw materials, and review path for uncertain rights

#### Scenario: Public brief cites evidence
- **WHEN** generated analysis is published
- **THEN** it SHALL link to official Court material and SHALL NOT bundle the underlying Court PDF, complete transcript, or source-page copy

### Requirement: Public-repository secret and private-data hygiene
The project MUST maintain deny-by-default ignore, build-context, scanning, and review controls for credentials, environment variants, keys/certificates, databases/dumps, Court documents, audio, extracted text, model payloads, private workspaces, reports, and generated temporary output.

#### Scenario: Developer creates a sensitive local file
- **WHEN** a common secret, private document, database, backup, certificate, model dump, or extracted-text filename is placed in the worktree
- **THEN** Git and container build context policy SHALL exclude it unless an explicit reviewed synthetic fixture exception applies

#### Scenario: Candidate commit contains a secret or prohibited payload
- **WHEN** continuous integration or publication scanning detects a credential or prohibited private/source payload
- **THEN** the check SHALL fail before merge, image publication, state promotion, or Pages deployment

### Requirement: Reproducible and least-privilege automation
Continuous integration and publication automation MUST use frozen dependency resolution, immutable action/image/tool references where supported, minimal GitHub token permissions, no persisted checkout credential for untrusted commands, and automated vulnerability, secret, and license checks. The persistent self-hosted publication runner MUST be dedicated to protected repository workflows and MUST never execute pull-request or arbitrary-ref code.

#### Scenario: CI installs dependencies
- **WHEN** a pull-request or default-branch workflow installs Python or audit dependencies
- **THEN** it SHALL use the committed lock data and pinned tool/action versions rather than mutable branches or unconstrained latest tags

#### Scenario: Pull-request tests run
- **WHEN** untrusted contribution code executes
- **THEN** the workflow token SHALL be read-only, checkout credentials SHALL not persist, and publication environments/secrets SHALL be unavailable

#### Scenario: Self-hosted runner is selected
- **WHEN** a workflow targets the local Spark runner
- **THEN** it SHALL be a protected default-branch publication build with read-only source permission, no persisted checkout credential, and no pull-request trigger

#### Scenario: Dependency policy fails
- **WHEN** a prohibited license, high-severity unmitigated vulnerability, mutable publication action, or lockfile mismatch is detected
- **THEN** CI SHALL fail and prevent release until reviewed or explicitly documented under policy

### Requirement: Public contribution and security governance
The repository SHALL include contribution guidance, a private vulnerability-reporting process, generated-data rules, synthetic-fixture labeling, ownership/review guidance, and documented required repository protections.

#### Scenario: Contributor proposes a change
- **WHEN** a contributor reads the repository guidance
- **THEN** it SHALL explain development/test commands, style and review expectations, fixture/data restrictions, licensing of contributions, and how generated-content changes are produced rather than hand-edited

#### Scenario: Researcher finds a vulnerability or exposed secret
- **WHEN** a researcher reads the security policy
- **THEN** it SHALL provide a non-public reporting channel, supported-version scope, expected response process, and instructions not to disclose sensitive evidence in a public issue

#### Scenario: Maintainer configures GitHub repository settings
- **WHEN** the release runbook is followed
- **THEN** it SHALL require protected default/generated-content branches, required status checks/reviews, protected publication and Pages environments, restricted manual dispatch, and least-privilege workflow settings

### Requirement: Public documentation matches the static architecture
The README, architecture, security, configuration, and operations documentation MUST identify GitHub Pages as the only production reader runtime and clearly mark legacy Kubernetes/PostgreSQL/S3 paths as removed, migration-only, local, or dormant.

#### Scenario: Operator follows deployment documentation
- **WHEN** an operator prepares production publication
- **THEN** the documented path SHALL use the static batch, validated generated-content state, and GitHub Pages workflow and SHALL NOT instruct them to expose FastAPI, PostgreSQL, MinIO, or Kubernetes to readers

#### Scenario: Developer runs a local preview
- **WHEN** a developer follows the README without production secrets
- **THEN** they SHALL be able to build fixture-backed static output and preview it with a plain static server
