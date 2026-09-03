## ADDED Requirements

### Requirement: Static-only production artifact
The system SHALL generate a complete GitHub Pages artifact whose reader-facing pages, navigation, assets, search data, provenance, and metadata require no runtime application, database, object store, model, credential, or external API other than ordinary retrieval of static files.

#### Scenario: Site is served from files only
- **WHEN** the generated directory is served by a plain static HTTP server under its configured project path
- **THEN** every generated archive and case page SHALL render and navigate without a FastAPI process, database connection, object-store connection, or runtime secret

#### Scenario: Dynamic production route is unavailable
- **WHEN** production deployment is configured for GitHub Pages
- **THEN** no FastAPI, Uvicorn, Kubernetes public service, or live projection API SHALL be required or exposed as part of the production site

### Requirement: Complete deterministic export
The exporter MUST validate one sanitized public projection and deterministically generate the root page, SCOTUS homepage, paginated indexes, term archives, argument-date archives, stable case pages, correction/revision navigation, search page/index, public JSON, release metadata, `404.html`, `robots.txt`, `sitemap.xml`, assets, and `.nojekyll`.

#### Scenario: Repeated build has identical inputs
- **WHEN** the exporter runs twice with the same projection, configuration, source commit, and build epoch
- **THEN** both output trees and release manifests SHALL be byte-for-byte identical

#### Scenario: Projection is missing or invalid
- **WHEN** the exporter receives no projection or a projection that fails its versioned schema
- **THEN** it SHALL fail without producing a deployable artifact

#### Scenario: Projection contains no cases
- **WHEN** a valid empty projection is intentionally used for bootstrap
- **THEN** the exporter SHALL create an accessible empty-state site and SHALL NOT invent case pages or source data

### Requirement: GitHub Pages path safety
All internal URLs MUST be generated through one URL policy supporting both a GitHub project base path and a custom-domain root, while official external Court URLs MUST remain unchanged.

#### Scenario: Project Pages deployment
- **WHEN** the site is configured with project base path `/ragchew/`
- **THEN** navigation, styles, scripts, search data, pagination, canonical URLs, redirects, sitemap entries, and case links SHALL resolve beneath `/ragchew/` and SHALL contain no accidental root-absolute `/scotus`, `/static`, or `/api` link

#### Scenario: Custom-domain deployment
- **WHEN** the project base path is `/` and canonical origin `https://scotusbriefs.us` is configured
- **THEN** generated links SHALL resolve from the custom-domain root, canonical URLs SHALL use that origin, and the artifact SHALL contain a root `CNAME` file with exactly `scotusbriefs.us`

#### Scenario: Custom-domain marker is inconsistent
- **WHEN** the `CNAME` file is missing, malformed, or differs from the canonical-origin host
- **THEN** static release validation SHALL fail before deployment

#### Scenario: Stable case identity
- **WHEN** a published case caption changes but its normalized term and primary docket do not
- **THEN** the existing canonical case path SHALL remain valid and any replacement slug SHALL receive a static redirect page

### Requirement: Pre-generated browsing and accessible presentation
The system SHALL pre-generate case, term, argument-date, status/topic navigation, and bounded pagination needed to browse every active public case, and every analysis surface MUST retain the automated-analysis, non-authoritative, not-official-record, not-legal-advice, and no-vote/outcome-prediction disclosure.

#### Scenario: JavaScript is unavailable
- **WHEN** a reader disables JavaScript
- **THEN** the homepage, generated archives, pagination, case content, official source links, revision history, and disclosures SHALL remain usable

#### Scenario: Accessibility validation
- **WHEN** a static release is validated
- **THEN** generated pages SHALL have semantic landmarks and headings, keyboard-usable navigation, visible focus, sufficient contrast, descriptive source labels, and a unique page title

#### Scenario: Source provenance is rendered
- **WHEN** a public paragraph or argument analysis is displayed
- **THEN** its provenance SHALL link to an allowlisted official Supreme Court URL and show the evidence type and public page label without exposing an internal claim identifier

### Requirement: Minimal client-side search
The static site SHALL provide dependency-free client-side search over a generated index limited to public path, title, caption, docket, term, argument date, status, and topics, using safe text rendering and bounded result pagination.

#### Scenario: Reader searches public metadata
- **WHEN** a reader submits a normalized caption, docket, title, term, status, or topic query
- **THEN** the script SHALL return matching public cases in the same deterministic order used by the generated site

#### Scenario: Search input contains markup
- **WHEN** a query or indexed public value contains HTML-like characters
- **THEN** results SHALL be inserted as text rather than executable markup

#### Scenario: Search index boundary is checked
- **WHEN** the release validator inspects the search index
- **THEN** it SHALL reject full brief sections, transcript excerpts, claim/document/observation identifiers, prompts, model payloads, and fields outside the search schema

### Requirement: Public artifact privacy boundary
The Pages artifact and public projection MUST contain only allowlisted sanitized contracts and MUST exclude copied documents, source HTML bodies, extracted transcript text, private storage paths, prompts, model responses, credentials, internal UUIDs, and unpublished or rejected case material.

#### Scenario: Forbidden content reaches candidate output
- **WHEN** structural or textual scanning detects a forbidden field, secret pattern, PDF signature, transcript payload, prompt/model payload, internal UUID, or private route in the candidate tree
- **THEN** export validation SHALL fail and no candidate file SHALL be deployed

#### Scenario: Public JSON is consumed
- **WHEN** a reader downloads generated projection or case JSON
- **THEN** every value SHALL validate against the public schema and source links SHALL remain restricted to reviewed official Court hosts

### Requirement: Release integrity and metadata
Every deployable tree SHALL include a content-derived release identifier and a manifest covering source commit, schema/tool/config versions, projection digest, previous release identifier, generated file digests and sizes, and aggregate case/page counts.

#### Scenario: Generated file is altered
- **WHEN** any generated file no longer matches its release-manifest digest or size
- **THEN** release validation SHALL fail before deployment

#### Scenario: Discovery state changes without public content change
- **WHEN** only a poll checkpoint or validator timestamp changes
- **THEN** public page bytes and their content-derived release identifier SHALL remain unchanged
