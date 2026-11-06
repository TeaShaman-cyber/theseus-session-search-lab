#!/usr/bin/env python3
"""Fail on invalid tracked shell scripts, including extensionless entrypoints."""

from pathlib import Path
import shlex
import subprocess
import sys


def shell_interpreter(path: Path) -> str | None:
    with path.open("rb") as source:
        line = source.readline(256)
    if not line.startswith(b"#!"):
        return None
    try:
        parts = shlex.split(line[2:].decode("utf-8").strip())
    except (ValueError, UnicodeDecodeError):
        return None
    if not parts:
        return None
    name = Path(parts[0]).name
    if name == "env":
        # For ordinary env sh / env bash and env -S sh / env -S bash.
        names = [part for part in parts[1:] if part in {"sh", "bash"}]
        name = names[0] if names else ""
    return name if name in {"sh", "bash"} else None


def main() -> int:
    root = Path(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], text=True
        ).strip()
    )
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).split(b"\0")
    scripts = []
    errors = []
    for raw in tracked:
        if not raw:
            continue
        relative = Path(raw.decode("utf-8"))
        absolute = root / relative
        if not absolute.is_file():
            continue
        # Reject tests that have no usable extension: suffix-based runners would skip them.
        if relative.parts[0] == "tests" and not relative.suffix and not relative.name.startswith("."):
            errors.append(f"UNCLASSIFIED_EXTENSIONLESS_TEST: {relative}")
        interpreter = shell_interpreter(absolute)
        if interpreter:
            scripts.append(str(relative))
            check = subprocess.run([interpreter, "-n", str(absolute)], cwd=root, capture_output=True, text=True)
            if check.returncode != 0:
                errors.append(f"SHELL_SYNTAX_FAILED: {relative}: {check.stderr.strip()}")
    for message in errors:
        print(message, file=sys.stderr)
    print(f"SHELL_SYNTAX_CHECKED={len(scripts)}")
    if not scripts:
        print("SHELL_SYNTAX_NO_SCRIPTS_DISCOVERED", file=sys.stderr)
        return 1
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
