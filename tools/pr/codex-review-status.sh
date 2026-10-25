#!/bin/sh
set -eu

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
    printf '%s\n' 'codex-review-status: not inside a Git worktree' >&2
    exit 2
}
cd "$ROOT"
PR=${1:-}
EXPECTED_HEAD=${2:-}
if [ -z "$PR" ]; then
    printf '%s\n' 'usage: tools/pr/codex-review-status.sh <pr-number> [expected-head]' >&2
    exit 2
fi
GH_BIN=${GH_BIN:-gh}
REPO=$($GH_BIN repo view --json nameWithOwner --jq .nameWithOwner)
OBSERVED_HEAD=$($GH_BIN pr view "$PR" --repo "$REPO" --json headRefOid --jq .headRefOid)
if [ -n "$EXPECTED_HEAD" ] && [ "$OBSERVED_HEAD" != "$EXPECTED_HEAD" ]; then
    PYTHONPATH="$ROOT" python3 -c '
import json, sys
from scripts.codex_review_status import classify_pr_head_currentness
print(json.dumps(classify_pr_head_currentness(sys.argv[1], sys.argv[2]), sort_keys=True))
' "$OBSERVED_HEAD" "$EXPECTED_HEAD"
    exit 0
fi
HEAD=${EXPECTED_HEAD:-$OBSERVED_HEAD}
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT HUP INT TERM
$GH_BIN api --paginate --slurp "/repos/$REPO/issues/$PR/comments?per_page=100" > "$TMP"
PYTHONPATH="$ROOT" python3 scripts/codex_review_status.py --head "$HEAD" --input "$TMP"
