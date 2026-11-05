#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from session_search.refresh_plan import RefreshPlanError, prepare_refresh_plan  # noqa: E402


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RefreshPlanError(f"DUPLICATE_JSON_KEY:{key}")
        result[key] = value
    return result


def _load_manifest(path: pathlib.Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle, object_pairs_hook=_object_no_duplicates)
    except FileNotFoundError as exc:
        raise RefreshPlanError("MANIFEST_NOT_FOUND") from exc
    except json.JSONDecodeError as exc:
        raise RefreshPlanError("MANIFEST_JSON_INVALID") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate one deterministic Session Search refresh manifest and emit a non-mutating prepared plan."
    )
    parser.add_argument("--manifest", required=True, type=pathlib.Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        prepared = prepare_refresh_plan(_load_manifest(args.manifest))
    except RefreshPlanError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print("SESSION_SEARCH_REFRESH_PREPARE_PASS")
    print(json.dumps(prepared, sort_keys=True, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
