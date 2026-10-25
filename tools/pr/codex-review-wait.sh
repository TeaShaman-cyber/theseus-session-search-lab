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

STATUS_CMD=${CODEX_REVIEW_STATUS_CMD:-$ROOT/tools/pr/codex-review-status.sh}
command -v timeout >/dev/null 2>&1 || {
    printf '%s\n' 'codex-review-wait: GNU timeout is required' >&2
    exit 2
}

START=$(date +%s)
LAST_STATE=
while :; do
    NOW=$(date +%s)
    ELAPSED=$((NOW - START))
    if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
        if [ -n "$LAST_STATE" ]; then
            DECISION=$(printf '%s\n' "$LAST_STATE" | PYTHONPATH="$ROOT" python3 -c '
import json, sys
from scripts.codex_review_status import classify_codex_wait
state = json.load(sys.stdin)
print(json.dumps(classify_codex_wait(state, elapsed_seconds=int(sys.argv[1]), timeout_seconds=int(sys.argv[2])), sort_keys=True))
' "$ELAPSED" "$TIMEOUT")
        else
            DECISION=$(PYTHONPATH="$ROOT" python3 -c '
import json, sys
from scripts.codex_review_status import status_request_timeout_result
print(json.dumps(status_request_timeout_result(sys.argv[1], elapsed_seconds=int(sys.argv[2]), timeout_seconds=int(sys.argv[3])), sort_keys=True))
' "$EXPECTED_HEAD" "$ELAPSED" "$TIMEOUT")
        fi
        printf '%s\n' "$DECISION"
        exit 3
    fi

    REMAINING=$((TIMEOUT - ELAPSED))
    set +e
    STATE=$(timeout --signal=KILL "$REMAINING" "$STATUS_CMD" "$PR" "$EXPECTED_HEAD" 2>&1)
    STATUS_RC=$?
    set -e
    if [ "$STATUS_RC" -ne 0 ]; then
        NOW=$(date +%s)
        ELAPSED=$((NOW - START))
        case "$STATUS_RC" in
            124|137)
                DECISION=$(PYTHONPATH="$ROOT" python3 -c '
import json, sys
from scripts.codex_review_status import status_request_timeout_result
print(json.dumps(status_request_timeout_result(sys.argv[1], elapsed_seconds=int(sys.argv[2]), timeout_seconds=int(sys.argv[3])), sort_keys=True))
' "$EXPECTED_HEAD" "$ELAPSED" "$TIMEOUT")
                printf '%s\n' "$DECISION"
                exit 3
                ;;
            *)
                printf '%s\n' "codex-review-wait: status helper failed rc=$STATUS_RC" >&2
                printf '%s\n' "$STATE" >&2
                exit 2
                ;;
        esac
    fi
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
