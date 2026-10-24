BEGIN;
CREATE ROLE session_search_building_loader NOLOGIN;
SET LOCAL ROLE session_search_owner;
GRANT USAGE ON SCHEMA session_search TO session_search_building_loader;
GRANT SELECT,INSERT ON session_search.generations TO session_search_building_loader;
GRANT SELECT,INSERT ON session_search.sessions TO session_search_building_loader;
GRANT SELECT,INSERT ON session_search.messages TO session_search_building_loader;
RESET ROLE;
COMMIT;
