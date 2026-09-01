BEGIN;

-- Reserve one immutable model attempt per unchanged case/prompt/model input. A
-- started or rejected attempt is not silently purchased again; an operator must
-- change the prompt version or the case evidence watermark to authorize replay.
CREATE TABLE scotus_generation_attempts (
  attempt_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  case_id uuid NOT NULL REFERENCES scotus_cases(case_id),
  case_watermark timestamptz NOT NULL,
  generator_model text NOT NULL,
  prompt_version text NOT NULL,
  outcome text NOT NULL CHECK (
    outcome IN ('started', 'accepted', 'validation_denied', 'request_failed')
  ),
  failure_code text,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  UNIQUE (case_id, case_watermark, generator_model, prompt_version)
);

CREATE INDEX scotus_generation_attempts_case_created_idx
  ON scotus_generation_attempts(case_id, created_at DESC);

GRANT SELECT, INSERT, UPDATE ON scotus_generation_attempts TO ragchew_publisher;
REVOKE ALL ON scotus_generation_attempts FROM ragchew_public;

COMMIT;
