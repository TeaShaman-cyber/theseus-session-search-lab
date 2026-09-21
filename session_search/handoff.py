from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import uuid
from datetime import datetime, timezone

from .artifact import file_sha256
from .corpus_store import CorpusPaths, ingest_artifact, resolve_corpus_root

HANDOFF_RECEIPT_SCHEMA = "theseus.session-search-handoff-receipt.v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _write_receipt(paths: CorpusPaths, receipt: dict) -> pathlib.Path:
    directory = paths.root / "receipts" / "handoff"
    directory.mkdir(parents=True, exist_ok=True)
    source_sha = str(receipt["source_sha256"])
    target = directory / f"{source_sha}-{uuid.uuid4().hex}.json"
    temp = paths.staging / f"handoff-{uuid.uuid4().hex}.tmp"
    paths.ensure_layout()
    try:
        with temp.open("xb") as fh:
            fh.write(_stable_json_bytes(receipt))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp, target)
        return target
    finally:
        if temp.exists():
            temp.unlink()


def run_handoff(inbox: pathlib.Path, corpus_root: pathlib.Path) -> dict:
    inbox = pathlib.Path(inbox)
    if not inbox.is_dir():
        raise ValueError(f"HANDOFF_INBOX_MISSING: {inbox}")

    paths = CorpusPaths.from_root(pathlib.Path(corpus_root))
    paths.ensure_layout()
    candidates = sorted(
        (path for path in inbox.iterdir() if path.is_file() and path.suffix.lower() == ".zip"),
        key=lambda path: path.name,
    )
    results: list[dict] = []
    for source in candidates:
        source_sha = file_sha256(source)
        try:
            ingest = ingest_artifact(source, paths.root)
        except Exception as exc:
            receipt = {
                "schema": HANDOFF_RECEIPT_SCHEMA,
                "recorded_at": _utc_now(),
                "source": str(source),
                "source_sha256": source_sha,
                "status": "FAILED",
                "error_class": type(exc).__name__,
                "error": str(exc),
            }
            receipt_path = _write_receipt(paths, receipt)
            results.append({
                "source": str(source),
                "source_sha256": source_sha,
                "status": "FAILED",
                "error_class": type(exc).__name__,
                "error": str(exc),
                "receipt": str(receipt_path),
            })
            continue

        status = str(ingest.get("status") or "UNKNOWN")
        receipt = {
            "schema": HANDOFF_RECEIPT_SCHEMA,
            "recorded_at": _utc_now(),
            "source": str(source),
            "source_sha256": source_sha,
            "status": status,
            "ingest": ingest,
        }
        receipt_path = _write_receipt(paths, receipt)
        results.append({
            "source": str(source),
            "source_sha256": source_sha,
            "status": status,
            "receipt": str(receipt_path),
            "ingest": ingest,
        })

    return {
        "status": "DEGRADED" if any(row["status"] == "FAILED" for row in results) else "COMPLETE",
        "scanned": len(candidates),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest portable Session Search artifacts from a source-agnostic inbox once.")
    parser.add_argument("--inbox", type=pathlib.Path, required=True)
    parser.add_argument("--corpus")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        corpus_root = resolve_corpus_root(args.corpus)
        result = run_handoff(args.inbox, corpus_root)
    except Exception as exc:
        print(f"HANDOFF FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(f"HANDOFF {result['status']} scanned={result['scanned']}")
        for row in result["results"]:
            print(f"{row['status']} {row['source']}")
    return 0 if result["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
