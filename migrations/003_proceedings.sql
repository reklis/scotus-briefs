BEGIN;

-- Official-source collection and proceedings use the existing durable jobs table.
CREATE TYPE source_health AS ENUM ('disabled','healthy','quiet','degraded','review_required');
CREATE TYPE proceeding_lifecycle AS ENUM (
  'scheduled','live','delayed','completed','postponed','cancelled','archive_pending','unavailable'
);
CREATE TYPE proceeding_media_status AS ENUM (
  'discovered','collecting','ready','incomplete','rejected','expired','deleted'
);

CREATE TABLE official_sources (
  source_id text PRIMARY KEY CHECK (source_id ~ '^[a-z0-9_-]{2,64}$'),
  schema_version text NOT NULL,
  authority text NOT NULL,
  jurisdiction text NOT NULL,
  display_name text NOT NULL,
  official_index_url text NOT NULL CHECK (official_index_url LIKE 'https://%'),
  adapter text NOT NULL,
  discovery_method text NOT NULL,
  media_method text NOT NULL DEFAULT 'none',
  access_basis text,
  access_reviewed_at timestamptz,
  access_reviewed_by text,
  access_review_expires_at timestamptz,
  allowed_hosts jsonb NOT NULL DEFAULT '[]'::jsonb,
  poll_interval_seconds integer NOT NULL CHECK (poll_interval_seconds BETWEEN 30 AND 86400),
  expected_schedule text NOT NULL,
  enabled boolean NOT NULL DEFAULT false,
  health source_health NOT NULL DEFAULT 'disabled',
  last_polled_at timestamptz,
  last_success_at timestamptz,
  consecutive_failures integer NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (access_review_expires_at IS NULL OR access_reviewed_at IS NOT NULL),
  CHECK (access_review_expires_at IS NULL OR access_review_expires_at > access_reviewed_at),
  CHECK (
    NOT enabled OR (
      discovery_method <> 'none' AND access_basis IS NOT NULL AND access_basis <> '' AND
      access_reviewed_at IS NOT NULL AND access_reviewed_by IS NOT NULL AND
      jsonb_array_length(allowed_hosts) > 0
    )
  )
);

CREATE TABLE official_source_approval_history (
  approval_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id text NOT NULL REFERENCES official_sources(source_id),
  enabled boolean NOT NULL,
  access_basis text,
  discovery_method text NOT NULL,
  media_method text NOT NULL,
  allowed_hosts jsonb NOT NULL,
  reviewed_at timestamptz NOT NULL,
  reviewed_by text NOT NULL,
  review_expires_at timestamptz,
  reason text NOT NULL
);

CREATE TABLE source_checkpoints (
  source_id text NOT NULL REFERENCES official_sources(source_id),
  checkpoint_kind text NOT NULL,
  checkpoint_key text NOT NULL,
  checkpoint_value jsonb NOT NULL,
  etag text,
  last_modified text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (source_id, checkpoint_kind, checkpoint_key)
);

CREATE TABLE proceedings (
  proceeding_id uuid PRIMARY KEY,
  source_id text NOT NULL REFERENCES official_sources(source_id),
  schema_version text NOT NULL,
  authority text NOT NULL,
  jurisdiction text NOT NULL,
  external_id text NOT NULL,
  proceeding_type text NOT NULL,
  title_private text NOT NULL,
  official_url text NOT NULL CHECK (official_url LIKE 'https://%'),
  lifecycle proceeding_lifecycle NOT NULL,
  scheduled_start_at timestamptz NOT NULL,
  scheduled_end_at timestamptz,
  actual_start_at timestamptz,
  actual_end_at timestamptz,
  discovered_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  archive_wait_started_at timestamptz,
  UNIQUE (source_id, external_id),
  CHECK (scheduled_end_at IS NULL OR scheduled_end_at > scheduled_start_at),
  CHECK (actual_end_at IS NULL OR (actual_start_at IS NOT NULL AND actual_end_at > actual_start_at))
);
CREATE INDEX proceedings_schedule_idx ON proceedings(source_id, scheduled_start_at, lifecycle);

CREATE TABLE proceeding_revisions (
  revision_id uuid PRIMARY KEY,
  proceeding_id uuid NOT NULL REFERENCES proceedings(proceeding_id),
  revision_number integer NOT NULL CHECK (revision_number > 0),
  source_updated_at timestamptz,
  observed_at timestamptz NOT NULL,
  payload_private jsonb NOT NULL,
  payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  UNIQUE (proceeding_id, revision_number),
  UNIQUE (proceeding_id, payload_sha256)
);

CREATE TABLE proceeding_media_assets (
  media_asset_id uuid PRIMARY KEY,
  proceeding_id uuid NOT NULL REFERENCES proceedings(proceeding_id),
  kind text NOT NULL CHECK (kind IN ('live','archive')),
  revision_number integer NOT NULL CHECK (revision_number > 0),
  source_url_private text NOT NULL,
  source_external_id text NOT NULL,
  content_type text NOT NULL CHECK (content_type LIKE 'audio/%' OR content_type LIKE 'video/%'),
  status proceeding_media_status NOT NULL DEFAULT 'discovered',
  byte_count bigint CHECK (byte_count > 0),
  sha256 text CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  duration_ms bigint CHECK (duration_ms > 0),
  object_key text UNIQUE,
  canonical boolean NOT NULL DEFAULT false,
  discovered_at timestamptz NOT NULL,
  ready_at timestamptz,
  delete_after timestamptz,
  deleted_at timestamptz,
  diagnostic_private text,
  UNIQUE (proceeding_id, kind, revision_number),
  UNIQUE (proceeding_id, kind, source_external_id)
);
CREATE UNIQUE INDEX one_canonical_proceeding_media
  ON proceeding_media_assets(proceeding_id) WHERE canonical;

CREATE TABLE proceeding_media_chunks (
  chunk_id uuid PRIMARY KEY,
  media_asset_id uuid NOT NULL REFERENCES proceeding_media_assets(media_asset_id),
  sequence integer NOT NULL CHECK (sequence >= 0),
  source_start_ms bigint NOT NULL CHECK (source_start_ms >= 0),
  source_end_ms bigint NOT NULL CHECK (source_end_ms > source_start_ms),
  overlap_ms integer NOT NULL DEFAULT 0 CHECK (overlap_ms >= 0),
  content_type text NOT NULL CHECK (content_type LIKE 'audio/%' OR content_type LIKE 'video/%'),
  byte_count bigint NOT NULL CHECK (byte_count > 0),
  sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  object_key text NOT NULL UNIQUE,
  discontinuity_before boolean NOT NULL DEFAULT false,
  captured_at timestamptz NOT NULL,
  delete_after timestamptz,
  deleted_at timestamptz,
  UNIQUE (media_asset_id, sequence),
  CHECK (overlap_ms < source_end_ms - source_start_ms)
);

CREATE TABLE official_documents (
  document_id uuid PRIMARY KEY,
  proceeding_id uuid REFERENCES proceedings(proceeding_id),
  source_id text NOT NULL REFERENCES official_sources(source_id),
  document_type text NOT NULL,
  external_id text NOT NULL,
  revision_number integer NOT NULL CHECK (revision_number > 0),
  title_private text NOT NULL,
  official_url text NOT NULL CHECK (official_url LIKE 'https://%'),
  published_at timestamptz,
  observed_at timestamptz NOT NULL,
  content_type text NOT NULL,
  byte_count bigint CHECK (byte_count > 0),
  sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  object_key text UNIQUE,
  extracted_text_private text,
  delete_after timestamptz,
  content_deleted_at timestamptz,
  UNIQUE (source_id, external_id, revision_number),
  UNIQUE (source_id, external_id, sha256)
);
CREATE INDEX official_documents_proceeding_idx ON official_documents(proceeding_id, document_type);

CREATE TABLE proceeding_participants (
  participant_id uuid PRIMARY KEY,
  proceeding_id uuid NOT NULL REFERENCES proceedings(proceeding_id),
  display_name_private text,
  public_name text,
  participant_role text NOT NULL,
  official_role text,
  identity_basis text NOT NULL,
  identity_evidence_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (public_name IS NULL OR participant_role = 'public_official'),
  CHECK (public_name IS NULL OR identity_basis <> 'anonymous')
);

CREATE TABLE proceeding_transcript_revisions (
  revision_id uuid PRIMARY KEY,
  media_asset_id uuid NOT NULL REFERENCES proceeding_media_assets(media_asset_id),
  status text NOT NULL,
  model text NOT NULL,
  model_config_hash text NOT NULL CHECK (model_config_hash ~ '^[0-9a-f]{64}$'),
  language text NOT NULL,
  hint_set_version text NOT NULL,
  diarization_config_hash text NOT NULL CHECK (diarization_config_hash ~ '^[0-9a-f]{64}$'),
  confidence double precision CHECK (confidence BETWEEN 0 AND 1),
  started_at timestamptz NOT NULL,
  completed_at timestamptz NOT NULL,
  text_delete_after timestamptz,
  text_deleted_at timestamptz,
  UNIQUE (media_asset_id, model_config_hash, diarization_config_hash)
);

CREATE TABLE proceeding_transcript_segments (
  segment_id uuid PRIMARY KEY,
  transcript_revision_id uuid NOT NULL REFERENCES proceeding_transcript_revisions(revision_id),
  media_asset_id uuid NOT NULL REFERENCES proceeding_media_assets(media_asset_id),
  chunk_id uuid REFERENCES proceeding_media_chunks(chunk_id),
  sequence integer NOT NULL CHECK (sequence >= 0),
  start_ms bigint NOT NULL CHECK (start_ms >= 0),
  end_ms bigint NOT NULL CHECK (end_ms > start_ms),
  status text NOT NULL,
  text_private text,
  normalized_text_private text,
  speaker_label text,
  participant_id uuid REFERENCES proceeding_participants(participant_id),
  identity_basis text NOT NULL,
  confidence double precision CHECK (confidence BETWEEN 0 AND 1),
  source_segment_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  UNIQUE (transcript_revision_id, sequence),
  CHECK ((status = 'complete' AND text_private IS NOT NULL) OR (status <> 'complete' AND text_private IS NULL))
);

CREATE TABLE proceeding_extraction_revisions (
  extraction_revision_id uuid PRIMARY KEY,
  proceeding_id uuid NOT NULL REFERENCES proceedings(proceeding_id),
  transcript_revision_id uuid REFERENCES proceeding_transcript_revisions(revision_id),
  model text NOT NULL,
  schema_version text NOT NULL,
  prompt_version text NOT NULL,
  vocabulary_version text NOT NULL,
  document_versions jsonb NOT NULL DEFAULT '[]'::jsonb,
  media_versions jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (
    proceeding_id, transcript_revision_id, model, schema_version,
    prompt_version, vocabulary_version, document_versions, media_versions
  )
);

CREATE TABLE proceeding_observations (
  observation_id uuid PRIMARY KEY,
  extraction_revision_id uuid NOT NULL REFERENCES proceeding_extraction_revisions(extraction_revision_id),
  proceeding_id uuid NOT NULL REFERENCES proceedings(proceeding_id),
  jurisdiction text NOT NULL,
  authority text NOT NULL,
  body_private text NOT NULL,
  topic_hint_private text,
  participant_id uuid REFERENCES proceeding_participants(participant_id),
  speaker_label_private text,
  identity_basis text NOT NULL,
  statement_type text NOT NULL,
  action_type text NOT NULL,
  action_status text NOT NULL,
  raw_value_private text NOT NULL,
  normalized_value_private text,
  target_identifier text,
  vote_yes integer CHECK (vote_yes >= 0),
  vote_no integer CHECK (vote_no >= 0),
  vote_other integer CHECK (vote_other >= 0),
  confidence double precision NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  occurred_at timestamptz NOT NULL,
  evidence_private jsonb NOT NULL,
  sensitive boolean NOT NULL DEFAULT false,
  supersedes_observation_id uuid REFERENCES proceeding_observations(observation_id),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX proceeding_observations_time_idx ON proceeding_observations(proceeding_id, occurred_at);

CREATE TABLE proceeding_topics (
  topic_id uuid PRIMARY KEY,
  proceeding_id uuid NOT NULL REFERENCES proceedings(proceeding_id),
  title_private text NOT NULL,
  official_identifier text,
  first_observed_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  correlation_version text NOT NULL,
  UNIQUE (proceeding_id, official_identifier)
);

CREATE TABLE proceeding_topic_observations (
  topic_id uuid NOT NULL REFERENCES proceeding_topics(topic_id),
  observation_id uuid NOT NULL REFERENCES proceeding_observations(observation_id),
  linked_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (topic_id, observation_id)
);

CREATE TABLE government_events (
  event_id uuid PRIMARY KEY,
  jurisdiction text NOT NULL,
  authority text NOT NULL,
  event_kind text NOT NULL,
  official_identifier text,
  title_private text NOT NULL,
  current_status text NOT NULL,
  first_observed_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  correlation_version text NOT NULL
);
CREATE UNIQUE INDEX government_event_official_id
  ON government_events(jurisdiction, authority, event_kind, official_identifier)
  WHERE official_identifier IS NOT NULL;

CREATE TABLE government_event_proceedings (
  event_id uuid NOT NULL REFERENCES government_events(event_id),
  proceeding_id uuid NOT NULL REFERENCES proceedings(proceeding_id),
  linked_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (event_id, proceeding_id)
);

CREATE TABLE government_event_topics (
  event_id uuid NOT NULL REFERENCES government_events(event_id),
  topic_id uuid NOT NULL REFERENCES proceeding_topics(topic_id),
  linked_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (event_id, topic_id)
);

CREATE TABLE government_event_observations (
  event_id uuid NOT NULL REFERENCES government_events(event_id),
  observation_id uuid NOT NULL REFERENCES proceeding_observations(observation_id),
  linked_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (event_id, observation_id)
);

CREATE TABLE government_event_history (
  history_id bigserial PRIMARY KEY,
  event_id uuid NOT NULL REFERENCES government_events(event_id),
  prior_status text,
  new_status text NOT NULL,
  reason text NOT NULL,
  evidence_ids jsonb NOT NULL,
  correlation_version text NOT NULL,
  changed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE proceeding_policy_decisions (
  decision_id uuid PRIMARY KEY,
  event_id uuid NOT NULL REFERENCES government_events(event_id),
  eligible boolean NOT NULL,
  policy_version text NOT NULL,
  reasons jsonb NOT NULL,
  approved_claims jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (event_id, policy_version, approved_claims)
);

CREATE TABLE proceeding_story_revisions (
  revision_id uuid PRIMARY KEY,
  story_id uuid NOT NULL,
  event_id uuid NOT NULL REFERENCES government_events(event_id),
  revision_number integer NOT NULL CHECK (revision_number > 0),
  public_payload jsonb NOT NULL,
  generator_model text NOT NULL,
  policy_decision_id uuid NOT NULL REFERENCES proceeding_policy_decisions(decision_id),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (story_id, revision_number)
);

CREATE TABLE proceeding_public_projections (
  projection_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  watermark timestamptz NOT NULL UNIQUE,
  payload jsonb NOT NULL,
  status text NOT NULL CHECK (status IN ('building','active','failed','superseded')),
  created_at timestamptz NOT NULL DEFAULT now(),
  activated_at timestamptz
);
CREATE UNIQUE INDEX one_active_proceeding_projection
  ON proceeding_public_projections(status) WHERE status = 'active';
CREATE VIEW active_proceeding_public_projection AS
  SELECT payload FROM proceeding_public_projections WHERE status = 'active';

-- Deployment-specific LOGIN roles should be members of these groups.
DO $$ BEGIN
  CREATE ROLE ragchew_collector NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
GRANT CONNECT ON DATABASE ragchew TO ragchew_collector;
GRANT USAGE ON SCHEMA public TO ragchew_collector;

GRANT SELECT, UPDATE ON official_sources TO ragchew_collector;
GRANT SELECT, INSERT, UPDATE ON source_checkpoints, proceedings, proceeding_media_assets, jobs
  TO ragchew_collector;
GRANT SELECT, INSERT ON proceeding_revisions, proceeding_media_chunks, official_documents
  TO ragchew_collector;

GRANT SELECT ON official_sources, proceedings, proceeding_revisions, proceeding_media_assets,
  proceeding_media_chunks, official_documents TO ragchew_worker;
GRANT SELECT, INSERT, UPDATE ON proceeding_participants, proceeding_topics, government_events, jobs
  TO ragchew_worker;
GRANT SELECT, INSERT ON proceeding_transcript_revisions, proceeding_transcript_segments,
  proceeding_extraction_revisions, proceeding_observations, proceeding_topic_observations,
  government_event_proceedings, government_event_topics, government_event_observations,
  government_event_history TO ragchew_worker;
GRANT USAGE, SELECT ON SEQUENCE government_event_history_history_id_seq TO ragchew_worker;

GRANT SELECT ON official_sources, proceedings, official_documents, proceeding_participants,
  proceeding_observations, proceeding_topics, government_events, government_event_proceedings,
  government_event_topics, government_event_observations, government_event_history
  TO ragchew_publisher;
GRANT SELECT, INSERT, UPDATE ON proceeding_policy_decisions, proceeding_story_revisions,
  proceeding_public_projections TO ragchew_publisher;

GRANT SELECT, UPDATE ON proceeding_media_assets, proceeding_media_chunks, official_documents,
  proceeding_transcript_revisions TO ragchew_retention;

REVOKE ALL ON official_sources, proceedings, proceeding_revisions, proceeding_media_assets,
  proceeding_media_chunks, official_documents, proceeding_participants,
  proceeding_transcript_revisions, proceeding_transcript_segments,
  proceeding_extraction_revisions, proceeding_observations, proceeding_topics,
  proceeding_topic_observations, government_events, government_event_proceedings,
  government_event_topics, government_event_observations, government_event_history,
  proceeding_policy_decisions, proceeding_story_revisions, proceeding_public_projections
  FROM ragchew_public;
GRANT SELECT ON active_proceeding_public_projection TO ragchew_public;

COMMIT;
