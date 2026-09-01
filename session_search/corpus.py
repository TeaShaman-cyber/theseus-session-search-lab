from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .corpus_store import ingest_many, resolve_corpus_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage a cumulative Session Search corpus.")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest", help="ingest one or more portable session artifacts")
    ingest.add_argument("captures", nargs="+", type=pathlib.Path)
    ingest.add_argument("--corpus")
    ingest.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "ingest":
            root = resolve_corpus_root(args.corpus)
            result = ingest_many(args.captures, root)
        else:
            parser.error("unsupported command")
            return 2
    except Exception as exc:
        print(f"CORPUS FAILED: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        for row in result["results"]:
            print(f"{row.get('status')} {row.get('source')}")
    return 0 if result.get("status") == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
