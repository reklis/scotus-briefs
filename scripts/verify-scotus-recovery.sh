#!/bin/sh
set -eu
: "${RAGCHEW_DATABASE_DSN:?required}"

psql "$RAGCHEW_DATABASE_DSN" -v ON_ERROR_STOP=1 <<'SQL'
SELECT 'cases' AS object, count(*) FROM scotus_cases
UNION ALL SELECT 'documents', count(*) FROM scotus_document_revisions
UNION ALL SELECT 'document_digests', count(*) FROM scotus_document_revisions WHERE sha256 IS NOT NULL
UNION ALL SELECT 'case_history', count(*) FROM scotus_case_history
UNION ALL SELECT 'observations', count(*) FROM scotus_legal_observations
UNION ALL SELECT 'approved_claims', count(*) FROM scotus_approved_claims
UNION ALL SELECT 'brief_revisions', count(*) FROM scotus_brief_revisions
UNION ALL SELECT 'public_projections', count(*) FROM scotus_public_projections;

SELECT CASE WHEN count(*) <= 1 THEN 'active_projection_ok'
            ELSE CAST(1/0 AS text) END
FROM scotus_public_projections WHERE status='active';

SELECT count(*) AS public_view_rows FROM active_scotus_public_projection;
SQL
