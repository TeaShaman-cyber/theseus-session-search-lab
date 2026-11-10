BEGIN;

CREATE FUNCTION session_search.search_generation_grouped_v3(
    target_generation_id text,
    query_text text,
    scopes text[],
    result_limit integer,
    candidate_budget integer,
    session_filter text DEFAULT NULL,
    from_time double precision DEFAULT NULL,
    to_time double precision DEFAULT NULL,
    search_mode text DEFAULT 'strict'
)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, session_search, pg_temp
AS $$
DECLARE
    generation_record session_search.generations%ROWTYPE;
    lexical_query tsquery;
    candidate_count integer;
    result_groups jsonb;
BEGIN
    IF query_text IS NULL OR btrim(query_text) = '' OR char_length(query_text) > 512 THEN
        RAISE EXCEPTION 'query text out of bounds' USING ERRCODE='22023';
    END IF;
    IF result_limit IS NULL OR result_limit < 1 OR result_limit > 100 THEN
        RAISE EXCEPTION 'result limit out of bounds' USING ERRCODE='22023';
    END IF;
    IF candidate_budget IS NULL OR candidate_budget < result_limit OR candidate_budget > 300 THEN
        RAISE EXCEPTION 'candidate budget out of bounds' USING ERRCODE='22023';
    END IF;
    IF scopes IS NULL OR cardinality(scopes) < 1
       OR NOT scopes <@ ARRAY['dialogue','evidence','trace']::text[] THEN
        RAISE EXCEPTION 'search scopes out of bounds' USING ERRCODE='22023';
    END IF;
    IF from_time IS NOT NULL AND to_time IS NOT NULL AND from_time > to_time THEN
        RAISE EXCEPTION 'time bounds inverted' USING ERRCODE='22023';
    END IF;
    IF search_mode IS NULL OR search_mode NOT IN ('strict','recall') THEN
        RAISE EXCEPTION 'search mode out of bounds' USING ERRCODE='22023';
    END IF;

    SELECT * INTO generation_record
    FROM session_search.generations
    WHERE generation_id=target_generation_id;

    IF NOT FOUND OR generation_record.state <> 'READY' THEN
        RAISE EXCEPTION 'search generation not READY' USING ERRCODE='55000';
    END IF;
    IF generation_record.projection_schema_version <> 'session-search-federated-v1' THEN
        RAISE EXCEPTION 'unsupported projection schema version' USING ERRCODE='55000';
    END IF;

    IF search_mode = 'strict' THEN
        lexical_query := plainto_tsquery('simple', btrim(query_text));
    ELSE
        lexical_query := to_tsquery(
            'simple',
            array_to_string(
                tsvector_to_array(to_tsvector('simple', btrim(query_text))),
                ' | '
            )
        );
    END IF;
    WITH scored AS MATERIALIZED (
        SELECT
            m.session_id,
            s.title,
            s.coverage_state,
            m.ordinal,
            m.message_id,
            m.role,
            m.search_class,
            m.create_time,
            m.text,
            ts_rank_cd(m.search_vector,lexical_query) AS score
        FROM session_search.messages AS m
        JOIN session_search.sessions AS s
          ON s.generation_id=m.generation_id
         AND s.session_id=m.session_id
        WHERE m.generation_id=target_generation_id
          AND m.search_class=ANY(scopes)
          AND m.search_vector @@ lexical_query
          AND (session_filter IS NULL OR m.session_id=session_filter)
          AND (from_time IS NULL OR m.create_time IS NULL OR m.create_time >= from_time)
          AND (to_time IS NULL OR m.create_time IS NULL OR m.create_time <= to_time)
    ),
    candidates AS MATERIALIZED (
        SELECT
            scored.*,
            row_number() OVER (
                ORDER BY score DESC,session_id,ordinal,message_id
            ) AS candidate_rank
        FROM scored
        ORDER BY score DESC,session_id,ordinal,message_id
        LIMIT candidate_budget
    ),
    selected_texts AS MATERIALIZED (
        SELECT
            c.text,
            min(c.candidate_rank) AS group_rank
        FROM candidates AS c
        GROUP BY c.text
        ORDER BY group_rank
        LIMIT result_limit
    ),
    grouped AS (
        SELECT
            st.group_rank,
            st.text,
            (
                SELECT jsonb_build_object(
                    'session_id',r.session_id,
                    'session_title',r.title,
                    'session_coverage',r.coverage_state,
                    'ordinal',r.ordinal,
                    'message_id',r.message_id,
                    'role',r.role,
                    'search_class',r.search_class,
                    'create_time',r.create_time,
                    'score',r.score,
                    'provider',generation_record.provider,
                    'shard_id',generation_record.shard_id,
                    'generation_id',generation_record.generation_id
                )
                FROM candidates AS r
                WHERE r.text=st.text
                ORDER BY r.candidate_rank
                LIMIT 1
            ) AS representative,
            count(*)::integer AS occurrence_count,
            jsonb_agg(
                jsonb_build_object(
                    'session_id',c.session_id,
                    'message_id',c.message_id,
                    'ordinal',c.ordinal,
                    'session_title',c.title,
                    'session_coverage',c.coverage_state,
                    'role',c.role,
                    'search_class',c.search_class,
                    'create_time',c.create_time,
                    'provider',generation_record.provider,
                    'shard_id',generation_record.shard_id,
                    'generation_id',generation_record.generation_id,
                    'candidate_rank',c.candidate_rank
                )
                ORDER BY c.candidate_rank
            ) AS occurrences
        FROM selected_texts AS st
        JOIN candidates AS c ON c.text=st.text
        GROUP BY st.group_rank,st.text
        ORDER BY st.group_rank
    ),
    counts AS (
        SELECT count(*)::integer AS candidate_count FROM candidates
    )
    SELECT
        counts.candidate_count,
        coalesce(
            jsonb_agg(
                jsonb_build_object(
                    'text',g.text,
                    'representative',g.representative,
                    'occurrence_count',g.occurrence_count,
                    'occurrences',g.occurrences
                )
                ORDER BY g.group_rank
            ) FILTER (WHERE g.group_rank IS NOT NULL),
            '[]'::jsonb
        )
    INTO candidate_count,result_groups
    FROM counts
    LEFT JOIN grouped AS g ON true
    GROUP BY counts.candidate_count;

    RETURN jsonb_build_object(
        'status',CASE WHEN jsonb_array_length(result_groups)>0 THEN 'FOUND' ELSE 'NO_SIGNAL' END,
        'generation_id',generation_record.generation_id,
        'shard_id',generation_record.shard_id,
        'provider',generation_record.provider,
        'projection_schema_version',generation_record.projection_schema_version,
        'search_mode',search_mode,
        'grouping','exact_text_v1',
        'candidate_budget',candidate_budget,
        'candidate_count',candidate_count,
        'returned_group_count',jsonb_array_length(result_groups),
        'groups',result_groups
    );
END
$$;

CREATE FUNCTION session_search.search_grouped_v3(
    query_text text,
    scopes text[],
    result_limit integer,
    candidate_budget integer,
    session_filter text DEFAULT NULL,
    from_time double precision DEFAULT NULL,
    to_time double precision DEFAULT NULL,
    search_mode text DEFAULT 'strict'
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
    RETURN session_search.search_generation_grouped_v3(
        active_id,query_text,scopes,result_limit,candidate_budget,session_filter,from_time,to_time,search_mode
    );
END
$$;

ALTER FUNCTION session_search.search_generation_grouped_v3(
    text,text,text[],integer,integer,text,double precision,double precision,text
) OWNER TO session_search_owner;
ALTER FUNCTION session_search.search_grouped_v3(
    text,text[],integer,integer,text,double precision,double precision,text
) OWNER TO session_search_owner;

REVOKE ALL ON FUNCTION session_search.search_generation_grouped_v3(
    text,text,text[],integer,integer,text,double precision,double precision,text
) FROM PUBLIC;
REVOKE ALL ON FUNCTION session_search.search_grouped_v3(
    text,text[],integer,integer,text,double precision,double precision,text
) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION session_search.search_generation_grouped_v3(
    text,text,text[],integer,integer,text,double precision,double precision,text
) TO session_search_reader,session_search_materializer;
GRANT EXECUTE ON FUNCTION session_search.search_grouped_v3(
    text,text[],integer,integer,text,double precision,double precision,text
) TO session_search_reader;

COMMIT;
