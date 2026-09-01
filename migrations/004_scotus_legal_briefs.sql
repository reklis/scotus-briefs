BEGIN;

ALTER TABLE jobs ADD COLUMN priority integer NOT NULL DEFAULT 50 CHECK (priority >= 0);
CREATE INDEX jobs_priority_claim_idx
  ON jobs(status, priority, available_at, lease_expires_at);

CREATE TYPE scotus_case_status AS ENUM (
  'docketed','argued','reargued','order_issued','decided','corrected','unresolved'
);
CREATE TYPE scotus_argument_status AS ENUM (
  'transcript_pending','transcript_ready','analyzed','published','corrected','retracted'
);
CREATE TYPE scotus_document_status AS ENUM (
  'discovered','downloading','ready','quarantined','parse_failed','parsed','content_deleted'
);
CREATE TYPE scotus_projection_status AS ENUM ('building','active','failed','superseded');

CREATE TABLE scotus_cases (
  case_id uuid PRIMARY KEY,
  schema_version text NOT NULL,
  term text NOT NULL CHECK (term ~ '^\d{4}$'),
  caption_private text NOT NULL,
  public_caption text,
  primary_docket text NOT NULL,
  official_url text NOT NULL CHECK (official_url LIKE 'https://www.supremecourt.gov/%'),
  status scotus_case_status NOT NULL DEFAULT 'unresolved',
  first_observed_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  UNIQUE (term, primary_docket)
);

CREATE TABLE scotus_case_revisions (
  revision_id uuid PRIMARY KEY,
  case_id uuid NOT NULL REFERENCES scotus_cases(case_id),
  revision_number integer NOT NULL CHECK (revision_number > 0),
  payload_private jsonb NOT NULL,
  payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  source_updated_at timestamptz,
  observed_at timestamptz NOT NULL,
  UNIQUE (case_id, revision_number),
  UNIQUE (case_id, payload_sha256)
);

CREATE TABLE scotus_dockets (
  docket_id uuid PRIMARY KEY,
  term text NOT NULL CHECK (term ~ '^\d{4}$'),
  docket_number text NOT NULL,
  normalized_docket text NOT NULL,
  official_url text NOT NULL CHECK (official_url LIKE 'https://www.supremecourt.gov/%'),
  UNIQUE (term, normalized_docket)
);

CREATE TABLE scotus_case_dockets (
  case_id uuid NOT NULL REFERENCES scotus_cases(case_id),
  docket_id uuid NOT NULL REFERENCES scotus_dockets(docket_id),
  primary_docket boolean NOT NULL DEFAULT false,
  linked_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (case_id, docket_id)
);
CREATE UNIQUE INDEX one_primary_scotus_docket
  ON scotus_case_dockets(case_id) WHERE primary_docket;

CREATE TABLE scotus_argument_sessions (
  argument_id uuid PRIMARY KEY,
  case_id uuid NOT NULL REFERENCES scotus_cases(case_id),
  term text NOT NULL CHECK (term ~ '^\d{4}$'),
  session_key text NOT NULL,
  argument_date timestamptz NOT NULL,
  sequence integer NOT NULL DEFAULT 1 CHECK (sequence > 0),
  reargument boolean NOT NULL DEFAULT false,
  status scotus_argument_status NOT NULL DEFAULT 'transcript_pending',
  official_detail_url text NOT NULL CHECK (
    official_detail_url LIKE 'https://www.supremecourt.gov/%'
  ),
  transcript_document_revision_id uuid,
  discovered_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  UNIQUE (case_id, session_key)
);
CREATE INDEX scotus_arguments_term_date ON scotus_argument_sessions(term, argument_date DESC);

CREATE TABLE scotus_argument_revisions (
  revision_id uuid PRIMARY KEY,
  argument_id uuid NOT NULL REFERENCES scotus_argument_sessions(argument_id),
  revision_number integer NOT NULL CHECK (revision_number > 0),
  payload_private jsonb NOT NULL,
  payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  observed_at timestamptz NOT NULL,
  UNIQUE (argument_id, revision_number),
  UNIQUE (argument_id, payload_sha256)
);

CREATE TABLE scotus_document_revisions (
  document_revision_id uuid PRIMARY KEY,
  case_id uuid NOT NULL REFERENCES scotus_cases(case_id),
  argument_id uuid REFERENCES scotus_argument_sessions(argument_id),
  document_kind text NOT NULL,
  external_id text NOT NULL,
  revision_number integer NOT NULL CHECK (revision_number > 0),
  official_url_private text NOT NULL CHECK (
    official_url_private LIKE 'https://www.supremecourt.gov/%'
  ),
  status scotus_document_status NOT NULL DEFAULT 'discovered',
  content_type text NOT NULL,
  byte_count bigint CHECK (byte_count > 0),
  sha256 text CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  object_key text UNIQUE,
  canonical boolean NOT NULL DEFAULT false,
  source_published_at timestamptz,
  observed_at timestamptz NOT NULL,
  ready_at timestamptz,
  delete_after timestamptz,
  content_deleted_at timestamptz,
  diagnostic_private text,
  UNIQUE (case_id, document_kind, external_id, revision_number),
  UNIQUE (case_id, document_kind, external_id, sha256),
  CHECK (document_kind <> 'transcript' OR argument_id IS NOT NULL)
);
CREATE UNIQUE INDEX one_canonical_scotus_document
  ON scotus_document_revisions(case_id, document_kind, external_id) WHERE canonical;

ALTER TABLE scotus_argument_sessions
  ADD CONSTRAINT scotus_argument_transcript_fk
  FOREIGN KEY (transcript_document_revision_id)
  REFERENCES scotus_document_revisions(document_revision_id);

CREATE TABLE scotus_document_parses (
  parse_revision_id uuid PRIMARY KEY,
  document_revision_id uuid NOT NULL REFERENCES scotus_document_revisions(document_revision_id),
  parser text NOT NULL,
  parser_version text NOT NULL,
  config_hash text NOT NULL CHECK (config_hash ~ '^[0-9a-f]{64}$'),
  status text NOT NULL CHECK (status IN ('complete','failed','ambiguous')),
  page_count integer CHECK (page_count > 0),
  diagnostic_private text,
  text_delete_after timestamptz,
  text_deleted_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (document_revision_id, parser, parser_version, config_hash)
);

CREATE TABLE scotus_transcript_lines (
  line_id uuid PRIMARY KEY,
  parse_revision_id uuid NOT NULL REFERENCES scotus_document_parses(parse_revision_id),
  document_revision_id uuid NOT NULL REFERENCES scotus_document_revisions(document_revision_id),
  file_page integer NOT NULL CHECK (file_page > 0),
  printed_page integer CHECK (printed_page > 0),
  line_number integer NOT NULL CHECK (line_number > 0),
  raw_text_private text,
  normalized_text_private text,
  artifact boolean NOT NULL DEFAULT false,
  UNIQUE (parse_revision_id, file_page, line_number)
);

CREATE TABLE scotus_transcript_turns (
  turn_id uuid PRIMARY KEY,
  parse_revision_id uuid NOT NULL REFERENCES scotus_document_parses(parse_revision_id),
  document_revision_id uuid NOT NULL REFERENCES scotus_document_revisions(document_revision_id),
  sequence integer NOT NULL CHECK (sequence >= 0),
  start_file_page integer NOT NULL CHECK (start_file_page > 0),
  start_line integer NOT NULL CHECK (start_line > 0),
  end_file_page integer NOT NULL CHECK (end_file_page > 0),
  end_line integer NOT NULL CHECK (end_line > 0),
  speaker_label_private text,
  speaker_name text,
  speaker_kind text NOT NULL,
  advocate_role text,
  identity_basis text NOT NULL,
  text_private text,
  confidence double precision NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  UNIQUE (parse_revision_id, sequence),
  CHECK ((end_file_page, end_line) >= (start_file_page, start_line))
);

CREATE TABLE scotus_extraction_revisions (
  extraction_revision_id uuid PRIMARY KEY,
  case_id uuid NOT NULL REFERENCES scotus_cases(case_id),
  argument_id uuid REFERENCES scotus_argument_sessions(argument_id),
  model text NOT NULL,
  schema_version text NOT NULL,
  prompt_version text NOT NULL,
  vocabulary_version text NOT NULL,
  parser_versions jsonb NOT NULL,
  document_revision_ids jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (
    case_id, argument_id, model, schema_version, prompt_version,
    vocabulary_version, parser_versions, document_revision_ids
  )
);

CREATE TABLE scotus_legal_observations (
  observation_id uuid PRIMARY KEY,
  extraction_revision_id uuid NOT NULL REFERENCES scotus_extraction_revisions(extraction_revision_id),
  case_id uuid NOT NULL REFERENCES scotus_cases(case_id),
  argument_id uuid REFERENCES scotus_argument_sessions(argument_id),
  observation_type text NOT NULL,
  legal_status text NOT NULL,
  certainty text NOT NULL,
  raw_value_private text NOT NULL,
  normalized_value_private text,
  attribution_private text,
  speaker_name text,
  speaker_kind text NOT NULL,
  identity_basis text NOT NULL,
  authority_citations_private jsonb NOT NULL DEFAULT '[]'::jsonb,
  confidence double precision NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  evidence_private jsonb NOT NULL,
  sensitivity jsonb NOT NULL DEFAULT '[]'::jsonb,
  supersedes_observation_id uuid REFERENCES scotus_legal_observations(observation_id),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX scotus_observations_case_idx ON scotus_legal_observations(case_id, created_at);

CREATE TABLE scotus_case_observations (
  case_id uuid NOT NULL REFERENCES scotus_cases(case_id),
  observation_id uuid NOT NULL REFERENCES scotus_legal_observations(observation_id),
  linked_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (case_id, observation_id)
);

CREATE TABLE scotus_issues (
  issue_id uuid PRIMARY KEY,
  case_id uuid NOT NULL REFERENCES scotus_cases(case_id),
  issue_key text NOT NULL,
  title_private text NOT NULL,
  authority_citations_private jsonb NOT NULL DEFAULT '[]'::jsonb,
  first_observed_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  correlation_version text NOT NULL,
  UNIQUE (case_id, issue_key)
);

CREATE TABLE scotus_issue_observations (
  issue_id uuid NOT NULL REFERENCES scotus_issues(issue_id),
  observation_id uuid NOT NULL REFERENCES scotus_legal_observations(observation_id),
  linked_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (issue_id, observation_id)
);

CREATE TABLE scotus_case_history (
  history_id bigserial PRIMARY KEY,
  case_id uuid NOT NULL REFERENCES scotus_cases(case_id),
  prior_status scotus_case_status,
  new_status scotus_case_status NOT NULL,
  reason text NOT NULL,
  evidence_ids jsonb NOT NULL,
  correlation_version text NOT NULL,
  changed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE scotus_approved_claims (
  claim_id uuid PRIMARY KEY,
  case_id uuid NOT NULL REFERENCES scotus_cases(case_id),
  argument_id uuid REFERENCES scotus_argument_sessions(argument_id),
  observation_type text NOT NULL,
  legal_status text NOT NULL,
  certainty text NOT NULL,
  public_value text NOT NULL,
  attribution text,
  official_url text NOT NULL,
  public_source_label text NOT NULL,
  page_label text NOT NULL,
  source_observation_ids jsonb NOT NULL,
  policy_version text NOT NULL,
  approved_at timestamptz NOT NULL
);

CREATE TABLE scotus_brief_revisions (
  revision_id uuid PRIMARY KEY,
  brief_id uuid NOT NULL,
  case_id uuid NOT NULL REFERENCES scotus_cases(case_id),
  argument_id uuid NOT NULL REFERENCES scotus_argument_sessions(argument_id),
  revision_number integer NOT NULL CHECK (revision_number > 0),
  maturity text NOT NULL,
  public_payload jsonb NOT NULL,
  claim_ids jsonb NOT NULL,
  correction_note text,
  generator_model text NOT NULL,
  created_at timestamptz NOT NULL,
  UNIQUE (brief_id, revision_number)
);

CREATE TABLE scotus_public_projections (
  projection_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  watermark timestamptz NOT NULL UNIQUE,
  payload jsonb NOT NULL,
  status scotus_projection_status NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  activated_at timestamptz
);
CREATE UNIQUE INDEX one_active_scotus_projection
  ON scotus_public_projections(status) WHERE status = 'active';
CREATE VIEW active_scotus_public_projection AS
  SELECT payload FROM scotus_public_projections WHERE status = 'active';

GRANT SELECT ON official_sources TO ragchew_collector;
GRANT SELECT, INSERT, UPDATE ON scotus_cases, scotus_dockets, scotus_case_dockets,
  scotus_argument_sessions, scotus_document_revisions, jobs TO ragchew_collector;
GRANT SELECT, INSERT ON scotus_case_revisions, scotus_argument_revisions TO ragchew_collector;

GRANT SELECT ON scotus_cases, scotus_case_revisions, scotus_dockets, scotus_case_dockets,
  scotus_argument_sessions, scotus_argument_revisions, scotus_document_revisions
  TO ragchew_worker;
GRANT SELECT, INSERT, UPDATE ON scotus_document_parses, scotus_transcript_lines,
  scotus_transcript_turns, scotus_extraction_revisions, scotus_legal_observations,
  scotus_case_observations, scotus_issues, scotus_issue_observations,
  scotus_case_history, scotus_cases, scotus_argument_sessions, jobs
  TO ragchew_worker;
GRANT USAGE, SELECT ON SEQUENCE scotus_case_history_history_id_seq TO ragchew_worker;

GRANT SELECT ON scotus_cases, scotus_dockets, scotus_case_dockets, scotus_argument_sessions,
  scotus_document_revisions, scotus_legal_observations, scotus_issues,
  scotus_issue_observations, scotus_case_history
  TO ragchew_publisher;
GRANT SELECT, INSERT, UPDATE ON scotus_approved_claims, scotus_brief_revisions,
  scotus_public_projections TO ragchew_publisher;

GRANT SELECT, UPDATE ON scotus_document_revisions, scotus_document_parses,
  scotus_transcript_lines, scotus_transcript_turns TO ragchew_retention;

REVOKE ALL ON scotus_cases, scotus_case_revisions, scotus_dockets, scotus_case_dockets,
  scotus_argument_sessions, scotus_argument_revisions, scotus_document_revisions,
  scotus_document_parses, scotus_transcript_lines, scotus_transcript_turns,
  scotus_extraction_revisions, scotus_legal_observations, scotus_case_observations,
  scotus_issues, scotus_issue_observations, scotus_case_history,
  scotus_approved_claims, scotus_brief_revisions,
  scotus_public_projections FROM ragchew_public;
GRANT SELECT ON active_scotus_public_projection TO ragchew_public;

COMMIT;
