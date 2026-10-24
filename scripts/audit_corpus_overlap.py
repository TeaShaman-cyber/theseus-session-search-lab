#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from session_search.artifact_overlap import (  # noqa: E402
    audit_provider_sets,
    load_accepted_ledger,
    load_frozen_provider_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit immutable accepted-artifact overlap across corpus surfaces."
    )
    parser.add_argument("--authority-corpus", required=True, type=pathlib.Path)
    parser.add_argument("--candidate-manifest", required=True, type=pathlib.Path)
    args = parser.parse_args()

    authority = load_accepted_ledger(args.authority_corpus.resolve())
    provider_sets = load_frozen_provider_manifest(args.candidate_manifest.resolve())
    result = audit_provider_sets(authority, provider_sets)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 2 if any(item["status"] == "PARTIAL_OVERLAP" for item in result["providers"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
