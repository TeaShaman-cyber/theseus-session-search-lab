#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import tempfile
import zipfile

RUNTIME_SCHEMA = "theseus.session-search-portable-runtime.v1"
RECEIPT_SCHEMA = "theseus.session-search-portable-runtime-receipt.v1"
ZIP_NAME = "session-search-runtime.zip"
RECEIPT_NAME = "session-search-runtime.receipt.json"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
ENTRYPOINTS = [
    "python3 -m session_search.corpus",
    "python3 -m session_search.search",
    "python3 -m session_search.deepseek_export",
    "python3 -m session_search.xai_export",
    "python3 -m session_search.speed_booster_export",
    "python3 -m session_search.chatgpt_export",
    "python3 -m session_search.barn_recovery",
    "python3 -m session_search.handoff",
    "python3 -m session_search.refresh_readiness",
]


def _stable_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zip_info(path: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    info.create_system = 3
    return info


def _runtime_members(source_root: pathlib.Path) -> list[tuple[str, bytes]]:
    package = source_root / "session_search"
    note = source_root / "docs" / "portable-runtime.md"
    if not package.is_dir():
        raise ValueError("missing runtime package: session_search")
    if not note.is_file():
        raise ValueError("missing runtime contract file: docs/portable-runtime.md")

    members: list[tuple[str, bytes]] = []
    for path in sorted(package.glob("*.py"), key=lambda p: p.name):
        if path.is_file():
            members.append((f"session_search/{path.name}", path.read_bytes()))
    if not any(path == "session_search/__init__.py" for path, _ in members):
        raise ValueError("missing runtime package initializer: session_search/__init__.py")
    members.append(("PORTABLE_RUNTIME.md", note.read_bytes()))
    return sorted(members, key=lambda item: item[0])


def build_runtime(
    source_root: pathlib.Path,
    source_repo: str,
    source_revision: str,
    out_dir: pathlib.Path,
) -> tuple[pathlib.Path, pathlib.Path]:
    source_root = pathlib.Path(source_root).resolve()
    out_dir = pathlib.Path(out_dir).resolve()
    if not source_repo or "/" not in source_repo:
        raise ValueError("source repo must be OWNER/REPO")
    if not re.fullmatch(r"[0-9a-f]{40}", source_revision):
        raise ValueError("source revision must be a 40-character lowercase Git SHA")

    payload = _runtime_members(source_root)
    packaged_paths = {path for path, _data in payload}
    for entrypoint in ENTRYPOINTS:
        prefix = "python3 -m "
        if not entrypoint.startswith(prefix):
            raise ValueError(f"unsupported runtime entrypoint declaration: {entrypoint}")
        module = entrypoint[len(prefix):]
        module_path = module.replace(".", "/") + ".py"
        if module_path not in packaged_paths:
            raise ValueError(f"runtime entrypoint module missing from package: {module}")
    manifest = {
        "schema": RUNTIME_SCHEMA,
        "source": {"repo": source_repo, "revision": source_revision},
        "python_min": "3.11",
        "dependencies": {"python": "stdlib-only"},
        "entrypoints": ENTRYPOINTS,
        "members": [
            {"path": path, "sha256": _sha256(data), "size_bytes": len(data)}
            for path, data in payload
        ],
    }
    manifest_raw = _stable_json_bytes(manifest)
    all_members = payload + [("runtime-manifest.json", manifest_raw)]
    all_members.sort(key=lambda item: item[0])

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / ZIP_NAME
    receipt_path = out_dir / RECEIPT_NAME
    fd, tmp_name = tempfile.mkstemp(prefix=".runtime-", suffix=".zip", dir=out_dir)
    os.close(fd)
    tmp_path = pathlib.Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for path, data in all_members:
                zf.writestr(_zip_info(path), data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        os.replace(tmp_path, zip_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    zip_raw = zip_path.read_bytes()
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "source": {"repo": source_repo, "revision": source_revision},
        "manifest_sha256": _sha256(manifest_raw),
        "artifact": {
            "name": ZIP_NAME,
            "sha256": _sha256(zip_raw),
            "size_bytes": len(zip_raw),
        },
    }
    receipt_raw = _stable_json_bytes(receipt)
    fd, tmp_name = tempfile.mkstemp(prefix=".runtime-receipt-", suffix=".json", dir=out_dir)
    os.close(fd)
    tmp_path = pathlib.Path(tmp_name)
    try:
        tmp_path.write_bytes(receipt_raw)
        os.replace(tmp_path, receipt_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return zip_path, receipt_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build deterministic standalone Session Search runtime ZIP.")
    parser.add_argument("--source-root", type=pathlib.Path, required=True)
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--out-dir", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    try:
        zip_path, receipt_path = build_runtime(
            args.source_root,
            args.source_repo,
            args.source_revision,
            args.out_dir,
        )
    except Exception as exc:
        parser.exit(1, f"portable runtime build failed: {exc}\n")
    print(json.dumps({"runtime": str(zip_path), "receipt": str(receipt_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
