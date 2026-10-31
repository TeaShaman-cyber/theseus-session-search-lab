BEGIN;
SET LOCAL ROLE session_search_owner;

CREATE OR REPLACE FUNCTION session_search.safe_message_search_vector(input text)
RETURNS tsvector LANGUAGE plpgsql IMMUTABLE STRICT SET search_path = pg_catalog, pg_temp AS $$
DECLARE candidate text := input;
BEGIN LOOP BEGIN RETURN pg_catalog.to_tsvector('pg_catalog.simple'::regconfig, candidate); EXCEPTION WHEN program_limit_exceeded THEN IF length(candidate) <= 1 THEN RAISE; END IF; candidate := left(candidate, GREATEST(1, length(candidate) / 2)); END; END LOOP; END
$$;

DROP INDEX IF EXISTS session_search.messages_search_vector_gin;
ALTER TABLE session_search.messages DROP COLUMN search_vector;
ALTER TABLE session_search.messages ADD COLUMN search_vector tsvector GENERATED ALWAYS AS (session_search.safe_message_search_vector(text)) STORED;
CREATE INDEX messages_search_vector_gin ON session_search.messages USING GIN(search_vector);
GRANT EXECUTE ON FUNCTION session_search.safe_message_search_vector(text) TO session_search_materializer, session_search_building_loader;
RESET ROLE;
COMMIT;
