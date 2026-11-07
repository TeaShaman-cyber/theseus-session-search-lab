from __future__ import annotations

import math

CANDIDATE_SCHEMA_V1 = 'theseus.session-search-candidate.v1'
_VALID_SEARCH_CLASSES = {'dialogue', 'evidence', 'trace'}
_VALID_SCORE_ORDERS = {'lower_better', 'higher_better'}
_OPTIONAL_FIELDS = (
    'session_title',
    'session_coverage',
    'message_id',
    'role',
    'content_type',
    'create_time',
    'provider',
    'shard_id',
    'generation_id',
    'shard_rank',
)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def project_candidate_v1(row: dict, *, score_order: str) -> dict:
    if score_order not in _VALID_SCORE_ORDERS:
        raise ValueError('CANDIDATE_SCORE_ORDER_INVALID')
    if not isinstance(row, dict):
        raise ValueError('CANDIDATE_ROW_INVALID')

    session_id = row.get('session_id')
    ordinal = row.get('ordinal')
    text = row.get('text')
    search_class = row.get('search_class')
    score = row.get('score')

    if not isinstance(session_id, str) or not session_id:
        raise ValueError('CANDIDATE_SESSION_INVALID')
    if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0:
        raise ValueError('CANDIDATE_ORDINAL_INVALID')
    if not isinstance(text, str):
        raise ValueError('CANDIDATE_TEXT_INVALID')
    if search_class not in _VALID_SEARCH_CLASSES:
        raise ValueError('CANDIDATE_SEARCH_CLASS_INVALID')
    if not _is_number(score):
        raise ValueError('CANDIDATE_SCORE_INVALID')

    result = {
        'schema': CANDIDATE_SCHEMA_V1,
        'session_id': session_id,
        'ordinal': ordinal,
        'search_class': search_class,
        'text': text,
    }
    for field in _OPTIONAL_FIELDS:
        if field in row:
            result[field] = row[field]

    message_id = result.get('message_id')
    if message_id is not None and not isinstance(message_id, str):
        raise ValueError('CANDIDATE_MESSAGE_ID_INVALID')
    create_time = result.get('create_time')
    if create_time is not None and not _is_number(create_time):
        raise ValueError('CANDIDATE_CREATE_TIME_INVALID')
    shard_rank = result.get('shard_rank')
    if shard_rank is not None and (
        not isinstance(shard_rank, int) or isinstance(shard_rank, bool) or shard_rank < 1
    ):
        raise ValueError('CANDIDATE_SHARD_RANK_INVALID')

    result['backend_score'] = score
    result['backend_score_order'] = score_order
    return result
