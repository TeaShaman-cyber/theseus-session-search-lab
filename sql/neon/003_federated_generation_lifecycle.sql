BEGIN;
SET LOCAL ROLE session_search_owner;

CREATE FUNCTION session_search.reject_direct_ready_generation()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, session_search, pg_temp
AS $$
BEGIN
    IF NEW.state <> 'BUILDING' OR NEW.ready_at IS NOT NULL THEN
        RAISE EXCEPTION 'generation insert must start BUILDING' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER generations_building_insert_only
BEFORE INSERT ON session_search.generations
FOR EACH ROW EXECUTE FUNCTION session_search.reject_direct_ready_generation();

CREATE FUNCTION session_search.guard_projection_mutation()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, session_search, pg_temp
AS $$
DECLARE
    target_generation_id text;
    target_state text;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.generation_id IS DISTINCT FROM OLD.generation_id THEN
        RAISE EXCEPTION 'child generation_id is immutable' USING ERRCODE='55000';
    END IF;

    target_generation_id := CASE WHEN TG_OP = 'INSERT' THEN NEW.generation_id ELSE OLD.generation_id END;

    SELECT state INTO target_state
    FROM session_search.generations
    WHERE generation_id = target_generation_id;

    IF target_state IS DISTINCT FROM 'BUILDING' THEN
        RAISE EXCEPTION 'projection generation is immutable after READY' USING ERRCODE='55000';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER sessions_building_mutation_only
BEFORE INSERT OR UPDATE OR DELETE ON session_search.sessions
FOR EACH ROW EXECUTE FUNCTION session_search.guard_projection_mutation();

CREATE TRIGGER messages_building_mutation_only
BEFORE INSERT OR UPDATE OR DELETE ON session_search.messages
FOR EACH ROW EXECUTE FUNCTION session_search.guard_projection_mutation();

RESET ROLE;
COMMIT;
