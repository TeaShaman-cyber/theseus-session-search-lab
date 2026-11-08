#!/usr/bin/env python3
"""Run pinned Biome lint over tracked first-party JavaScript/MJS and JSON."""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIOME = ROOT / "functions" / "node_modules" / ".bin" / "biome"


def tracked_biome_paths() -> list[pathlib.Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.js", "*.mjs", "*.json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    paths: list[pathlib.Path] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        rel = pathlib.Path(raw.decode("utf-8"))
        if rel.parts[:2] == ("functions", "dist"):
            continue
        if "node_modules" in rel.parts:
            continue
        paths.append(ROOT / rel)
    return paths


def lint_paths(paths: list[pathlib.Path]) -> subprocess.CompletedProcess[str]:
    if not BIOME.exists():
        raise RuntimeError(
            "Biome is missing; run: npm ci --prefix functions --ignore-scripts"
        )
    if not paths:
        raise RuntimeError("JavaScript lint gate: no source files supplied")

    return subprocess.run(
        [
            str(BIOME),
            "lint",
            "--config-path",
            str(ROOT / "biome.json"),
            "--diagnostic-level=error",
            *[str(path) for path in paths],
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


def main() -> int:
    paths = tracked_biome_paths()
    result = lint_paths(paths)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode != 0:
        return result.returncode
    print(f"BIOME_LINT_PASS files={len(paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
