BEGIN;

-- Permit a whole-case brief revision to be anchored directly to official disposition
-- evidence when no oral-argument session exists. Existing argument-linked rows and
-- foreign-key behavior are unchanged.
ALTER TABLE scotus_brief_revisions
  ALTER COLUMN argument_id DROP NOT NULL;

COMMIT;
