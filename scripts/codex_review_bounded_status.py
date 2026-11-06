#!/usr/bin/env python3
"""Run a status helper with a deadline that also terminates its children."""

import os
import signal
import subprocess
import sys


def main() -> int:
    if len(sys.argv) < 4:
        print("usage: bounded-status <seconds> <status-command> [args...]", file=sys.stderr)
        return 2
    try:
        seconds = int(sys.argv[1])
        if seconds <= 0:
            raise ValueError("deadline must be positive")
    except ValueError:
        print("bounded-status: invalid deadline", file=sys.stderr)
        return 2
    try:
        proc = subprocess.Popen(
            sys.argv[2:],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as error:
        print(f"bounded-status: cannot start helper ({type(error).__name__})", file=sys.stderr)
        return 2

    try:
        output, _ = proc.communicate(timeout=seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # Child may have exited between timeout and cancellation.
        proc.communicate()
        return 124

    sys.stdout.buffer.write(output)
    return proc.returncode if proc.returncode >= 0 else 128 - proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
