#!/usr/bin/env python3
from __future__ import annotations

import json
import sys


PROTECTED_PREFIXES = (
    '.github/workflows/',
    'tools/pr/',
    'scripts/codex_review_',
    'scripts/verify_',
    'scripts/arcade_',
    'functions/router/',
    'functions/shard/',
)

PROTECTED_EXACT = {
    'scripts/review_gate_required.py',
    'scripts/materialize_neon_shard.py',
    'scripts/prepare_refresh.py',
    'tools/dev/check',
    'docs/qa.md',
    'functions/candidate-contract.mjs',
    'session_search/candidate_contract.py',
    'session_search/corpus_store.py',
    'session_search/reconciliation.py',
    'session_search/neon_materializer.py',
    'session_search/refresh_plan.py',
}


def _normalize(path: str) -> str:
    return str(path).strip().replace('\\', '/')


def _reason(path: str) -> str | None:
    if path in PROTECTED_EXACT:
        return 'protected-exact'
    for prefix in PROTECTED_PREFIXES:
        if path.startswith(prefix):
            return f'protected-prefix:{prefix}'
    return None


def classify_paths(paths: list[str]) -> dict[str, object]:
    matches = []
    seen = set()
    for raw in paths:
        path = _normalize(raw)
        if not path or path in seen:
            continue
        seen.add(path)
        reason = _reason(path)
        if reason is not None:
            matches.append({'path': path, 'reason': reason})
    return {'required': bool(matches), 'matches': matches}


def main() -> int:
    paths = [line.rstrip('\n') for line in sys.stdin]
    print(json.dumps(classify_paths(paths), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
