BEGIN;
CREATE ROLE session_search_ready_marker NOLOGIN;
SET LOCAL ROLE session_search_owner;
GRANT USAGE ON SCHEMA session_search TO session_search_ready_marker;
GRANT EXECUTE ON FUNCTION session_search.mark_generation_ready(text,text,integer,integer) TO session_search_ready_marker;
RESET ROLE;
COMMIT;
