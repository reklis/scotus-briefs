#!/bin/sh
set -eu

name="ragchew-scotus-validate-$$"
dump="${TMPDIR:-/tmp}/ragchew-scotus-validate-$$.dump"
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  rm -f "$dump"
}
trap cleanup EXIT INT TERM

docker run -d --name "$name" -e POSTGRES_PASSWORD=test postgres:16-alpine >/dev/null
until docker exec "$name" pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
# The image briefly exposes its bootstrap server before restarting into the final server.
until docker exec "$name" createdb -U postgres ragchew >/dev/null 2>&1; do sleep 1; done
sleep 1
until docker exec "$name" psql -U postgres -d ragchew -Atc 'SELECT 1' >/dev/null 2>&1; do sleep 1; done
for migration in migrations/001_initial.sql migrations/002_roles.sql migrations/003_proceedings.sql migrations/004_scotus_legal_briefs.sql migrations/005_scotus_whole_case_briefs.sql migrations/006_scotus_generation_cost_controls.sql; do
  docker exec -i "$name" psql -U postgres -d ragchew -v ON_ERROR_STOP=1 < "$migration" >/dev/null
done

docker exec -i "$name" psql -U postgres -d ragchew -v ON_ERROR_STOP=1 >/dev/null <<'SQL'
INSERT INTO scotus_cases VALUES
('00000000-0000-0000-0000-000000000001','1.0','2025','Example v. Agency','Example v. Agency','25-100','https://www.supremecourt.gov/example','corrected',now(),now());
INSERT INTO scotus_argument_sessions VALUES
('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001','2025','2025-25-100',now(),1,false,'corrected','https://www.supremecourt.gov/example',NULL,now(),now());
INSERT INTO scotus_document_revisions
(document_revision_id,case_id,argument_id,document_kind,external_id,revision_number,official_url_private,status,content_type,byte_count,sha256,object_key,canonical,observed_at,ready_at)
VALUES ('00000000-0000-0000-0000-000000000003','00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000002','transcript','25-100-transcript',1,'https://www.supremecourt.gov/transcript.pdf','parsed','application/pdf',100,repeat('a',64),'official/us_supreme_court/supreme_court/case/transcript/1/document.pdf',true,now(),now());
UPDATE scotus_argument_sessions SET transcript_document_revision_id='00000000-0000-0000-0000-000000000003';
INSERT INTO scotus_document_parses
(parse_revision_id,document_revision_id,parser,parser_version,config_hash,status,page_count)
VALUES ('00000000-0000-0000-0000-000000000004','00000000-0000-0000-0000-000000000003','pypdf','1',repeat('b',64),'complete',1);
INSERT INTO scotus_extraction_revisions
(extraction_revision_id,case_id,argument_id,model,schema_version,prompt_version,vocabulary_version,parser_versions,document_revision_ids)
VALUES ('00000000-0000-0000-0000-000000000005','00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000002','fixture','1','1','1','["pypdf:1"]','["00000000-0000-0000-0000-000000000003"]');
INSERT INTO scotus_legal_observations
(observation_id,extraction_revision_id,case_id,argument_id,observation_type,legal_status,certainty,raw_value_private,speaker_kind,identity_basis,confidence,evidence_private)
VALUES ('00000000-0000-0000-0000-000000000006','00000000-0000-0000-0000-000000000005','00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000002','justice_question','questioned','attributed','Does the statute authorize the action?','justice','official_label',1,'[]');
INSERT INTO scotus_case_history
(case_id,prior_status,new_status,reason,evidence_ids,correlation_version)
VALUES ('00000000-0000-0000-0000-000000000001','argued','corrected','revised transcript','["00000000-0000-0000-0000-000000000006"]','1');
INSERT INTO scotus_approved_claims VALUES
('00000000-0000-0000-0000-000000000007','00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000002','justice_question','questioned','attributed','A justice asked whether the statute authorized the action.',NULL,'https://www.supremecourt.gov/transcript.pdf','Official transcript','page 1, line 1','["00000000-0000-0000-0000-000000000006"]','1',now());
INSERT INTO scotus_brief_revisions VALUES
('00000000-0000-0000-0000-000000000008','00000000-0000-0000-0000-000000000009','00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000002',1,'official_transcript','{}','["00000000-0000-0000-0000-000000000007"]',NULL,'fixture',now()-interval '1 hour'),
('00000000-0000-0000-0000-000000000010','00000000-0000-0000-0000-000000000009','00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000002',2,'corrected','{}','["00000000-0000-0000-0000-000000000007"]','Updated after revised transcript.','fixture',now());
INSERT INTO scotus_generation_attempts
(case_id,case_watermark,generator_model,prompt_version,outcome,completed_at)
VALUES ('00000000-0000-0000-0000-000000000001',now(),'gpt-5','fixture','validation_denied',now());
INSERT INTO scotus_public_projections(watermark,payload,status,activated_at)
VALUES (now(),'{}','active',now());
SQL

docker exec "$name" pg_dump -U postgres -Fc --no-owner ragchew > "$dump"
docker exec "$name" createdb -U postgres ragchew_restore
docker exec -i "$name" pg_restore -U postgres -d ragchew_restore --no-owner < "$dump"

for table in scotus_cases scotus_document_revisions scotus_case_history scotus_legal_observations scotus_approved_claims scotus_brief_revisions scotus_generation_attempts scotus_public_projections; do
  count=$(docker exec "$name" psql -U postgres -d ragchew_restore -Atc "SELECT count(*) FROM $table")
  test "$count" -gt 0
 done

docker exec "$name" psql -U postgres -d ragchew_restore -v ON_ERROR_STOP=1 -c \
  "SET ROLE ragchew_public; SELECT count(*) FROM active_scotus_public_projection" >/dev/null
if docker exec "$name" psql -U postgres -d ragchew_restore -v ON_ERROR_STOP=1 -c \
  "SET ROLE ragchew_public; SELECT count(*) FROM scotus_cases" >/dev/null 2>&1; then
  echo "public role unexpectedly read private cases" >&2
  exit 1
fi

docker exec "$name" psql -U postgres -d ragchew_restore -v ON_ERROR_STOP=1 >/dev/null <<'SQL'
BEGIN;
UPDATE scotus_document_revisions
SET status='content_deleted',object_key=NULL,content_deleted_at=now()
WHERE document_revision_id='00000000-0000-0000-0000-000000000003';
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM scotus_document_revisions
    WHERE document_revision_id='00000000-0000-0000-0000-000000000003'
      AND sha256=repeat('a',64) AND object_key IS NULL
  ) THEN RAISE EXCEPTION 'retention lost provenance'; END IF;
  IF (SELECT count(*) FROM scotus_brief_revisions
      WHERE brief_id='00000000-0000-0000-0000-000000000009') <> 2
  THEN RAISE EXCEPTION 'correction provenance missing'; END IF;
END $$;
ROLLBACK;
SQL

grep -q '^enabled: false' config/scotus.yaml
grep -q '^  enabled: false' config/scotus.yaml
grep -q 'RagchewScotusSourceStale' deploy/k8s/base/alerts.yaml
kubectl kustomize deploy/k8s/base >/dev/null
printf '%s\n' 'SCOTUS deployment, recovery, retention, access, alerts, and kill switches verified'
