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

from session_search.corpus_store import verify_corpus  # noqa: E402
from session_search.neon_materializer import build_source_manifest, load_building_generation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize a verified Session Search corpus into a Neon BUILDING generation."
    )
    parser.add_argument("--corpus", required=True, type=pathlib.Path)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--shard-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--database-url-env", default="SESSION_SEARCH_DATABASE_URL")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    corpus = args.corpus.resolve()
    manifest = build_source_manifest(corpus, provider=args.provider, shard_id=args.shard_id)
    if args.dry_run:
        print(json.dumps(manifest, sort_keys=True, indent=2))
        return 0

    verification = verify_corpus(corpus)
    if verification.get("status") != "VERIFIED":
        raise SystemExit("source corpus is not VERIFIED")
    if verification.get("sessions") != manifest["session_count"]:
        raise SystemExit("source verification session count mismatch")
    verified_messages = verification.get("messages")
    if not isinstance(verified_messages, int) or verified_messages < manifest["message_count"]:
        raise SystemExit("source verification message count mismatch")

    database_url = os.environ.get(args.database_url_env, "")
    result = load_building_generation(
        corpus,
        provider=args.provider,
        shard_id=args.shard_id,
        database_url=database_url,
        batch_size=args.batch_size,
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
