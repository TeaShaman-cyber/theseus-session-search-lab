from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .corpus_store import (
    CorpusPaths,
    ingest_many,
    read_lock_status,
    rebuild_corpus,
    resolve_corpus_root,
    verify_corpus,
)


def _add_common_corpus_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--corpus")
    parser.add_argument("--json", action="store_true")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage a cumulative Session Search corpus.")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="ingest one or more portable session artifacts")
    ingest.add_argument("captures", nargs="+", type=pathlib.Path)
    _add_common_corpus_args(ingest)

    verify = sub.add_parser("verify", help="verify accepted artifacts and the current projection")
    _add_common_corpus_args(verify)

    rebuild = sub.add_parser("rebuild", help="rebuild projection from accepted artifact membership")
    _add_common_corpus_args(rebuild)

    lock_status = sub.add_parser("lock-status", help="inspect corpus mutation lock without modifying it")
    _add_common_corpus_args(lock_status)

    args = parser.parse_args(argv)
    try:
        root = resolve_corpus_root(args.corpus)
        if args.command == "ingest":
            result = ingest_many(args.captures, root)
            exit_code = 0 if result.get("status") == "COMPLETE" else 1
        elif args.command == "verify":
            result = verify_corpus(root)
            exit_code = 0 if result.get("status") == "VERIFIED" else 1
        elif args.command == "rebuild":
            result = rebuild_corpus(root)
            exit_code = 0 if result.get("status") == "REBUILT" else 1
        elif args.command == "lock-status":
            result = read_lock_status(CorpusPaths.from_root(root))
            exit_code = 0
        else:
            parser.error("unsupported command")
            return 2
    except Exception as exc:
        print(f"CORPUS FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    elif args.command == "ingest":
        for row in result["results"]:
            print(f"{row.get('status')} {row.get('source')}")
    else:
        print(result.get("status") or ("LOCKED" if result.get("locked") else "UNLOCKED"))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
