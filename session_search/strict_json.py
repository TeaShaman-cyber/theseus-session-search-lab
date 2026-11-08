from __future__ import annotations

import json
from typing import Any


class StrictJsonError(ValueError):
    pass


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJsonError(f"DUPLICATE_KEY:{key}")
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> None:
    raise StrictJsonError(f"NONSTANDARD_CONSTANT:{value}")


def loads_strict(text: str) -> Any:
    if not isinstance(text, str):
        raise StrictJsonError("JSON_TEXT_INVALID")
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
    except StrictJsonError:
        raise
    except json.JSONDecodeError as exc:
        raise StrictJsonError(
            f"JSON_INVALID:{exc.msg}:line={exc.lineno}:col={exc.colno}"
        ) from exc
