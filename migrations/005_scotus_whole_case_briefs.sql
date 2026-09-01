BEGIN;

-- The case-level publisher verifies that every historical argument transcript
-- has a complete parse before synthesizing one durable case page.
GRANT SELECT ON scotus_document_parses TO ragchew_publisher;
GRANT UPDATE ON scotus_argument_sessions TO ragchew_publisher;

CREATE INDEX IF NOT EXISTS scotus_briefs_case_revision_idx
  ON scotus_brief_revisions(case_id, revision_number DESC);

COMMIT;
