BEGIN;
CREATE SCHEMA session_search;

CREATE TABLE session_search.generations (
    generation_id text PRIMARY KEY CHECK (generation_id ~ '^[0-9a-f]{64}$'),
    shard_id text NOT NULL,
    provider text NOT NULL,
    source_manifest_sha256 text NOT NULL CHECK (source_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    projection_schema_version text NOT NULL DEFAULT 'session-search-federated-v1',
    state text NOT NULL DEFAULT 'BUILDING' CHECK (state IN ('BUILDING','READY')),
    ready_at timestamptz,
    CHECK ((state='BUILDING' AND ready_at IS NULL) OR (state='READY' AND ready_at IS NOT NULL))
);

CREATE TABLE session_search.sessions (
    generation_id text NOT NULL REFERENCES session_search.generations(generation_id),
    session_id text NOT NULL,
    title text,
    coverage_state text NOT NULL,
    time_min double precision,
    time_max double precision,
    PRIMARY KEY (generation_id, session_id),
    CHECK (time_min IS NULL OR time_max IS NULL OR time_min <= time_max)
);

CREATE TABLE session_search.messages (
    generation_id text NOT NULL,
    session_id text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    message_id text NOT NULL,
    role text NOT NULL,
    search_class text NOT NULL CHECK (search_class IN ('dialogue','evidence','trace')),
    create_time double precision,
    text text NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,
    PRIMARY KEY (generation_id, session_id, message_id),
    FOREIGN KEY (generation_id, session_id)
        REFERENCES session_search.sessions(generation_id, session_id)
);
CREATE INDEX messages_search_vector_gin ON session_search.messages USING GIN(search_vector);
CREATE INDEX messages_generation_session_time
    ON session_search.messages(generation_id, session_id, create_time);

CREATE TABLE session_search.active_generation (
    singleton_id integer PRIMARY KEY CHECK (singleton_id=1),
    generation_id text REFERENCES session_search.generations(generation_id)
);
INSERT INTO session_search.active_generation(singleton_id,generation_id) VALUES (1,NULL);

CREATE FUNCTION session_search.mark_generation_ready(
    target_generation_id text,
    expected_source_manifest_sha256 text,
    expected_session_count integer,
    expected_message_count integer
)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, session_search, pg_temp
AS $$
DECLARE
    observed_state text;
    observed_manifest text;
    actual_sessions bigint;
    actual_messages bigint;
BEGIN
    SELECT state, source_manifest_sha256 INTO observed_state, observed_manifest
    FROM session_search.generations
    WHERE generation_id=target_generation_id FOR UPDATE;
    IF NOT FOUND OR observed_state <> 'BUILDING'
       OR observed_manifest IS DISTINCT FROM expected_source_manifest_sha256 THEN
        RAISE EXCEPTION 'READY precondition mismatch' USING ERRCODE='55000';
    END IF;
    IF expected_session_count IS NULL OR expected_session_count < 1
       OR expected_message_count IS NULL OR expected_message_count < 1 THEN
        RAISE EXCEPTION 'expected counts out of bounds' USING ERRCODE='22023';
    END IF;
    SELECT count(*) INTO actual_sessions FROM session_search.sessions
      WHERE generation_id=target_generation_id;
    SELECT count(*) INTO actual_messages FROM session_search.messages
      WHERE generation_id=target_generation_id;
    IF actual_sessions <> expected_session_count OR actual_messages <> expected_message_count THEN
        RAISE EXCEPTION 'projection count mismatch' USING ERRCODE='55000';
    END IF;
    UPDATE session_search.generations SET state='READY', ready_at=CURRENT_TIMESTAMP
      WHERE generation_id=target_generation_id;
END
$$;

CREATE FUNCTION session_search.activate_generation(
    target_generation_id text,
    expected_previous_generation_id text
)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, session_search, pg_temp
AS $$
DECLARE
    previous_generation_id text;
    candidate_state text;
BEGIN
    SELECT generation_id INTO previous_generation_id FROM session_search.active_generation
      WHERE singleton_id=1 FOR UPDATE;
    IF NOT FOUND OR previous_generation_id IS DISTINCT FROM expected_previous_generation_id THEN
        RAISE EXCEPTION 'stale activation' USING ERRCODE='55000';
    END IF;
    SELECT state INTO candidate_state FROM session_search.generations
      WHERE generation_id=target_generation_id FOR SHARE;
    IF NOT FOUND OR candidate_state <> 'READY' THEN
        RAISE EXCEPTION 'candidate not READY' USING ERRCODE='55000';
    END IF;
    UPDATE session_search.active_generation SET generation_id=target_generation_id
      WHERE singleton_id=1;
END
$$;

CREATE FUNCTION session_search.search_generation(
    target_generation_id text,
    query_text text,
    scopes text[],
    result_limit integer,
    session_filter text DEFAULT NULL,
    from_time double precision DEFAULT NULL,
    to_time double precision DEFAULT NULL
)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, session_search, pg_temp
AS $$
DECLARE
    generation_record session_search.generations%ROWTYPE;
    lexical_query tsquery;
    result_hits jsonb;
BEGIN
    IF query_text IS NULL OR btrim(query_text) = '' OR char_length(query_text) > 512 THEN
        RAISE EXCEPTION 'query text out of bounds' USING ERRCODE='22023';
    END IF;
    IF result_limit IS NULL OR result_limit < 1 OR result_limit > 100 THEN
        RAISE EXCEPTION 'result limit out of bounds' USING ERRCODE='22023';
    END IF;
    IF scopes IS NULL OR cardinality(scopes) < 1
       OR NOT scopes <@ ARRAY['dialogue','evidence','trace']::text[] THEN
        RAISE EXCEPTION 'search scopes out of bounds' USING ERRCODE='22023';
    END IF;
    IF from_time IS NOT NULL AND to_time IS NOT NULL AND from_time > to_time THEN
        RAISE EXCEPTION 'time bounds inverted' USING ERRCODE='22023';
    END IF;

    SELECT * INTO generation_record FROM session_search.generations
      WHERE generation_id=target_generation_id;
    IF NOT FOUND OR generation_record.state <> 'READY' THEN
        RAISE EXCEPTION 'search generation not READY' USING ERRCODE='55000';
    END IF;
    IF generation_record.projection_schema_version <> 'session-search-federated-v1' THEN
        RAISE EXCEPTION 'unsupported projection schema version' USING ERRCODE='55000';
    END IF;

    lexical_query := plainto_tsquery('simple', btrim(query_text));
    SELECT coalesce(jsonb_agg(
        jsonb_build_object(
            'session_id',h.session_id,'session_title',h.title,
            'session_coverage',h.coverage_state,'ordinal',h.ordinal,
            'message_id',h.message_id,'role',h.role,'search_class',h.search_class,
            'create_time',h.create_time,'text',h.text,'score',h.score
        ) ORDER BY h.score DESC,h.session_id,h.ordinal,h.message_id
    ), '[]'::jsonb)
    INTO result_hits
    FROM (
        SELECT m.session_id,s.title,s.coverage_state,m.ordinal,m.message_id,
               m.role,m.search_class,m.create_time,m.text,
               ts_rank_cd(m.search_vector,lexical_query) AS score
        FROM session_search.messages AS m
        JOIN session_search.sessions AS s
          ON s.generation_id=m.generation_id AND s.session_id=m.session_id
        WHERE m.generation_id=target_generation_id
          AND m.search_class=ANY(scopes)
          AND m.search_vector @@ lexical_query
          AND (session_filter IS NULL OR m.session_id=session_filter)
          AND (from_time IS NULL OR m.create_time IS NULL OR m.create_time >= from_time)
          AND (to_time IS NULL OR m.create_time IS NULL OR m.create_time <= to_time)
        ORDER BY score DESC,m.session_id,m.ordinal,m.message_id
        LIMIT result_limit
    ) AS h;

    RETURN jsonb_build_object(
        'status',CASE WHEN jsonb_array_length(result_hits)>0 THEN 'FOUND' ELSE 'NO_SIGNAL' END,
        'generation_id',generation_record.generation_id,
        'shard_id',generation_record.shard_id,
        'provider',generation_record.provider,
        'projection_schema_version',generation_record.projection_schema_version,
        'hits',result_hits
    );
END
$$;

CREATE FUNCTION session_search.search(
    query_text text,
    scopes text[],
    result_limit integer,
    session_filter text DEFAULT NULL,
    from_time double precision DEFAULT NULL,
    to_time double precision DEFAULT NULL
)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, session_search, pg_temp
AS $$
DECLARE active_id text;
BEGIN
    SELECT generation_id INTO active_id FROM session_search.active_generation WHERE singleton_id=1;
    IF active_id IS NULL THEN
        RAISE EXCEPTION 'active generation missing' USING ERRCODE='55000';
    END IF;
    RETURN session_search.search_generation(
        active_id,query_text,scopes,result_limit,session_filter,from_time,to_time
    );
END
$$;

CREATE FUNCTION session_search.generation_status()
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, session_search, pg_temp
AS $$
    SELECT CASE
        WHEN g.generation_id IS NULL THEN jsonb_build_object('active',false)
        ELSE jsonb_build_object(
            'active',true,'generation_id',g.generation_id,'shard_id',g.shard_id,
            'provider',g.provider,'projection_schema_version',g.projection_schema_version,
            'state',g.state,'ready_at',g.ready_at,
            'source_manifest_sha256',g.source_manifest_sha256
        )
    END
    FROM session_search.active_generation AS a
    LEFT JOIN session_search.generations AS g ON g.generation_id=a.generation_id
    WHERE a.singleton_id=1
$$;

COMMIT;
