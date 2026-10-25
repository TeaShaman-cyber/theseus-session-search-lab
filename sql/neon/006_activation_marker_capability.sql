BEGIN;
CREATE ROLE session_search_activation_marker NOLOGIN;
SET LOCAL ROLE session_search_owner;
GRANT USAGE ON SCHEMA session_search TO session_search_activation_marker;
GRANT EXECUTE ON FUNCTION session_search.activate_generation(text,text) TO session_search_activation_marker;
RESET ROLE;
COMMIT;
