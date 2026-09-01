-- Group roles. Bind deployment-specific LOGIN roles to these groups; do not put passwords here.
DO $$ BEGIN
  CREATE ROLE ragchew_ingestion NOLOGIN;
  CREATE ROLE ragchew_worker NOLOGIN;
  CREATE ROLE ragchew_publisher NOLOGIN;
  CREATE ROLE ragchew_retention NOLOGIN;
  CREATE ROLE ragchew_public NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

GRANT CONNECT ON DATABASE ragchew TO ragchew_ingestion, ragchew_worker,
  ragchew_publisher, ragchew_retention, ragchew_public;
GRANT USAGE ON SCHEMA public TO ragchew_ingestion, ragchew_worker,
  ragchew_publisher, ragchew_retention, ragchew_public;

GRANT SELECT, INSERT, UPDATE ON receivers, captures, jobs, edge_heartbeats
  TO ragchew_ingestion;
GRANT USAGE, SELECT ON SEQUENCE edge_heartbeats_heartbeat_id_seq TO ragchew_ingestion;

GRANT SELECT, INSERT, UPDATE ON captures, jobs, transcript_revisions,
  extraction_revisions, observations, incidents, incident_observations,
  incident_state_history TO ragchew_worker;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ragchew_worker;

GRANT SELECT ON incidents, incident_observations, observations,
  transcript_revisions, captures TO ragchew_publisher;
GRANT SELECT, INSERT, UPDATE ON policy_decisions, story_revisions, public_projections
  TO ragchew_publisher;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ragchew_publisher;

GRANT SELECT, UPDATE ON captures, transcript_revisions TO ragchew_retention;
GRANT SELECT ON jobs TO ragchew_retention;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ragchew_public;
GRANT SELECT ON active_public_projection TO ragchew_public;
