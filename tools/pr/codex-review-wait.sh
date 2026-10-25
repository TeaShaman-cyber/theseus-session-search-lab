#!/bin/sh
set -eu

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
    printf '%s\n' 'codex-review-wait: not inside a Git worktree' >&2
    exit 2
}
cd "$ROOT"
PR=${1:-}
EXPECTED_HEAD=${2:-}
TIMEOUT=${3:-120}
POLL=${4:-10}
if [ -z "$PR" ] || [ -z "$EXPECTED_HEAD" ]; then
    printf '%s\n' 'usage: tools/pr/codex-review-wait.sh <pr-number> <expected-head> [timeout-seconds] [poll-seconds]' >&2
    exit 2
fi
case "$TIMEOUT:$POLL" in
    *[!0-9:]*|:*|*:0) printf '%s\n' 'timeout must be >=0 and poll must be >0 integer seconds' >&2; exit 2 ;;
esac

START=$(date +%s)
LAST_STATE=
while :; do
    NOW=$(date +%s)
    ELAPSED=$((NOW - START))
    if [ -n "$LAST_STATE" ] && [ "$ELAPSED" -ge "$TIMEOUT" ]; then
        DECISION=$(printf '%s\n' "$LAST_STATE" | PYTHONPATH="$ROOT" python3 -c '
import json, sys
from scripts.codex_review_status import classify_codex_wait
state = json.load(sys.stdin)
print(json.dumps(classify_codex_wait(state, elapsed_seconds=int(sys.argv[1]), timeout_seconds=int(sys.argv[2])), sort_keys=True))
' "$ELAPSED" "$TIMEOUT")
        printf '%s\n' "$DECISION"
        exit 3
    fi

    STATE=$("$ROOT/tools/pr/codex-review-status.sh" "$PR" "$EXPECTED_HEAD")
    NOW=$(date +%s)
    ELAPSED=$((NOW - START))
    DECISION=$(printf '%s\n' "$STATE" | PYTHONPATH="$ROOT" python3 -c '
import json, sys
from scripts.codex_review_status import classify_codex_wait
state = json.load(sys.stdin)
print(json.dumps(classify_codex_wait(state, elapsed_seconds=int(sys.argv[1]), timeout_seconds=int(sys.argv[2])), sort_keys=True))
' "$ELAPSED" "$TIMEOUT")
    DISPOSITION=$(printf '%s\n' "$DECISION" | python3 -c 'import json,sys; print(json.load(sys.stdin)["wait_disposition"])')
    case "$DISPOSITION" in
        TERMINAL) printf '%s\n' "$DECISION"; exit 0 ;;
        TIMEOUT) printf '%s\n' "$DECISION"; exit 3 ;;
        WAIT)
            LAST_STATE=$STATE
            SLEEP_SECONDS=$(PYTHONPATH="$ROOT" python3 -c '
import sys
from scripts.codex_review_status import bounded_poll_sleep_seconds
print(bounded_poll_sleep_seconds(elapsed_seconds=int(sys.argv[1]), timeout_seconds=int(sys.argv[2]), poll_seconds=int(sys.argv[3])))
' "$ELAPSED" "$TIMEOUT" "$POLL")
            [ "$SLEEP_SECONDS" -gt 0 ] || { printf '%s\n' "$DECISION"; exit 3; }
            sleep "$SLEEP_SECONDS"
            ;;
        *) printf '%s\n' "codex-review-wait: invalid disposition $DISPOSITION" >&2; exit 2 ;;
    esac
done
