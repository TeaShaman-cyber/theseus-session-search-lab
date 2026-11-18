BEGIN;

-- Reader-only, immutable READY generation: a single statement owns each page.
-- Position (ordinal, message_id, offset) refers to the next unread character;
-- offset=0 with a non-null message_id means AFTER that complete message.
CREATE FUNCTION session_search.read_generation_session_v1(
    target_generation_id text,
    target_session_id text,
    start_ordinal integer,
    cursor_message_id text,
    cursor_offset integer,
    result_limit integer,
    max_chars integer
)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, session_search, pg_temp
AS $$
DECLARE
    gen session_search.generations%ROWTYPE;
    ses session_search.sessions%ROWTYPE;
    item record;
    page jsonb := '[]'::jsonb;
    next_ordinal integer := start_ordinal;
    next_message_id text := cursor_message_id;
    next_offset integer := cursor_offset;
    remaining integer := max_chars;
    start_at integer;
    segment text;
    complete boolean;
    has_more boolean;
    text_length integer;
BEGIN
    IF target_session_id IS NULL OR target_session_id = '' OR char_length(target_session_id) > 512
       OR start_ordinal IS NULL OR start_ordinal < 0
       OR cursor_offset IS NULL OR cursor_offset < 0
       OR (cursor_offset > 0 AND cursor_message_id IS NULL)
       OR result_limit IS NULL OR result_limit NOT BETWEEN 1 AND 100
       OR max_chars IS NULL OR max_chars NOT BETWEEN 1 AND 100000 THEN
        RAISE EXCEPTION 'session browse bounds invalid' USING ERRCODE='22023';
    END IF;

    SELECT * INTO gen FROM session_search.generations
    WHERE generation_id = target_generation_id AND state = 'READY';
    IF NOT FOUND OR gen.projection_schema_version <> 'session-search-federated-v1' THEN
        RAISE EXCEPTION 'browse generation not READY' USING ERRCODE='55000';
    END IF;
    SELECT * INTO ses FROM session_search.sessions
    WHERE generation_id = target_generation_id AND session_id = target_session_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'session not present in generation' USING ERRCODE='55000';
    END IF;

    IF cursor_message_id IS NOT NULL THEN
        SELECT char_length(m.text) INTO text_length FROM session_search.messages AS m
        WHERE m.generation_id = target_generation_id
          AND m.session_id = target_session_id
          AND m.ordinal = start_ordinal
          AND m.message_id = cursor_message_id;
        IF NOT FOUND OR cursor_offset > 0 AND cursor_offset >= text_length THEN
            RAISE EXCEPTION 'browse cursor not on an existing message' USING ERRCODE='22023';
        END IF;
    END IF;

    FOR item IN
        SELECT m.ordinal, m.message_id, m.role, m.search_class, m.create_time
        FROM session_search.messages AS m
        WHERE m.generation_id = target_generation_id
          AND m.session_id = target_session_id
          AND (
             m.ordinal > start_ordinal
             OR (m.ordinal = start_ordinal AND (
                cursor_message_id IS NULL
                OR m.message_id COLLATE "C" > cursor_message_id COLLATE "C"
                OR (m.message_id = cursor_message_id AND cursor_offset > 0)
             ))
          )
        ORDER BY m.ordinal, m.message_id COLLATE "C"
        LIMIT result_limit
    LOOP
        start_at := CASE WHEN item.ordinal = start_ordinal
                                AND item.message_id = cursor_message_id
                         THEN cursor_offset ELSE 0 END;
        -- Never serialize a full message to the client to deliver a short page.
        SELECT char_length(m.text), substring(m.text FROM start_at + 1 FOR remaining)
          INTO text_length, segment
        FROM session_search.messages AS m
        WHERE m.generation_id = target_generation_id
          AND m.session_id = target_session_id
          AND m.message_id = item.message_id;

        complete := start_at + char_length(segment) >= text_length;
        page := page || jsonb_build_array(jsonb_build_object(
            'ordinal', item.ordinal,
            'message_id', item.message_id,
            'role', item.role,
            'search_class', item.search_class,
            'create_time', item.create_time,
            'text', segment,
            'text_offset', start_at,
            'text_total_chars', text_length,
            'complete_message', complete
        ));
        remaining := remaining - char_length(segment);
        next_ordinal := item.ordinal;
        next_message_id := item.message_id;
        next_offset := CASE WHEN complete THEN 0 ELSE start_at + char_length(segment) END;

        IF NOT complete OR remaining = 0 THEN
            EXIT;
        END IF;
    END LOOP;

    IF next_offset > 0 THEN
        has_more := true;
    ELSE
        SELECT EXISTS (
            SELECT 1 FROM session_search.messages AS m
            WHERE m.generation_id = target_generation_id
              AND m.session_id = target_session_id
              AND (m.ordinal > next_ordinal
                   OR (m.ordinal = next_ordinal AND
                       (next_message_id IS NULL
                        OR m.message_id COLLATE "C" > next_message_id COLLATE "C")))
        ) INTO has_more;
    END IF;
    RETURN jsonb_build_object(
        'status', 'OK',
        'generation_id', gen.generation_id,
        'shard_id', gen.shard_id,
        'provider', gen.provider,
        'session_id', ses.session_id,
        'session_title', ses.title,
        'session_coverage', ses.coverage_state,
        'messages', page,
        'has_more', has_more,
        'next_position', CASE WHEN has_more THEN jsonb_build_object(
            'ordinal', next_ordinal, 'message_id', next_message_id,
            'offset', next_offset
        ) ELSE NULL END
    );
END
$$;

ALTER FUNCTION session_search.read_generation_session_v1(
    text,text,integer,text,integer,integer,integer
) OWNER TO session_search_owner;
REVOKE ALL ON FUNCTION session_search.read_generation_session_v1(
    text,text,integer,text,integer,integer,integer
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION session_search.read_generation_session_v1(
    text,text,integer,text,integer,integer,integer
) TO session_search_reader;

COMMIT;
