#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from session_search.feedback_refresh import build_feedback_refresh, write_feedback_refresh
from session_search.feedback_sources import (
    DEFAULT_FEEDBACK_HASH_KEY,
    scan_upstash_feedback_records,
)
from session_search.retrieval_experience import load_jsonl


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"FEEDBACK_REFRESH_ENV_MISSING:{name}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh private retrieval-experience and pattern projections from durable feedback."
    )
    parser.add_argument(
        "--seed-ledger",
        type=pathlib.Path,
        default=ROOT / "experiments" / "015-retrieval-experience-ledger" / "seeds.public.jsonl",
    )
    parser.add_argument("--episodes-output", type=pathlib.Path, required=True)
    parser.add_argument("--patterns-output", type=pathlib.Path, required=True)
    args = parser.parse_args()

    rest_url = _required_env("SESSION_SEARCH_FEEDBACK_UPSTASH_REST_URL")
    token = (
        os.environ.get("SESSION_SEARCH_FEEDBACK_UPSTASH_REST_READ_TOKEN")
        or _required_env("SESSION_SEARCH_FEEDBACK_UPSTASH_REST_TOKEN")
    )
    hash_key = os.environ.get(
        "SESSION_SEARCH_FEEDBACK_UPSTASH_HASH_KEY",
        DEFAULT_FEEDBACK_HASH_KEY,
    )

    records = scan_upstash_feedback_records(
        rest_url=rest_url,
        token=token,
        hash_key=hash_key,
    )
    seeds = load_jsonl(args.seed_ledger)
    payload = build_feedback_refresh(records, seed_episodes=seeds)
    write_feedback_refresh(
        payload,
        episodes_path=args.episodes_output,
        patterns_path=args.patterns_output,
    )

    print(
        json.dumps(
            {
                "schema": payload["schema"],
                "seed_episodes": payload["source_counts"]["seed"],
                "live_feedback_episodes": payload["source_counts"]["live_feedback"],
                "total_episodes": len(payload["episodes"]),
                "patterns": len(payload["patterns"]["patterns"]),
                "policy_promotion": payload["patterns"]["policy_promotion"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
