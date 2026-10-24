#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from session_search.shard_bundle import build_bundle, verify_bundle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build or verify a deterministic Session Search shard bundle."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build")
    build.add_argument("--corpus", required=True, type=pathlib.Path)
    build.add_argument("--provider", required=True)
    build.add_argument("--shard-id", required=True)
    build.add_argument("--output", required=True, type=pathlib.Path)

    verify = sub.add_parser("verify")
    verify.add_argument("--bundle", required=True, type=pathlib.Path)

    args = parser.parse_args()
    if args.command == "build":
        result = build_bundle(
            args.corpus.resolve(),
            provider=args.provider,
            shard_id=args.shard_id,
            output_path=args.output.resolve(),
        )
    else:
        result = verify_bundle(args.bundle.resolve())

    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
