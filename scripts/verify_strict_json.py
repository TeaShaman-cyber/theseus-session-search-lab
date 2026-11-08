#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from session_search.strict_json import StrictJsonError, load_strict, loads_strict


def _tracked_json_paths() -> list[pathlib.Path]:
    proc = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.json", "*.jsonl"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [
        ROOT / raw.decode("utf-8")
        for raw in proc.stdout.split(b"\0")
        if raw
    ]


def _verify_jsonl(path: pathlib.Path) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise StrictJsonError("UTF8_INVALID") from exc
    except OSError as exc:
        raise StrictJsonError(f"JSON_READ_FAILED:{exc.__class__.__name__}") from exc

    lines = text.splitlines()
    if not lines:
        raise StrictJsonError("JSONL_EMPTY")

    records = 0
    for line_number, raw in enumerate(lines, 1):
        if not raw.strip():
            raise StrictJsonError(f"{line_number}:JSONL_EMPTY_LINE")
        try:
            loads_strict(raw)
        except StrictJsonError as exc:
            raise StrictJsonError(f"{line_number}:{exc}") from exc
        records += 1
    return records


def verify_path(path: pathlib.Path) -> int:
    if path.suffix.lower() == ".jsonl":
        return _verify_jsonl(path)
    if path.suffix.lower() == ".json":
        load_strict(path)
        return 1
    raise StrictJsonError("UNSUPPORTED_SUFFIX")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Strictly validate tracked or explicitly supplied JSON/JSONL files."
    )
    parser.add_argument("paths", nargs="*", type=pathlib.Path)
    args = parser.parse_args()

    paths = args.paths or _tracked_json_paths()
    files = 0
    records = 0
    for supplied in paths:
        path = supplied if supplied.is_absolute() else ROOT / supplied
        try:
            records += verify_path(path)
        except StrictJsonError as exc:
            print(f"STRICT_JSON_FAIL {path}:{exc}", file=sys.stderr)
            return 1
        files += 1

    print(f"STRICT_JSON_PASS files={files} records={records}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
