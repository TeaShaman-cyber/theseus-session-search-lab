#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from session_search.retrieval_experience import aggregate_patterns, load_jsonl


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate retrieval-experience JSONL and build a deterministic pattern projection."
    )
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()

    episodes = load_jsonl(args.source)
    payload = aggregate_patterns(episodes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "episodes": payload["episode_count"],
                "patterns": len(payload["patterns"]),
                "policy_promotion": payload["policy_promotion"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
