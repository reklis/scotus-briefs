BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE capture_status AS ENUM ('created','uploading','ready','rejected','expired','audio_deleted');
CREATE TYPE job_status AS ENUM ('pending','leased','complete','retry','failed');
CREATE TYPE incident_state AS ENUM ('candidate','corroborating','publishable','active','resolved','corrected','retracted','suppressed');

CREATE TABLE receivers (
  receiver_id text PRIMARY KEY,
  object_prefix text NOT NULL UNIQUE,
  token_hash text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE captures (
  capture_id text NOT NULL,
  receiver_id text NOT NULL REFERENCES receivers(receiver_id),
  schema_version text NOT NULL,
  manifest jsonb NOT NULL,
  audio_sha256 text NOT NULL CHECK (audio_sha256 ~ '^[0-9a-f]{64}$'),
  audio_bytes bigint NOT NULL CHECK (audio_bytes > 0),
  content_type text NOT NULL CHECK (content_type LIKE 'audio/%'),
  object_key text NOT NULL UNIQUE,
  status capture_status NOT NULL DEFAULT 'created',
  diagnostic text,
  created_at timestamptz NOT NULL DEFAULT now(),
  committed_at timestamptz,
  audio_delete_after timestamptz,
  audio_deleted_at timestamptz,
  PRIMARY KEY (receiver_id, capture_id)
);

CREATE TABLE jobs (
  job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  stage text NOT NULL,
  input_kind text NOT NULL,
  input_id text NOT NULL,
  input_version text NOT NULL,
  status job_status NOT NULL DEFAULT 'pending',
  attempts integer NOT NULL DEFAULT 0,
  available_at timestamptz NOT NULL DEFAULT now(),
  lease_owner text,
  lease_expires_at timestamptz,
  last_error text,
  output_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  UNIQUE(stage, input_kind, input_id, input_version)
);
CREATE INDEX jobs_claim_idx ON jobs(status, available_at, lease_expires_at);

CREATE TABLE transcript_revisions (
  revision_id uuid PRIMARY KEY,
  receiver_id text NOT NULL,
  capture_id text NOT NULL,
  status text NOT NULL,
  text_private text,
  normalized_text_private text,
  model text NOT NULL,
  model_config_hash text NOT NULL,
  hint_set_version text NOT NULL,
  confidence double precision,
  started_at timestamptz NOT NULL,
  completed_at timestamptz NOT NULL,
  text_delete_after timestamptz,
  text_deleted_at timestamptz,
  FOREIGN KEY (receiver_id, capture_id) REFERENCES captures(receiver_id, capture_id),
  UNIQUE(receiver_id, capture_id, model_config_hash)
);

CREATE TABLE extraction_revisions (
  extraction_revision_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  transcript_revision_id uuid NOT NULL REFERENCES transcript_revisions(revision_id),
  model text NOT NULL,
  schema_version text NOT NULL,
  prompt_version text NOT NULL,
  vocabulary_version text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(transcript_revision_id, model, schema_version, prompt_version, vocabulary_version)
);

CREATE TABLE observations (
  observation_id uuid PRIMARY KEY,
  extraction_revision_id uuid NOT NULL REFERENCES extraction_revisions(extraction_revision_id),
  transcript_revision_id uuid NOT NULL REFERENCES transcript_revisions(revision_id),
  capture_id text NOT NULL,
  observation_type text NOT NULL,
  raw_value_private text NOT NULL,
  normalized_value_private text,
  confidence double precision NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  epistemic_status text NOT NULL,
  evidence_private jsonb NOT NULL,
  occurred_at timestamptz NOT NULL,
  sensitivity jsonb NOT NULL DEFAULT '[]'::jsonb,
  routine boolean NOT NULL DEFAULT false,
  supersedes_observation_id uuid REFERENCES observations(observation_id),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE incidents (
  incident_id uuid PRIMARY KEY,
  state incident_state NOT NULL,
  incident_type text,
  public_location text,
  confidence double precision NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  sensitivity jsonb NOT NULL DEFAULT '[]'::jsonb,
  first_observed_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  correlation_version text NOT NULL
);

CREATE TABLE incident_observations (
  incident_id uuid NOT NULL REFERENCES incidents(incident_id),
  observation_id uuid NOT NULL REFERENCES observations(observation_id),
  linked_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (incident_id, observation_id)
);

CREATE TABLE incident_state_history (
  history_id bigserial PRIMARY KEY,
  incident_id uuid NOT NULL REFERENCES incidents(incident_id),
  prior_state incident_state,
  new_state incident_state NOT NULL,
  reason text NOT NULL,
  evidence_ids jsonb NOT NULL,
  correlation_version text NOT NULL,
  changed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE policy_decisions (
  decision_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  incident_id uuid NOT NULL REFERENCES incidents(incident_id),
  eligible boolean NOT NULL,
  policy_version text NOT NULL,
  reasons jsonb NOT NULL,
  approved_claims jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE story_revisions (
  revision_id uuid PRIMARY KEY,
  story_id uuid NOT NULL,
  incident_id uuid NOT NULL REFERENCES incidents(incident_id),
  revision_number integer NOT NULL,
  public_payload jsonb NOT NULL,
  generator_model text NOT NULL,
  policy_decision_id uuid NOT NULL REFERENCES policy_decisions(decision_id),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(story_id, revision_number)
);

CREATE TABLE public_projections (
  projection_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  watermark timestamptz NOT NULL UNIQUE,
  payload jsonb NOT NULL,
  status text NOT NULL CHECK (status IN ('building','active','failed','superseded')),
  created_at timestamptz NOT NULL DEFAULT now(),
  activated_at timestamptz
);
CREATE UNIQUE INDEX one_active_projection ON public_projections(status) WHERE status = 'active';
CREATE VIEW active_public_projection AS
  SELECT payload FROM public_projections WHERE status = 'active';

CREATE TABLE edge_heartbeats (
  heartbeat_id bigserial PRIMARY KEY,
  receiver_id text NOT NULL REFERENCES receivers(receiver_id),
  payload jsonb NOT NULL,
  observed_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX edge_heartbeats_receiver_time ON edge_heartbeats(receiver_id, observed_at DESC);

COMMIT;
