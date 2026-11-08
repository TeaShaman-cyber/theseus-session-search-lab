-- Optional feedback replica (issue #232); no relation to corpus/generation tables.
-- Apply only in the explicitly selected private Neon database.
-- Portable authority remains theseus.session-search-feedback-record.v0.
CREATE SCHEMA IF NOT EXISTS session_feedback;
REVOKE ALL ON SCHEMA session_feedback FROM PUBLIC;

CREATE TABLE IF NOT EXISTS session_feedback.records_v0 (
  feedback_ref text PRIMARY KEY
    CHECK (feedback_ref ~ '^[0-9a-f]{32}$'),
  record jsonb NOT NULL,
  CONSTRAINT feedback_record_shape CHECK (
    (
      record @> jsonb_build_object(
        'schema', 'theseus.session-search-feedback-record.v0',
        'feedback_ref', feedback_ref
      )
      AND jsonb_typeof(record->'feedback') = 'object'
      AND jsonb_typeof(record->'received_at') = 'string'
    ) IS TRUE
  )
);
REVOKE ALL ON TABLE session_feedback.records_v0 FROM PUBLIC;
