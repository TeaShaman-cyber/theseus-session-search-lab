#!/usr/bin/env python3
"""Cheap local PostgreSQL + PL/pgSQL syntax gate for versioned SQL."""

from __future__ import annotations

import argparse
import pathlib
import sys

try:
    from pglast import parse_plpgsql, parse_sql, split
    from pglast.ast import CreateFunctionStmt, DefElem
except ImportError as exc:  # pragma: no cover - exercised by environment setup
    raise SystemExit(
        "SQL syntax gate requires pglast; install: "
        "python3 -m pip install -r requirements-dev.txt"
    ) from exc


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_SQL_DIR = ROOT / "sql" / "neon"


def _create_function_language(statement: str) -> str | None:
    tree = parse_sql(statement)
    if len(tree) != 1 or not isinstance(tree[0].stmt, CreateFunctionStmt):
        return None
    for option in tree[0].stmt.options or ():
        if isinstance(option, DefElem) and option.defname == "language":
            language = getattr(option.arg, "sval", None)
            return language.lower() if isinstance(language, str) else None
    return None


def check_sql_text(text: str, *, source: str = "<memory>") -> int:
    """Parse outer SQL and procedural bodies. Return function statements checked."""
    try:
        parse_sql(text)
    except Exception as exc:
        raise ValueError(f"{source}: PostgreSQL syntax error: {exc}") from exc

    function_count = 0
    try:
        statements = split(text)
    except Exception as exc:
        raise ValueError(f"{source}: PostgreSQL split/parse error: {exc}") from exc

    for statement in statements:
        try:
            language = _create_function_language(statement)
        except Exception as exc:
            raise ValueError(f"{source}: PostgreSQL function syntax error: {exc}") from exc

        if language == "plpgsql":
            try:
                # pglast/libpg_query expects the complete CREATE FUNCTION statement.
                parse_plpgsql(statement)
            except Exception as exc:
                raise ValueError(f"{source}: PL/pgSQL syntax error: {exc}") from exc
            function_count += 1
        elif language == "sql":
            # Outer parse already validates SQL-language CREATE FUNCTION syntax.
            function_count += 1

    return function_count


def check_paths(paths: list[pathlib.Path]) -> tuple[int, int]:
    files_checked = 0
    functions_checked = 0
    for path in paths:
        functions_checked += check_sql_text(
            path.read_text(encoding="utf-8"),
            source=str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path),
        )
        files_checked += 1
    return files_checked, functions_checked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=pathlib.Path)
    args = parser.parse_args(argv)

    paths = args.paths or sorted(DEFAULT_SQL_DIR.glob("*.sql"))
    if not paths:
        raise SystemExit("SQL syntax gate: no SQL files found")
    files_checked, functions_checked = check_paths(paths)
    print(
        f"SQL_SYNTAX_PASS files={files_checked} "
        f"function_statements={functions_checked}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
