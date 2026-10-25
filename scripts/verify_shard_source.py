#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from session_search.shard_integrity import verify_shard_source_integrity


def main() -> int:
    p = argparse.ArgumentParser(description="Verify one provider-aware shard projection by replaying its accepted source artifacts.")
    p.add_argument("--corpus", required=True, type=pathlib.Path)
    p.add_argument("--provider", required=True)
    p.add_argument("--shard-id", required=True)
    args = p.parse_args()
    result = verify_shard_source_integrity(
        args.corpus.resolve(), provider=args.provider, shard_id=args.shard_id
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result.get("status") == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
