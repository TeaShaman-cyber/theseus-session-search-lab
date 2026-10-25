#!/bin/sh
set -eu

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
    printf '%s\n' 'codex-review-wait: not inside a Git worktree' >&2
    exit 2
}
cd "$ROOT"
PR=${1:-}
TIMEOUT=${2:-120}
POLL=${3:-10}
if [ -z "$PR" ]; then
    printf '%s\n' 'usage: tools/pr/codex-review-wait.sh <pr-number> [timeout-seconds] [poll-seconds]' >&2
    exit 2
fi
case "$TIMEOUT:$POLL" in
    *[!0-9:]*|:*|*:0) printf '%s\n' 'timeout must be >=0 and poll must be >0 integer seconds' >&2; exit 2 ;;
esac

START=$(date +%s)
while :; do
    STATE=$("$ROOT/tools/pr/codex-review-status.sh" "$PR")
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
        WAIT) sleep "$POLL" ;;
        *) printf '%s\n' "codex-review-wait: invalid disposition $DISPOSITION" >&2; exit 2 ;;
    esac
done
