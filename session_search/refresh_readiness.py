from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .artifact import file_sha256
from .corpus_store import CorpusPaths

READINESS_SCHEMA = "theseus.session-search-refresh-readiness.v1"
UNKNOWN_SOURCE_CURRENTNESS = "UNKNOWN_WITHOUT_SOURCE_WATERMARK"


def _accepted_hashes(paths: CorpusPaths) -> set[str]:
    directory = paths.accepted_ledger
    if not directory.is_dir():
        return set()
    result: set[str] = set()
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"ACCEPTED_LEDGER_UNREADABLE: {path}: {exc}") from exc
        sha = payload.get("artifact_sha256")
        if not isinstance(sha, str) or len(sha) != 64 or path.name != f"{sha}.json":
            raise ValueError(f"ACCEPTED_LEDGER_ENTRY_INVALID: {path}")
        result.add(sha)
    return result


def inspect_refresh_readiness(inbox: pathlib.Path, corpus_root: pathlib.Path) -> dict:
    inbox = pathlib.Path(inbox)
    if not inbox.is_dir():
        raise ValueError(f"HANDOFF_INBOX_MISSING: {inbox}")

    paths = CorpusPaths.from_root(pathlib.Path(corpus_root))
    accepted_hashes = _accepted_hashes(paths)
    zip_paths = sorted(
        (path for path in inbox.iterdir() if path.is_file() and path.suffix.lower() == ".zip"),
        key=lambda path: path.name,
    )

    pending: list[dict[str, str]] = []
    accepted: list[dict[str, str]] = []
    for path in zip_paths:
        sha = file_sha256(path)
        row = {"name": path.name, "path": str(path), "sha256": sha}
        if sha in accepted_hashes:
            accepted.append(row)
        else:
            pending.append(row)

    state = "NEW_ARTIFACTS_PENDING" if pending else "NO_NEW_ARTIFACTS"
    return {
        "schema": READINESS_SCHEMA,
        "state": state,
        "source_currentness": UNKNOWN_SOURCE_CURRENTNESS,
        "source_currentness_reason": "no authoritative live source watermark was supplied",
        "refresh_action": "RUN_HANDOFF" if pending else "NONE",
        "inbox": str(inbox),
        "corpus": str(paths.root),
        "zip_count": len(zip_paths),
        "pending_count": len(pending),
        "accepted_count": len(accepted),
        "pending": pending,
        "accepted": accepted,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect whether a Session Search inbox contains artifacts not yet accepted by a corpus."
    )
    parser.add_argument("--inbox", type=pathlib.Path, required=True)
    parser.add_argument("--corpus", type=pathlib.Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = inspect_refresh_readiness(args.inbox, args.corpus)
    except Exception as exc:
        print(f"REFRESH_READINESS FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"REFRESH_READINESS {result['state']} pending={result['pending_count']} "
            f"accepted={result['accepted_count']} source={result['source_currentness']}"
        )
        for row in result["pending"]:
            print(f"PENDING {row['name']} sha256={row['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
